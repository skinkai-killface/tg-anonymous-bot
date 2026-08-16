# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

import asyncio
import logging

# Auto-enable uvloop for better performance on Linux (Arch)
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from aiogram import Bot, Dispatcher
from config import BOT_TOKEN, ADMIN_CHAT_ID, BOT_VERSION
from handlers import start_router, suggest_router, moderation_router, admin_router
from middlewares import (
    BlockedUsersMiddleware,
    UserRegisterMiddleware,
    ThrottlingMiddleware,
    MediaGroupMiddleware,
)
from database import init_db, close_db, migrate_from_json
from auto_updater import auto_update_checker_loop

logger = logging.getLogger(__name__)

_updater_task: asyncio.Task | None = None


async def on_startup(bot: Bot):
    """Initialize database, start auto-updater loop, and notify admin chat."""
    global _updater_task

    # Initialize SQLite database
    await init_db()
    logger.info("Database initialized.")

    # One-time migration from old blocked_users.json (if exists)
    migrated = await migrate_from_json()
    if migrated:
        logger.info(f"Migrated {migrated} blocked users from JSON to SQLite.")

    # Start background auto-updater task
    _updater_task = asyncio.create_task(auto_update_checker_loop(bot))

    try:
        me = await bot.get_me()
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🟢 <b>Бот @{me.username} (v{BOT_VERSION}) успешно запущен и готов к работе!</b>",
            parse_mode="HTML",
        )
        logger.info("Startup notification sent to admin chat.")
    except Exception as e:
        logger.warning(f"Could not send startup notification to admin chat: {e}")


async def on_shutdown(bot: Bot):
    """Close database, cancel background tasks, and notify admin chat."""
    global _updater_task
    if _updater_task and not _updater_task.done():
        _updater_task.cancel()

    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="🔴 <b>Бот выключен / перезагружается...</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await close_db()


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

    # Register middlewares in optimal order
    dp.message.middleware(BlockedUsersMiddleware())
    dp.message.middleware(UserRegisterMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(MediaGroupMiddleware())

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
