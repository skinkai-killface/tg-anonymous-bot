# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
Bot middlewares:
- BlockedUsersMiddleware — silently drops updates from blocked users (O(1) in-memory).
- UserRegisterMiddleware — tracks active users in SQLite for broadcast & stats.
- ThrottlingMiddleware   — rate-limits private messages & auto-bans aggressive spammers.
- MediaGroupMiddleware   — aggregates multi-photo/video albums into single updates.
"""

import time
import html
import asyncio
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

from config import ADMIN_CHAT_ID
from database import is_blocked, block_user, increment_stat, register_user

logger = logging.getLogger(__name__)

# ── Anti-flood & Auto-ban settings ──
THROTTLE_SECONDS = 3       # Min interval between suggestions from one user
SPAM_WINDOW = 10           # Window (in seconds) to count rapid violations
MAX_SPAM_VIOLATIONS = 4    # Max flood attempts within SPAM_WINDOW before auto-ban


class BlockedUsersMiddleware(BaseMiddleware):
    """
    Silently ignores messages from blocked users in private chats.
    Drops the update with zero response to prevent server load during spam.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if event.chat.type == "private" and event.from_user and is_blocked(event.from_user.id):
            return  # Silently drop the update
        return await handler(event, data)


class UserRegisterMiddleware(BaseMiddleware):
    """
    Registers or updates users in SQLite on every private message.
    Used for broadcasting and user analytics.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if event.chat.type == "private" and event.from_user:
            user = event.from_user
            await register_user(
                user_id=user.id,
                username=user.username or "",
                full_name=user.full_name or "",
            )
        return await handler(event, data)


class MediaGroupMiddleware(BaseMiddleware):
    """
    Middleware that buffers multi-photo/video messages belonging to the same album
    (media_group_id) and passes the list of Message objects in data['album'].
    """

    def __init__(self, latency: float = 0.6):
        self.latency = latency
        self.media_groups: Dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        mg_id = event.media_group_id
        if mg_id not in self.media_groups:
            self.media_groups[mg_id] = [event]
            await asyncio.sleep(self.latency)
            messages = self.media_groups.pop(mg_id, [])
            if messages:
                data["album"] = messages
                return await handler(messages[0], data)
            return

        self.media_groups[mg_id].append(event)
        return  # Drop subsequent parts of album; first task will handle it with data['album']


class ThrottlingMiddleware(BaseMiddleware):
    """
    Rate-limits private-chat messages and auto-bans users who aggressively flood.
    """

    def __init__(self, cooldown: float = THROTTLE_SECONDS):
        self.cooldown = cooldown
        self._user_timestamps: Dict[int, float] = {}
        self._user_violations: Dict[int, list[float]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        # Only throttle private chat messages
        if event.chat.type != "private" or not event.from_user:
            return await handler(event, data)

        # Skip commands — let /start, /help etc. pass through
        if event.text and event.text.startswith("/"):
            return await handler(event, data)

        user_id = event.from_user.id
        now = time.monotonic()
        last = self._user_timestamps.get(user_id, 0)

        if now - last < self.cooldown:
            # Record this violation attempt
            violations = self._user_violations.get(user_id, [])
            violations = [t for t in violations if now - t <= SPAM_WINDOW]
            violations.append(now)
            self._user_violations[user_id] = violations

            # Check if threshold for AUTO-BAN is reached
            if len(violations) >= MAX_SPAM_VIOLATIONS:
                logger.warning(f"Auto-banning spammer {user_id} ({event.from_user.full_name}) after {len(violations)} flood attempts")
                
                await block_user(user_id, reason=f"Автобан: спам ({len(violations)} запросов за {SPAM_WINDOW}с)")
                await increment_stat("blocked")
                
                self._user_violations.pop(user_id, None)
                self._user_timestamps.pop(user_id, None)

                try:
                    await event.answer("⛔ <b>Вы были автоматически заблокированы за спам/флуд.</b>", parse_mode="HTML")
                except Exception:
                    pass

                try:
                    name = html.escape(event.from_user.full_name or "Пользователь")
                    tag = f", @{event.from_user.username}" if event.from_user.username else ""
                    bot = event.bot
                    await bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=(
                            f"🚨 <b>Автобан спамера!</b>\n\n"
                            f"👤 Пользователь: <a href=\"tg://user?id={user_id}\">{name}</a> [ID: <code>{user_id}</code>{tag}]\n"
                            f"⚠️ Причина: <code>Превышен лимит сообщений ({len(violations)} попыток за {SPAM_WINDOW} сек)</code>\n"
                            f"🛡 Все последующие сообщения от него будут бесшумно игнорироваться."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"Failed to send auto-ban notice to admin chat: {e}")

                return  # Drop update

            remaining = round(self.cooldown - (now - last))
            try:
                if len(violations) >= 2:
                    await event.answer("⚠️ <b>Не спамь!</b> Ещё попытка, и ты получишь автоматический бан.", parse_mode="HTML")
                else:
                    await event.answer(f"⏳ Подожди ещё {remaining} сек. перед следующим сообщением.")
            except Exception:
                pass

            return  # Drop update

        # Reset violations if user waited normally
        self._user_timestamps[user_id] = now
        self._user_violations.pop(user_id, None)

        return await handler(event, data)
