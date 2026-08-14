import json
import os

BLOCKED_FILE = "blocked_users.json"


def _load() -> dict:
    """Load blocked users from JSON file."""
    if not os.path.exists(BLOCKED_FILE):
        return {}
    with open(BLOCKED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    """Save blocked users to JSON file."""
    with open(BLOCKED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_blocked(user_id: int) -> bool:
    """Check if user is blocked."""
    data = _load()
    return str(user_id) in data


def block_user(user_id: int, reason: str = "") -> None:
    """Block a user by ID."""
    data = _load()
    data[str(user_id)] = {"reason": reason}
    _save(data)


def unblock_user(user_id: int) -> bool:
    """Unblock a user. Returns True if was blocked, False otherwise."""
    data = _load()
    if str(user_id) in data:
        del data[str(user_id)]
        _save(data)
        return True
    return False


def get_blocked_list() -> dict:
    """Return the full dict of blocked users."""
    return _load()
