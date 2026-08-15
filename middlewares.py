"""
Bot middlewares:
- BlockedUsersMiddleware  — silently drops updates from blocked users (O(1) in-memory).
- ThrottlingMiddleware    — rate-limits private messages (1 msg / THROTTLE_SECONDS per user).
"""

import time
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

from database import is_blocked

logger = logging.getLogger(__name__)

# ── Anti-flood settings ──
THROTTLE_SECONDS = 3  # Min interval between suggestions from one user


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


class ThrottlingMiddleware(BaseMiddleware):
    """
    Rate-limits private-chat messages to 1 per THROTTLE_SECONDS per user.
    If user sends faster, silently drops or sends a brief warning.
    """

    def __init__(self, cooldown: float = THROTTLE_SECONDS):
        self.cooldown = cooldown
        self._user_timestamps: Dict[int, float] = {}

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
            # Too fast — warn them once, then drop
            remaining = round(self.cooldown - (now - last))
            try:
                await event.answer(f"⏳ Подожди ещё {remaining} сек. перед следующим сообщением.")
            except Exception:
                pass
            return  # drop update

        self._user_timestamps[user_id] = now
        return await handler(event, data)
