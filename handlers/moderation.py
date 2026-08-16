# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import asyncio
import logging
from aiogram import Router, types, Bot, F
from aiogram.filters import Command
from aiogram.types import InputMediaPhoto, InputMediaVideo

from config import CHANNEL_ID, ADMIN_CHAT_ID
from database import (
    block_user,
    increment_stat,
    record_moderation,
    update_archive_status,
    update_archive_text,
)
from album import pop_album, get_album

router = Router()
logger = logging.getLogger(__name__)

CHANNEL_HEADER = "📩 <b>Новое анонимное сообщение:</b>"

# ── Anti-Double-Click protection ──
_processing_lock = asyncio.Lock()
_processing_messages: set[int] = set()


def strip_header(text: str) -> str:
    """
    Strips the moderation header (author info, ID, 'Новое предложение', etc.)
    and returns ONLY the user's actual text/caption.
    If the user sent media without text, returns empty string "".
    """
    if not text:
        return ""

    # Split by double newline first
    if "\n\n" in text:
        parts = text.split("\n\n", 1)
        user_content = parts[1].strip()
        if user_content.startswith("✅ Одобрено") or user_content.startswith("❌ Отклонено"):
            return ""
        return user_content

    # If no \n\n, check if the whole text is only the moderation header
    lines = text.splitlines()
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if (
            "Новое предложение" in stripped
            or "Автор:" in stripped
            or "ID:" in stripped
            or "Одобрено" in stripped
            or "Отклонено" in stripped
            or "Заблокирован" in stripped
            or not stripped
        ):
            continue
        filtered_lines.append(line)

    return "\n".join(filtered_lines).strip()


async def mark_moderation_message(source_msg: types.Message, status_text: str):
    """Safely edit or reply to the moderation message after admin action."""
    try:
        if source_msg.text:
            await source_msg.edit_text(
                source_msg.text + f"\n\n{status_text}",
                parse_mode="HTML",
            )
        elif source_msg.caption is not None:
            await source_msg.edit_caption(
                caption=(source_msg.caption or "") + f"\n\n{status_text}",
                parse_mode="HTML",
            )
        else:
            # For stickers / video_notes / albums where caption editing is not supported
            await source_msg.edit_reply_markup(reply_markup=None)
            await source_msg.reply(status_text, parse_mode="HTML")
    except Exception:
        pass


async def _acquire_message(message_id: int) -> bool:
    """Try to acquire exclusive processing for a moderation message. Returns False if already taken."""
    async with _processing_lock:
        if message_id in _processing_messages:
            return False
        _processing_messages.add(message_id)
        return True


def _release_message(message_id: int) -> None:
    """Release the processing lock after done."""
    _processing_messages.discard(message_id)


# ── Edit State Tracker ──
# admin_user_id -> {"target_msg_id": int, "keyboard": InlineKeyboardMarkup, "instruction_msg_id": int}
_active_editing: dict[int, dict] = {}
# instruction_message_id -> target_msg_id
_edit_instruction_map: dict[int, int] = {}


@router.callback_query(F.data.startswith("edit_text:"))
async def on_edit_text_button(callback: types.CallbackQuery, bot: Bot):
    """
    Admin clicked 'Edit text' button.
    Activate edit state for this admin and prompt for new text.
    """
    await callback.answer("Режим редактирования включен ✏️")
    source_msg = callback.message
    admin_id = callback.from_user.id

    instruction = await source_msg.reply(
        "✏️ <b>Режим редактирования:</b>\n"
        "Отправьте новый текст следующим сообщением (или сделайте Reply на предложку):\n\n"
        "<i>Нажмите /cancel чтобы отменить.</i>",
        parse_mode="HTML",
    )

    _active_editing[admin_id] = {
        "target_msg_id": source_msg.message_id,
        "keyboard": source_msg.reply_markup,
        "instruction_msg_id": instruction.message_id,
    }
    _edit_instruction_map[instruction.message_id] = source_msg.message_id


@router.message(Command("cancel"))
async def cmd_cancel_edit(message: types.Message):
    """Cancel active editing session."""
    if message.chat.id != ADMIN_CHAT_ID:
        return
    admin_id = message.from_user.id
    if admin_id in _active_editing:
        _active_editing.pop(admin_id, None)
        await message.reply("❌ Редактирование отменено.")


@router.message(Command("edit"))
async def cmd_edit_suggestion(message: types.Message, bot: Bot):
    """
    /edit <new text> — edit the text or caption of a suggestion in admin chat.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    if not message.reply_to_message:
        await message.reply("✏️ Ответьте (Reply) на предложку командой <code>/edit Новый текст</code>", parse_mode="HTML")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply("✏️ Использование: <code>/edit Новый текст</code>", parse_mode="HTML")
        return

    new_text = args[1].strip()
    replied = message.reply_to_message

    target_id = _edit_instruction_map.get(replied.message_id) or replied.message_id
    await _apply_edit_by_id(target_id, new_text, message, bot)


@router.message(F.chat.id == ADMIN_CHAT_ID, ~F.text.startswith("/"))
async def on_admin_message_edit_check(message: types.Message, bot: Bot):
    """
    Catches text sent while admin is in active editing mode,
    OR sent as reply to an edit instruction / suggestion card.
    """
    if not message.text:
        return

    admin_id = message.from_user.id if message.from_user else 0
    replied = message.reply_to_message

    target_id = None
    instruction_id = None

    # Case 1: Admin has active edit session
    if admin_id in _active_editing:
        sess = _active_editing.pop(admin_id)
        target_id = sess["target_msg_id"]
        instruction_id = sess.get("instruction_msg_id")

    # Case 2: Message is a reply to an edit instruction
    elif replied and replied.message_id in _edit_instruction_map:
        target_id = _edit_instruction_map.pop(replied.message_id)
        instruction_id = replied.message_id

    # Case 3: Message is a direct reply to a suggestion card with moderation keyboard
    elif replied and replied.reply_markup and replied.reply_markup.inline_keyboard:
        # Check if replied message has approve/reject buttons
        has_mod_buttons = any(
            btn.callback_data and btn.callback_data.startswith(("approve:", "reject:", "edit_text:"))
            for row in replied.reply_markup.inline_keyboard
            for btn in row
        )
        if has_mod_buttons:
            target_id = replied.message_id

    if not target_id:
        return  # Let other handlers (like admin_reply_to_user) handle it

    new_text = message.text.strip()
    await _apply_edit_by_id(target_id, new_text, message, bot, instruction_id)


async def _apply_edit_by_id(
    target_msg_id: int,
    new_text: str,
    trigger_message: types.Message,
    bot: Bot,
    instruction_msg_id: int | None = None,
):
    """Update text/caption of target suggestion message and keep moderation keyboard."""
    try:
        # Fetch or construct keyboard
        # Try editing as text message first
        edited = False
        try:
            # We first try to get the message or edit directly
            lines = []
            author_header = ""

            # Try editing caption (photo, video, etc.)
            try:
                await bot.edit_message_caption(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=target_msg_id,
                    caption=f"{new_text}\n\n<i>[✏️ Подпись отредактирована]</i>",
                    parse_mode="HTML",
                    reply_markup=trigger_message.reply_to_message.reply_markup if trigger_message.reply_to_message and trigger_message.reply_to_message.message_id == target_msg_id else None,
                )
                edited = True
            except Exception:
                pass

            if not edited:
                # Try editing text
                await bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=target_msg_id,
                    text=f"{new_text}\n\n<i>[✏️ Текст отредактирован]</i>",
                    parse_mode="HTML",
                    reply_markup=trigger_message.reply_to_message.reply_markup if trigger_message.reply_to_message and trigger_message.reply_to_message.message_id == target_msg_id else None,
                )
                edited = True

        except Exception as e:
            logger.warning(f"Direct edit by ID attempt: {e}")

        await update_archive_text(new_text=new_text, admin_msg_id=target_msg_id)
        await trigger_message.reply("✅ <b>Текст предложки успешно обновлён!</b>", parse_mode="HTML")

        # Clean up instruction message if exists
        if instruction_msg_id:
            try:
                await bot.delete_message(chat_id=ADMIN_CHAT_ID, message_id=instruction_msg_id)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Failed to edit suggestion {target_msg_id}: {e}")
        await trigger_message.reply(f"❌ Ошибка при изменении: {e}")


@router.callback_query(F.data.startswith("approve:"))
async def on_approve(callback: types.CallbackQuery, bot: Bot):
    """
    Admin pressed "Approve". Publish the content to the channel with the anonymous header.
    """
    _, user_id_str, orig_msg_id_str = callback.data.split(":")
    user_id = int(user_id_str)
    orig_msg_id = int(orig_msg_id_str)
    source_msg = callback.message

    # Anti-Double-Click
    if not await _acquire_message(source_msg.message_id):
        await callback.answer("⚠️ Уже обрабатывается другим модератором.", show_alert=True)
        return

    try:
        # Check if this is an album
        album_items = pop_album(orig_msg_id)
        if album_items:
            album_caption = strip_header(source_msg.text or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{album_caption}" if album_caption else CHANNEL_HEADER
            media_list = []
            for idx, item in enumerate(album_items):
                cap = post_caption if idx == 0 else None
                if item["type"] == "photo":
                    media_list.append(InputMediaPhoto(media=item["file_id"], caption=cap, parse_mode="HTML"))
                elif item["type"] == "video":
                    media_list.append(InputMediaVideo(media=item["file_id"], caption=cap, parse_mode="HTML"))
            if media_list:
                await bot.send_media_group(chat_id=CHANNEL_ID, media=media_list)

        elif source_msg.text:
            content = strip_header(source_msg.text)
            post_text = f"{CHANNEL_HEADER}\n\n{content}" if content else CHANNEL_HEADER
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                parse_mode="HTML",
            )
        elif source_msg.sticker:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=CHANNEL_HEADER,
                parse_mode="HTML",
            )
            await bot.send_sticker(
                chat_id=CHANNEL_ID,
                sticker=source_msg.sticker.file_id,
            )
        elif source_msg.photo:
            caption = strip_header(source_msg.caption or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=source_msg.photo[-1].file_id,
                caption=post_caption,
                parse_mode="HTML",
            )
        elif source_msg.video:
            caption = strip_header(source_msg.caption or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
            await bot.send_video(
                chat_id=CHANNEL_ID,
                video=source_msg.video.file_id,
                caption=post_caption,
                parse_mode="HTML",
            )
        elif source_msg.animation:
            caption = strip_header(source_msg.caption or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
            await bot.send_animation(
                chat_id=CHANNEL_ID,
                animation=source_msg.animation.file_id,
                caption=post_caption,
                parse_mode="HTML",
            )
        elif source_msg.voice:
            caption = strip_header(source_msg.caption or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
            await bot.send_voice(
                chat_id=CHANNEL_ID,
                voice=source_msg.voice.file_id,
                caption=post_caption,
                parse_mode="HTML",
            )
        elif source_msg.video_note:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=CHANNEL_HEADER,
                parse_mode="HTML",
            )
            await bot.send_video_note(
                chat_id=CHANNEL_ID,
                video_note=source_msg.video_note.file_id,
            )
        elif source_msg.audio:
            caption = strip_header(source_msg.caption or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
            await bot.send_audio(
                chat_id=CHANNEL_ID,
                audio=source_msg.audio.file_id,
                caption=post_caption,
                parse_mode="HTML",
            )
        elif source_msg.document:
            caption = strip_header(source_msg.caption or "")
            post_caption = f"{CHANNEL_HEADER}\n\n{caption}" if caption else CHANNEL_HEADER
            await bot.send_document(
                chat_id=CHANNEL_ID,
                document=source_msg.document.file_id,
                caption=post_caption,
                parse_mode="HTML",
            )

        # Record stats & archive
        await increment_stat("approved")
        await record_moderation(callback.from_user.id, callback.from_user.full_name, "approved")
        await update_archive_status(
            orig_msg_id=orig_msg_id,
            status="approved",
            moderator_id=callback.from_user.id,
            moderator_name=callback.from_user.full_name,
        )

        # Update the admin message to show it was approved
        admin_name = callback.from_user.full_name
        await mark_moderation_message(source_msg, f"✅ <b>Одобрено</b> — {admin_name}")

        # Notify user
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🎉 Твоё сообщение было одобрено и опубликовано в канале!",
            )
        except Exception:
            pass

        await callback.answer("Опубликовано ✅")

    finally:
        _release_message(source_msg.message_id)


@router.callback_query(F.data.startswith("reject:"))
async def on_reject(callback: types.CallbackQuery, bot: Bot):
    """
    Admin pressed "Reject". Mark as rejected and notify the user.
    """
    _, user_id_str, orig_msg_id_str = callback.data.split(":")
    user_id = int(user_id_str)
    orig_msg_id = int(orig_msg_id_str)
    source_msg = callback.message

    if not await _acquire_message(source_msg.message_id):
        await callback.answer("⚠️ Уже обрабатывается другим модератором.", show_alert=True)
        return

    try:
        # Clean up album if was rejected
        pop_album(orig_msg_id)

        admin_name = callback.from_user.full_name
        await increment_stat("rejected")
        await record_moderation(callback.from_user.id, admin_name, "rejected")
        await update_archive_status(
            orig_msg_id=orig_msg_id,
            status="rejected",
            moderator_id=callback.from_user.id,
            moderator_name=admin_name,
        )
        await mark_moderation_message(source_msg, f"❌ <b>Отклонено</b> — {admin_name}")

        try:
            await bot.send_message(
                chat_id=user_id,
                text="😔 К сожалению, твоё сообщение было отклонено.",
            )
        except Exception:
            pass

        await callback.answer("Отклонено ❌")

    finally:
        _release_message(source_msg.message_id)


@router.callback_query(F.data.startswith("block:"))
async def on_block(callback: types.CallbackQuery, bot: Bot):
    """
    Admin pressed "Block". Block the user and reject the message.
    """
    _, user_id_str, orig_msg_id_str = callback.data.split(":")
    user_id = int(user_id_str)
    orig_msg_id = int(orig_msg_id_str)
    source_msg = callback.message

    if not await _acquire_message(source_msg.message_id):
        await callback.answer("⚠️ Уже обрабатывается другим модератором.", show_alert=True)
        return

    try:
        pop_album(orig_msg_id)

        admin_name = callback.from_user.full_name
        await block_user(user_id, reason=f"Blocked by {admin_name}")
        await increment_stat("blocked")
        await record_moderation(callback.from_user.id, admin_name, "blocked")
        await update_archive_status(
            orig_msg_id=orig_msg_id,
            status="blocked",
            moderator_id=callback.from_user.id,
            moderator_name=admin_name,
        )
        await mark_moderation_message(source_msg, f"🚫 <b>Заблокирован и отклонён</b> — {admin_name}")

        try:
            await bot.send_message(
                chat_id=user_id,
                text="⛔ Вы были заблокированы. Ваши предложения больше не принимаются.",
            )
        except Exception:
            pass

        await callback.answer("Пользователь заблокирован 🚫")

    finally:
        _release_message(source_msg.message_id)
