import html
from aiogram import Router, types, Bot, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_CHAT_ID
from blocked import is_blocked

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
    if is_blocked(message.from_user.id):
        await message.answer("⛔ Вы заблокированы и не можете отправлять предложения.")
        return

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


@router.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot):
    """Forward a photo suggestion to the admin chat for moderation."""
    if is_blocked(message.from_user.id):
        await message.answer("⛔ Вы заблокированы и не можете отправлять предложения.")
        return

    author = get_author_header(message.from_user)
    caption = message.caption or ""

    photo = message.photo[-1]  # highest resolution

    await bot.send_photo(
        chat_id=ADMIN_CHAT_ID,
        photo=photo.file_id,
        caption=f"📩 <b>Новое предложение (фото)</b>\n"
                f"{author}\n\n"
                f"{caption}",
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоё фото отправлено на модерацию!")


@router.message(F.video)
async def handle_video(message: types.Message, bot: Bot):
    """Forward a video suggestion to the admin chat for moderation."""
    if is_blocked(message.from_user.id):
        await message.answer("⛔ Вы заблокированы и не можете отправлять предложения.")
        return

    author = get_author_header(message.from_user)
    caption = message.caption or ""

    await bot.send_video(
        chat_id=ADMIN_CHAT_ID,
        video=message.video.file_id,
        caption=f"📩 <b>Новое предложение (видео)</b>\n"
                f"{author}\n\n"
                f"{caption}",
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твоё видео отправлено на модерацию!")


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot):
    """Forward a document suggestion to the admin chat for moderation."""
    if is_blocked(message.from_user.id):
        await message.answer("⛔ Вы заблокированы и не можете отправлять предложения.")
        return

    author = get_author_header(message.from_user)
    caption = message.caption or ""

    await bot.send_document(
        chat_id=ADMIN_CHAT_ID,
        document=message.document.file_id,
        caption=f"📩 <b>Новое предложение (документ)</b>\n"
                f"{author}\n\n"
                f"{caption}",
        parse_mode="HTML",
        reply_markup=moderation_keyboard(message.from_user.id, message.message_id),
    )
    await message.answer("✅ Твой документ отправлен на модерацию!")
