from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message

from blocked import is_blocked


class BlockedUsersMiddleware(BaseMiddleware):
    """
    Middleware that silently ignores messages from blocked users in private chats.
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
