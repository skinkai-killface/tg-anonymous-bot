# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import html
from aiogram import Router, types, Bot, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo

from config import ADMIN_CHAT_ID
from database import increment_stat, add_to_archive
from album import save_album

router = Router()
# Only process messages sent in private chat with the bot
router.message.filter(F.chat.type == "private")


def get_author_header(user: types.User) -> str:
    """Format author link and ID so it's both clickable and easily parsed."""
    name = html.escape(user.full_name or "Пользователь")
    user_tag = f", @{user.username}" if user.username else ""
    return f'👤 Автор: <a href="tg://user?id={user.id}">{name}</a> [ID: <code>{user.id}</code>{user_tag}]'


def moderation_keyboard(user_id: int, message_id: int) -> InlineKeyboardMarkup:
    """
    Builds an inline keyboard with Approve / Reject / Edit / Block buttons.
    Callback data encodes the action, user_id, and original message_id.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Разрешить",
                callback_data=f"approve:{user_id}:{message_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отказать",
                callback_data=f"reject:{user_id}:{message_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✏️ Изменить текст",
                callback_data=f"edit_text:{user_id}:{message_id}",
            ),
            InlineKeyboardButton(
                text="🚫 Заблокировать",
                callback_data=f"block:{user_id}:{message_id}",
            ),
        ],
    ])


@router.message(F.text)
async def handle_text(message: types.Message, bot: Bot):
    """Forward a text suggestion to the admin chat for moderation and archive."""
    author = get_author_header(message.from_user)

    sent = await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение</b>\n"
             f"{author}\n\n"
             f"{message.text}",
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="text",
        text_content=message.text,
        media_list=[],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твоё сообщение отправлено на модерацию!")


@router.message(F.sticker)
async def handle_sticker(message: types.Message, bot: Bot):
    """Forward a sticker suggestion to the admin chat for moderation and archive."""
    author = get_author_header(message.from_user)

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение (стикер)</b>\n{author}",
        parse_mode="HTML",
    )
    sent = await bot.send_sticker(
        chat_id=ADMIN_CHAT_ID,
        sticker=message.sticker.file_id,
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="sticker",
        text_content=message.sticker.emoji or "",
        media_list=[{"type": "sticker", "file_id": message.sticker.file_id}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твой стикер отправлен на модерацию!")


@router.message(F.media_group_id)
async def handle_album(message: types.Message, bot: Bot, album: list[types.Message] | None = None):
    """Forward a multi-media album (photos/videos) to admin chat and archive."""
    if not album:
        album = [message]

    first_msg = album[0]
    author = get_author_header(first_msg.from_user)
    user_caption = first_msg.caption or ""
    header = f"📩 <b>Новое предложение (альбом: {len(album)} шт.)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    media_list = []
    saved_items = []
    for idx, msg in enumerate(album):
        caption = full_caption if idx == 0 else None
        if msg.photo:
            file_id = msg.photo[-1].file_id
            media_list.append(InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML"))
            saved_items.append({"type": "photo", "file_id": file_id, "caption": user_caption if idx == 0 else ""})
        elif msg.video:
            file_id = msg.video.file_id
            media_list.append(InputMediaVideo(media=file_id, caption=caption, parse_mode="HTML"))
            saved_items.append({"type": "video", "file_id": file_id, "caption": user_caption if idx == 0 else ""})

    if media_list:
        sent_msgs = await bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=media_list)
        # Store album mapping by first_msg.message_id
        save_album(first_msg.message_id, saved_items)

        # Send control keyboard
        ctrl_msg = await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"⚖️ <b>Управление альбомом выше</b> ({len(album)} файлов):",
            reply_to_message_id=sent_msgs[0].message_id,
            parse_mode="HTML",
            reply_markup=moderation_keyboard(first_msg.from_user.id, first_msg.message_id),
        )
        await increment_stat("total_suggestions")
        await add_to_archive(
            user_id=first_msg.from_user.id,
            user_name=first_msg.from_user.full_name or "",
            user_handle=first_msg.from_user.username or "",
            content_type="album",
            text_content=user_caption,
            media_list=saved_items,
            orig_msg_id=first_msg.message_id,
            admin_msg_id=ctrl_msg.message_id,
        )
        await first_msg.answer("✅ Твой альбом отправлен на модерацию!")


@router.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot):
    """Forward a single photo suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (фото)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    photo = message.photo[-1]  # highest resolution

    sent = await bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=photo.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="photo",
        text_content=user_caption,
        media_list=[{"type": "photo", "file_id": photo.file_id, "caption": user_caption}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твой кадр отправлен на модерацию!")


@router.message(F.video)
async def handle_video(message: types.Message, bot: Bot):
    """Forward a single video suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (видео)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    sent = await bot.send_video(
        chat_id=ADMIN_CHAT_ID,
        video=message.video.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="video",
        text_content=user_caption,
        media_list=[{"type": "video", "file_id": message.video.file_id, "caption": user_caption}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твоё видео отправлено на модерацию!")


@router.message(F.animation)
async def handle_animation(message: types.Message, bot: Bot):
    """Forward a GIF animation suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (GIF)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    sent = await bot.send_animation(
        chat_id=ADMIN_CHAT_ID,
        animation=message.animation.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="animation",
        text_content=user_caption,
        media_list=[{"type": "animation", "file_id": message.animation.file_id}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твоя GIF отправлена на модерацию!")


@router.message(F.voice)
async def handle_voice(message: types.Message, bot: Bot):
    """Forward a voice message suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (голосовое)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    sent = await bot.send_voice(
        chat_id=ADMIN_CHAT_ID,
        voice=message.voice.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="voice",
        text_content=user_caption,
        media_list=[{"type": "voice", "file_id": message.voice.file_id}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твоё голосовое сообщение отправлено на модерацию!")


@router.message(F.video_note)
async def handle_video_note(message: types.Message, bot: Bot):
    """Forward a video note (circle) suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение (кружочек)</b>\n{author}",
        parse_mode="HTML",
    )
    sent = await bot.send_video_note(
        chat_id=ADMIN_CHAT_ID,
        video_note=message.video_note.file_id,
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="video_note",
        text_content="",
        media_list=[{"type": "video_note", "file_id": message.video_note.file_id}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твой кружочек отправлен на модерацию!")


@router.message(F.audio)
async def handle_audio(message: types.Message, bot: Bot):
    """Forward an audio suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (аудио)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    sent = await bot.send_audio(
        chat_id=ADMIN_CHAT_ID,
        audio=message.audio.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="audio",
        text_content=user_caption,
        media_list=[{"type": "audio", "file_id": message.audio.file_id}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твоё аудио отправлено на модерацию!")


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot):
    """Forward a document suggestion to admin chat and archive."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (документ)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    sent = await bot.send_document(
        chat_id=ADMIN_CHAT_ID,
        document=message.document.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="document",
        text_content=user_caption,
        media_list=[{"type": "document", "file_id": message.document.file_id, "file_name": message.document.file_name}],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer("✅ Твой документ отправлен на модерацию!")
