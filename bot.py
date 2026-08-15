import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN, ADMIN_CHAT_ID
from handlers import start_router, suggest_router, moderation_router, admin_router
from middlewares import BlockedUsersMiddleware

logger = logging.getLogger(__name__)


async def on_startup(bot: Bot):
    """Notify admin chat that the bot has started."""
    try:
        me = await bot.get_me()
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🟢 <b>Бот @{me.username} успешно запущен и готов к работе!</b>",
            parse_mode="HTML",
        )
        logger.info("Startup notification sent to admin chat.")
    except Exception as e:
        logger.warning(f"Could not send startup notification to admin chat: {e}")


async def on_shutdown(bot: Bot):
    """Notify admin chat that the bot is stopping."""
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="🔴 <b>Бот выключен / перезагружается...</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def main():
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger.info("Starting bot...")

    # Initialize bot and dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Startup & shutdown hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Register middleware to silently ignore blocked users (zero overhead, no response)
    dp.message.middleware(BlockedUsersMiddleware())

    # Register routers (order matters: start, admin, and moderation before suggest)
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(moderation_router)
    dp.include_router(suggest_router)

    # Drop pending updates and start polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped!")
