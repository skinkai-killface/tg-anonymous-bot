# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
Role-based access control for admin chat commands.

Roles
-----
- **owner**  : chat creator — can execute ALL bot commands.
- **admin**  : chat administrator — can moderate suggestions and view stats.
- **member** : regular participant — no bot permissions.

Results are cached with a configurable TTL to avoid hammering the Telegram API.
"""

import time
import logging
from typing import Dict, Tuple

from aiogram import Bot
from aiogram.types import Message, CallbackQuery

from config import ADMIN_CHAT_ID

logger = logging.getLogger(__name__)

# Cache: {(chat_id, user_id): (role, timestamp)}
# role is one of: "creator", "administrator", "member", "restricted", "left", "kicked"
_role_cache: Dict[Tuple[int, int], Tuple[str, float]] = {}
CACHE_TTL_SECONDS = 60


async def _get_member_status(bot: Bot, chat_id: int, user_id: int) -> str:
    """Fetch and cache the chat member status from Telegram API."""
    cache_key = (chat_id, user_id)
    now = time.monotonic()

    cached = _role_cache.get(cache_key)
    if cached and (now - cached[1]) < CACHE_TTL_SECONDS:
        return cached[0]

    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        status = member.status
    except Exception as e:
        logger.warning("Could not fetch member status for user %d in chat %d: %s", user_id, chat_id, e)
        # Fail closed: deny access if we can't verify
        status = "unknown"

    _role_cache[cache_key] = (status, now)
    return status


async def is_chat_owner(bot: Bot, user_id: int, chat_id: int = ADMIN_CHAT_ID) -> bool:
    """Check if user is the owner (creator) of the admin chat."""
    status = await _get_member_status(bot, chat_id, user_id)
    return status == "creator"


async def is_chat_admin(bot: Bot, user_id: int, chat_id: int = ADMIN_CHAT_ID) -> bool:
    """Check if user is an admin or owner of the admin chat."""
    status = await _get_member_status(bot, chat_id, user_id)
    return status in ("creator", "administrator")


async def check_owner(message: Message, bot: Bot) -> bool:
    """
    Verify the sender is the chat owner. If not, send a denial message.
    Returns True if the user IS the owner.
    """
    if not message.from_user:
        return False

    if await is_chat_owner(bot, message.from_user.id, message.chat.id):
        return True

    await message.reply("🔒 Эта команда доступна только <b>владельцу</b> группы.", parse_mode="HTML")
    return False


async def check_admin(message: Message, bot: Bot) -> bool:
    """
    Verify the sender is an admin or owner. If not, send a denial message.
    Returns True if the user IS an admin/owner.
    """
    if not message.from_user:
        return False

    if await is_chat_admin(bot, message.from_user.id, message.chat.id):
        return True

    await message.reply("🔒 Эта команда доступна только <b>администраторам</b> группы.", parse_mode="HTML")
    return False


async def check_callback_admin(callback: CallbackQuery, bot: Bot) -> bool:
    """
    Verify the callback sender is an admin or owner. If not, show alert.
    Returns True if the user IS an admin/owner.
    """
    if not callback.from_user or not callback.message:
        return False

    if await is_chat_admin(bot, callback.from_user.id, callback.message.chat.id):
        return True

    await callback.answer("🔒 Только администраторы могут модерировать.", show_alert=True)
    return False


def invalidate_cache(chat_id: int = ADMIN_CHAT_ID, user_id: int | None = None) -> None:
    """Clear role cache — call when chat membership changes."""
    if user_id:
        _role_cache.pop((chat_id, user_id), None)
    else:
        keys_to_remove = [k for k in _role_cache if k[0] == chat_id]
        for k in keys_to_remove:
            del _role_cache[k]
