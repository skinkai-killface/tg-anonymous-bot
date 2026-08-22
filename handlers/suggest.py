# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import html
import logging
from aiogram import Router, types, Bot, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo

from config import ADMIN_CHAT_ID, CHANNEL_ID
from database import (
    increment_stat,
    add_to_archive,
    get_setting,
    set_post_anonymity,
    get_post_anonymity,
)
from album import save_album

logger = logging.getLogger(__name__)
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


def user_confirmation_keyboard(orig_msg_id: int, is_anon: bool) -> InlineKeyboardMarkup:
    """Keyboard sent to user after submission allowing them to toggle author attribution."""
    if is_anon:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Указать меня (@username) в посте",
                    callback_data=f"toggle_anon:non_anon:{orig_msg_id}",
                )
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎭 Опубликовать анонимно",
                    callback_data=f"toggle_anon:anon:{orig_msg_id}",
                )
            ]
        ])


async def check_channel_subscription(bot: Bot, user_id: int) -> tuple[bool, str]:
    """
    Check if user is subscribed to CHANNEL_ID if subcheck is enabled.
    Returns (is_subscribed, channel_link).
    """
    enabled = await get_setting("subcheck_enabled", "1")
    if enabled == "0":
        return True, ""

    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ("creator", "administrator", "member", "restricted"):
            return True, ""
    except Exception as e:
        logger.warning(f"Could not verify subscription for user {user_id}: {e}")
        return True, ""  # Fail open if bot cannot inspect channel

    # Fetch channel link
    channel_link = "https://t.me"
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        if chat.username:
            channel_link = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            channel_link = chat.invite_link
    except Exception:
        pass

    return False, channel_link


async def notify_sub_required(message: types.Message, channel_link: str) -> None:
    """Send subscription requirement prompt to the user."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_link)],
        [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub_cb")],
    ])
    await message.answer(
        "📢 <b>Чтобы отправлять предложения, вам необходимо подписаться на наш канал!</b>\n\n"
        "Подпишитесь по кнопке ниже и нажмите <b>«Проверить подписку»</b>:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "check_sub_cb")
async def on_check_subscription_callback(callback: types.CallbackQuery, bot: Bot):
    """User pressed 'Check subscription' button."""
    is_sub, link = await check_channel_subscription(bot, callback.from_user.id)
    if is_sub:
        await callback.answer("✅ Подписка подтверждена!", show_alert=True)
        try:
            await callback.message.edit_text(
                "✅ <b>Подписка подтверждена!</b>\n\n"
                "Теперь отправьте ваше предложение (текст, фото, видео, альбом и т.д.):",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await callback.answer("❌ Вы ещё не подписались на канал!", show_alert=True)


@router.callback_query(F.data.startswith("toggle_anon:"))
async def on_toggle_anon_callback(callback: types.CallbackQuery):
    """User clicked button to toggle anonymous / credited publication mode."""
    parts = callback.data.split(":")
    # parts: toggle_anon:anon/non_anon:orig_msg_id
    if len(parts) < 3:
        await callback.answer()
        return

    mode = parts[1]
    orig_msg_id = int(parts[2])

    if mode == "non_anon":
        await set_post_anonymity(orig_msg_id, is_anonymous=False)
        tag = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name
        await callback.answer(f"Авторство будет указано: {tag}")
        try:
            await callback.message.edit_text(
                f"✅ <b>Твоё предложение отправлено на модерацию!</b>\n\n"
                f"👤 Режим: <b>С указанием автора ({html.escape(tag)})</b>\n\n"
                f"<i>Хотите скрыть автора? Нажмите кнопку ниже:</i>",
                parse_mode="HTML",
                reply_markup=user_confirmation_keyboard(orig_msg_id, is_anon=False),
            )
        except Exception:
            pass

    else:
        await set_post_anonymity(orig_msg_id, is_anonymous=True)
        await callback.answer("Опубликуется анонимно 🎭")
        try:
            await callback.message.edit_text(
                "✅ <b>Твоё предложение отправлено на модерацию!</b>\n\n"
                "🎭 Режим: <b>Анонимно</b>\n\n"
                "<i>Хотите указать авторство? Нажмите кнопку ниже:</i>",
                parse_mode="HTML",
                reply_markup=user_confirmation_keyboard(orig_msg_id, is_anon=True),
            )
        except Exception:
            pass


@router.message(F.text)
async def handle_text(message: types.Message, bot: Bot):
    """Forward a text suggestion to the admin chat for moderation and archive."""
    # Skip commands
    if message.text.startswith("/"):
        return

    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    safe_text = html.escape(message.text)

    sent = await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение</b>\n"
             f"{author}\n\n"
             f"{safe_text}",
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await increment_stat("total_suggestions")
    await add_to_archive(
        user_id=message.from_user.id,
        user_name=message.from_user.full_name or "",
        user_handle=message.from_user.username or "",
        content_type="text",
        text_content=safe_text,
        media_list=[],
        orig_msg_id=message.message_id,
        admin_msg_id=sent.message_id,
    )
    await message.answer(
        "✅ <b>Твоё сообщение отправлено на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.sticker)
async def handle_sticker(message: types.Message, bot: Bot):
    """Forward a sticker suggestion to the admin chat for moderation and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

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
    await message.answer(
        "✅ <b>Твой стикер отправлен на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.media_group_id)
async def handle_album(message: types.Message, bot: Bot, album: list[types.Message] | None = None):
    """Forward a multi-media album (photos/videos) to admin chat and archive."""
    if not album:
        album = [message]

    first_msg = album[0]

    is_sub, link = await check_channel_subscription(bot, first_msg.from_user.id)
    if not is_sub:
        await notify_sub_required(first_msg, link)
        return

    author = get_author_header(first_msg.from_user)
    user_caption = html.escape(first_msg.caption or "")
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
        await first_msg.answer(
            f"✅ <b>Твой альбом ({len(album)} файлов) отправлен на модерацию!</b>\n\n"
            f"🎭 Режим публикации: <b>Анонимно</b>",
            parse_mode="HTML",
            reply_markup=user_confirmation_keyboard(first_msg.message_id, is_anon=True),
        )


@router.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot):
    """Forward a single photo suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    user_caption = html.escape(message.caption or "")
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
    await message.answer(
        "✅ <b>Твой кадр отправлен на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.video)
async def handle_video(message: types.Message, bot: Bot):
    """Forward a single video suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    user_caption = html.escape(message.caption or "")
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
    await message.answer(
        "✅ <b>Твоё видео отправлено на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.animation)
async def handle_animation(message: types.Message, bot: Bot):
    """Forward a GIF animation suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    user_caption = html.escape(message.caption or "")
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
    await message.answer(
        "✅ <b>Твоя GIF отправлена на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.voice)
async def handle_voice(message: types.Message, bot: Bot):
    """Forward a voice message suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    user_caption = html.escape(message.caption or "")
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
    await message.answer(
        "✅ <b>Твоё голосовое сообщение отправлено на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.video_note)
async def handle_video_note(message: types.Message, bot: Bot):
    """Forward a video note (circle) suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

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
    await message.answer(
        "✅ <b>Твой кружочек отправлен на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.audio)
async def handle_audio(message: types.Message, bot: Bot):
    """Forward an audio suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    user_caption = html.escape(message.caption or "")
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
    await message.answer(
        "✅ <b>Твоё аудио отправлено на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot):
    """Forward a document suggestion to admin chat and archive."""
    is_sub, link = await check_channel_subscription(bot, message.from_user.id)
    if not is_sub:
        await notify_sub_required(message, link)
        return

    author = get_author_header(message.from_user)
    user_caption = html.escape(message.caption or "")
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
    await message.answer(
        "✅ <b>Твой документ отправлен на модерацию!</b>\n\n"
        "🎭 Режим публикации: <b>Анонимно</b>",
        parse_mode="HTML",
        reply_markup=user_confirmation_keyboard(message.message_id, is_anon=True),
    )
