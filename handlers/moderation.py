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
from database import block_user, increment_stat, record_moderation
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
async def on_edit_text_button(callback: types.CallbackQuery):
    """Admin clicked 'Edit text' button — send instructions."""
    await callback.answer()
    # Reply to the suggestion message itself so the admin can reply to it
    target_msg = callback.message
    await target_msg.reply(
        "✏️ <b>Как изменить текст предложки:</b>\n"
        "Ответьте (Reply) на <b>сообщение с предложкой выше</b> с командой:\n"
        "<code>/edit Ваш новый текст или подпись</code>",
        parse_mode="HTML",
    )


@router.message(Command("edit"))
async def cmd_edit_suggestion(message: types.Message):
    """
    /edit <new text> — edit the text or caption of a suggestion in admin chat.
    Works when replying to the suggestion itself or to the bot's instruction message.
    """
    if message.chat.id != ADMIN_CHAT_ID:
        return

    if not message.reply_to_message:
        await message.reply(
            "Использование: ответьте на предложку командой <code>/edit Новый текст</code>",
            parse_mode="HTML",
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply(
            "Использование: ответьте на предложку командой <code>/edit Новый текст</code>",
            parse_mode="HTML",
        )
        return

    new_text = args[1].strip()

    # Find the actual suggestion message — traverse reply chain up
    target = message.reply_to_message

    # If the user replied to the bot's instruction message, follow its reply to the original
    if target.reply_to_message and target.from_user and target.from_user.is_bot:
        target = target.reply_to_message

    try:
        if target.text:
            # Keep original author header if present
            lines = target.text.split("\n\n", 1)
            header = lines[0] if len(lines) > 1 else target.text
            await target.edit_text(
                f"{header}\n\n{new_text}\n<i>[✏️ Текст отредактирован]</i>",
                parse_mode="HTML",
                reply_markup=target.reply_markup,
            )
            await message.reply("✅ Текст сообщения успешно обновлён!")
        elif target.caption is not None:
            lines = (target.caption or "").split("\n\n", 1)
            header = lines[0] if len(lines) > 1 else (target.caption or "")
            await target.edit_caption(
                caption=f"{header}\n\n{new_text}\n<i>[✏️ Подпись отредактирована]</i>",
                parse_mode="HTML",
                reply_markup=target.reply_markup,
            )
            await message.reply("✅ Подпись медиа успешно обновлена!")
        else:
            await message.reply("⚠️ Нельзя изменить текст у стикера или кружочка.")
    except Exception as e:
        await message.reply(f"❌ Ошибка при редактировании: {e}")


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

        # Record stats
        await increment_stat("approved")
        await record_moderation(callback.from_user.id, callback.from_user.full_name, "approved")

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
