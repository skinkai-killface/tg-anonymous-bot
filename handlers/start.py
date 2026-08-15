# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """
    Handler for the /start command.
    Greets the user and explains how to submit an anonymous message.
    """
    await message.answer(
        "👋 Привет! Я бот анонимных предложений.\n\n"
        "Отправь мне любое сообщение (текст, фото, видео, голосовое, стикер, кружочек, документ), "
        "и я передам его на модерацию администраторам.\n\n"
        "Если твоё сообщение одобрят — оно будет опубликовано в канале!\n\n"
        "Справка по боту: /help"
    )


@router.message(Command("help"))
async def cmd_help_user(message: types.Message):
    """
    Handler for the /help command in PM.
    """
    await message.answer(
        "ℹ️ <b>Как пользоваться ботом:</b>\n\n"
        "1. Просто отправь сюда любое сообщение, фото, видео, голосовое или кружочек.\n"
        "2. Бот передаст его модераторам (твоя личность анонимна в канале).\n"
        "3. После одобрения сообщение появится в канале!\n\n"
        "<b>Команды:</b>\n"
        "• /start — перезапустить диалог\n"
        "• /help — справка по боту",
        parse_mode="HTML",
    )
