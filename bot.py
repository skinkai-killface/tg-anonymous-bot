import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from handlers import start_router, suggest_router, moderation_router, admin_router

async def main():
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")

    # Initialize bot and dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Register routers (order matters: start, admin, and moderation before suggest,
    # because suggest catches all messages as a "catch-all")
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
