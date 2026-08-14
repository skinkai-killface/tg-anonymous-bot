from aiogram import Router, types, F
from aiogram.filters import CommandStart

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
        "Отправь мне любое сообщение (текст, фото, видео, документ), "
        "и я передам его на модерацию администраторам.\n\n"
        "Если твоё сообщение одобрят — оно будет опубликовано в канале!"
    )
