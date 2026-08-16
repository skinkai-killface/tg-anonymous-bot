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


@router.callback_query(F.data.startswith("edit_text:"))
async def on_edit_text_button(callback: types.CallbackQuery, bot: Bot):
    """
    Admin clicked 'Edit text' button.
    Store the message_id and ask the admin to send new text.
    """
    await callback.answer()
    parts = callback.data.split(":")
    # parts: edit_text:user_id:orig_msg_id
    source_msg = callback.message

    # Save mapping: the reply instruction message -> the suggestion message
    instruction = await source_msg.reply(
        "✏️ <b>Отправьте новый текст/подпись ответом (Reply) на ЭТО сообщение:</b>",
        parse_mode="HTML",
    )
    # Store the suggestion message_id in a module-level dict so we can find it later
    _edit_targets[instruction.message_id] = source_msg.message_id


# Module-level mapping: instruction_message_id -> suggestion_message_id
_edit_targets: dict[int, int] = {}


@router.message(Command("edit"))
async def cmd_edit_suggestion(message: types.Message, bot: Bot):
    """
    /edit <new text> — edit the text or caption of a suggestion in admin chat.
    Must be a reply to the suggestion itself OR the bot's edit instruction.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    if not message.reply_to_message:
        await message.reply(
            "✏️ Ответьте (Reply) на предложку командой <code>/edit Новый текст</code>",
            parse_mode="HTML",
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply(
            "✏️ Использование: <code>/edit Новый текст</code>",
            parse_mode="HTML",
        )
        return

    new_text = args[1].strip()
    replied = message.reply_to_message

    # If replied to the bot's instruction, find the real suggestion
    target_msg_id = _edit_targets.get(replied.message_id)
    if target_msg_id:
        # Clean up mapping
        _edit_targets.pop(replied.message_id, None)

    await _apply_edit(message, replied, new_text, bot, target_msg_id)


@router.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message, ~F.text.startswith("/"))
async def on_edit_reply(message: types.Message, bot: Bot):
    """
    Handle plain text replies to the bot's edit instruction message.
    No need for /edit prefix — just type the new text as reply.
    """
    if not message.text:
        return

    replied = message.reply_to_message
    if not replied:
        return

    # Only activate if replying to a known edit instruction
    target_msg_id = _edit_targets.get(replied.message_id)
    if not target_msg_id:
        return

    _edit_targets.pop(replied.message_id, None)
    new_text = message.text.strip()
    if not new_text:
        return

    await _apply_edit(message, replied, new_text, bot, target_msg_id)


async def _apply_edit(
    message: types.Message,
    replied: types.Message,
    new_text: str,
    bot: Bot,
    target_msg_id: int | None = None,
):
    """Apply the text edit to the suggestion message."""
    try:
        if target_msg_id:
            # We know the exact suggestion message — edit it by ID
            try:
                # Try editing text message
                await bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=target_msg_id,
                    text=new_text + "\n<i>[✏️ Текст отредактирован]</i>",
                    parse_mode="HTML",
                )
                await update_archive_text(new_text=new_text, admin_msg_id=target_msg_id)
                await message.reply("✅ Текст успешно обновлён!")
                return
            except Exception:
                pass

            # Try editing caption (photo/video/etc)
            try:
                await bot.edit_message_caption(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=target_msg_id,
                    caption=new_text + "\n<i>[✏️ Подпись отредактирована]</i>",
                    parse_mode="HTML",
                )
                await update_archive_text(new_text=new_text, admin_msg_id=target_msg_id)
                await message.reply("✅ Подпись успешно обновлена!")
                return
            except Exception as e2:
                await message.reply(f"❌ Не удалось изменить: {e2}")
                return

        # Fallback: try to edit the replied message directly
        target = replied
        # If replied to bot's message, try to go up
        if target.from_user and target.from_user.is_bot and target.reply_to_message:
            target = target.reply_to_message

        if target.text:
            lines = target.text.split("\n\n", 1)
            header = lines[0] if len(lines) > 1 else ""
            full = f"{header}\n\n{new_text}\n<i>[✏️ Текст отредактирован]</i>" if header else f"{new_text}\n<i>[✏️ Текст отредактирован]</i>"
            await target.edit_text(full, parse_mode="HTML", reply_markup=target.reply_markup)
            await update_archive_text(new_text=new_text, admin_msg_id=target.message_id)
            await message.reply("✅ Текст обновлён!")
        elif target.caption is not None:
            lines = (target.caption or "").split("\n\n", 1)
            header = lines[0] if len(lines) > 1 else ""
            full = f"{header}\n\n{new_text}\n<i>[✏️ Подпись отредактирована]</i>" if header else f"{new_text}\n<i>[✏️ Подпись отредактирована]</i>"
            await target.edit_caption(caption=full, parse_mode="HTML", reply_markup=target.reply_markup)
            await update_archive_text(new_text=new_text, admin_msg_id=target.message_id)
            await message.reply("✅ Подпись обновлена!")
        else:
            await message.reply("⚠️ Нельзя изменить текст у этого типа сообщения.")
    except Exception as e:
        logger.error(f"Edit failed: {e}")
        await message.reply(f"❌ Ошибка: {e}")


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
