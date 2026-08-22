# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
In-memory store for multi-media albums awaiting moderation.
"""

from typing import Dict, List, Any

import time
from typing import Dict, List, Any, Tuple

# Map: suggestion_msg_id -> (timestamp, list of media dicts)
_album_store: Dict[int, Tuple[float, List[Dict[str, Any]]]] = {}
ALBUM_TTL_SECONDS = 86400  # 24 hours


def _cleanup_expired() -> None:
    """Remove albums older than ALBUM_TTL_SECONDS."""
    now = time.monotonic()
    expired = [msg_id for msg_id, (ts, _) in _album_store.items() if now - ts > ALBUM_TTL_SECONDS]
    for msg_id in expired:
        _album_store.pop(msg_id, None)


def save_album(message_id: int, items: List[Dict[str, Any]]) -> None:
    _cleanup_expired()
    _album_store[message_id] = (time.monotonic(), items)


def get_album(message_id: int) -> List[Dict[str, Any]] | None:
    _cleanup_expired()
    entry = _album_store.get(message_id)
    return entry[1] if entry else None


def pop_album(message_id: int) -> List[Dict[str, Any]] | None:
    _cleanup_expired()
    entry = _album_store.pop(message_id, None)
    return entry[1] if entry else None

