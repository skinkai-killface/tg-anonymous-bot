# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
In-memory store for multi-media albums awaiting moderation.
"""

from typing import Dict, List, Any

# Map: suggestion_msg_id -> list of media dicts: [{"type": "photo"|"video", "file_id": str, "caption": str}]
_album_store: Dict[int, List[Dict[str, Any]]] = {}


def save_album(message_id: int, items: List[Dict[str, Any]]) -> None:
    _album_store[message_id] = items


def get_album(message_id: int) -> List[Dict[str, Any]] | None:
    return _album_store.get(message_id)


def pop_album(message_id: int) -> List[Dict[str, Any]] | None:
    return _album_store.pop(message_id, None)
