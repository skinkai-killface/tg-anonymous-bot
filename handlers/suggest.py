import html
from aiogram import Router, types, Bot, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_CHAT_ID

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
    Builds an inline keyboard with Approve / Reject / Block buttons.
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
                text="🚫 Заблокировать",
                callback_data=f"block:{user_id}:{message_id}",
            ),
        ],
    ])


@router.message(F.text)
async def handle_text(message: types.Message, bot: Bot):
    """Forward a text suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение</b>\n"
             f"{author}\n\n"
             f"{message.text}",
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоё сообщение отправлено на модерацию!")


@router.message(F.sticker)
async def handle_sticker(message: types.Message, bot: Bot):
    """Forward a sticker suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение (стикер)</b>\n{author}",
        parse_mode="HTML",
    )
    await bot.send_sticker(
        chat_id=ADMIN_CHAT_ID,
        sticker=message.sticker.file_id,
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твой стикер отправлен на модерацию!")


@router.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot):
    """Forward a photo suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (фото)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    photo = message.photo[-1]  # highest resolution

    await bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=photo.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твой кадр отправлен на модерацию!")


@router.message(F.video)
async def handle_video(message: types.Message, bot: Bot):
    """Forward a video suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (видео)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    await bot.send_video(
        chat_id=ADMIN_CHAT_ID,
        video=message.video.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоё видео отправлено на модерацию!")


@router.message(F.animation)
async def handle_animation(message: types.Message, bot: Bot):
    """Forward a GIF animation suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (GIF)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    await bot.send_animation(
        chat_id=ADMIN_CHAT_ID,
        animation=message.animation.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоя GIF отправлена на модерацию!")


@router.message(F.voice)
async def handle_voice(message: types.Message, bot: Bot):
    """Forward a voice message suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (голосовое)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    await bot.send_voice(
        chat_id=ADMIN_CHAT_ID,
        voice=message.voice.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоё голосовое сообщение отправлено на модерацию!")


@router.message(F.video_note)
async def handle_video_note(message: types.Message, bot: Bot):
    """Forward a video note (circle) suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)

    await bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📩 <b>Новое предложение (кружочек)</b>\n{author}",
        parse_mode="HTML",
    )
    await bot.send_video_note(
        chat_id=ADMIN_CHAT_ID,
        video_note=message.video_note.file_id,
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твой кружочек отправлен на модерацию!")


@router.message(F.audio)
async def handle_audio(message: types.Message, bot: Bot):
    """Forward an audio suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (аудио)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    await bot.send_audio(
        chat_id=ADMIN_CHAT_ID,
        audio=message.audio.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоё аудио отправлено на модерацию!")


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot):
    """Forward a document suggestion to the admin chat for moderation."""
    author = get_author_header(message.from_user)
    user_caption = message.caption or ""
    header = f"📩 <b>Новое предложение (документ)</b>\n{author}"
    full_caption = f"{header}\n\n{user_caption}" if user_caption else header

    await bot.send_document(
        chat_id=ADMIN_CHAT_ID,
        document=message.document.file_id,
        caption=full_caption,
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твой документ отправлен на модерацию!")
