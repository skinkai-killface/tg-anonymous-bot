# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
SQLite-backed storage for the suggestion bot.

Tables
------
- blocked_users   : user_id (PK), reason, blocked_at
- stats           : key (PK), value  (total_suggestions, approved, rejected, blocked)
- moderator_stats : admin_id (PK), approved, rejected, blocked
"""

import aiosqlite
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

# In-memory cache of blocked user IDs for O(1) middleware checks.
_blocked_cache: set[int] = set()

# Global database connection (reused throughout the bot lifetime).
_db: aiosqlite.Connection | None = None


# ─────────────────────────── init / close ───────────────────────────

async def init_db() -> None:
    """Create tables (if needed) and populate the blocked-users cache."""
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id   INTEGER PRIMARY KEY,
            reason    TEXT    DEFAULT '',
            blocked_at TEXT   DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS stats (
            key   TEXT PRIMARY KEY,
            value INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS moderator_stats (
            admin_id INTEGER PRIMARY KEY,
            admin_name TEXT DEFAULT '',
            approved INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0,
            blocked  INTEGER DEFAULT 0
        );
    """)

    # Seed stats rows if absent
    for key in ("total_suggestions", "approved", "rejected", "blocked"):
        await _db.execute(
            "INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,)
        )
    await _db.commit()

    # Fill in-memory cache
    async with _db.execute("SELECT user_id FROM blocked_users") as cur:
        _blocked_cache.clear()
        async for row in cur:
            _blocked_cache.add(row[0])

    logger.info("Database initialised. Blocked cache: %d users", len(_blocked_cache))


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


# ─────────────────────── migrate from JSON ──────────────────────────

async def migrate_from_json(json_path: str = "blocked_users.json") -> int:
    """
    One-time import of blocked_users.json into SQLite.
    Returns the number of users migrated.
    """
    import json, os
    if not os.path.exists(json_path):
        return 0

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
    except Exception:
        return 0

    count = 0
    for uid_str, info in data.items():
        uid = int(uid_str)
        reason = info.get("reason", "") if isinstance(info, dict) else str(info)
        await _db.execute(
            "INSERT OR IGNORE INTO blocked_users (user_id, reason, blocked_at) VALUES (?, ?, ?)",
            (uid, reason, datetime.now().isoformat()),
        )
        _blocked_cache.add(uid)
        count += 1

    await _db.commit()

    # Rename old file so it's not re-imported
    os.rename(json_path, json_path + ".migrated")
    logger.info("Migrated %d blocked users from JSON → SQLite", count)
    return count


# ───────────────────────── blocked users ────────────────────────────

def is_blocked(user_id: int) -> bool:
    """Synchronous O(1) check against in-memory cache."""
    return user_id in _blocked_cache


async def block_user(user_id: int, reason: str = "") -> None:
    _blocked_cache.add(user_id)
    await _db.execute(
        "INSERT OR REPLACE INTO blocked_users (user_id, reason, blocked_at) VALUES (?, ?, ?)",
        (user_id, reason, datetime.now().isoformat()),
    )
    await _db.commit()


async def unblock_user(user_id: int) -> bool:
    """Returns True if the user was previously blocked."""
    if user_id not in _blocked_cache:
        return False
    _blocked_cache.discard(user_id)
    await _db.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
    await _db.commit()
    return True


async def get_blocked_list() -> list[tuple[int, str, str]]:
    """Return list of (user_id, reason, blocked_at)."""
    async with _db.execute("SELECT user_id, reason, blocked_at FROM blocked_users") as cur:
        return [(row[0], row[1], row[2]) async for row in cur]


# ──────────────────────── global stats ──────────────────────────────

async def increment_stat(key: str, amount: int = 1) -> None:
    await _db.execute(
        "UPDATE stats SET value = value + ? WHERE key = ?", (amount, key)
    )
    await _db.commit()


async def get_stats() -> dict[str, int]:
    result: dict[str, int] = {}
    async with _db.execute("SELECT key, value FROM stats") as cur:
        async for row in cur:
            result[row[0]] = row[1]
    return result


# ────────────────────── moderator stats ─────────────────────────────

async def record_moderation(admin_id: int, admin_name: str, action: str) -> None:
    """
    action: 'approved' | 'rejected' | 'blocked'
    """
    await _db.execute(
        f"""INSERT INTO moderator_stats (admin_id, admin_name, {action})
            VALUES (?, ?, 1)
            ON CONFLICT(admin_id) DO UPDATE
            SET {action} = {action} + 1, admin_name = excluded.admin_name""",
        (admin_id, admin_name),
    )
    await _db.commit()


async def get_moderator_stats() -> list[tuple[int, str, int, int, int]]:
    """Return list of (admin_id, admin_name, approved, rejected, blocked)."""
    async with _db.execute(
        "SELECT admin_id, admin_name, approved, rejected, blocked FROM moderator_stats ORDER BY (approved+rejected+blocked) DESC"
    ) as cur:
        return [(row[0], row[1], row[2], row[3], row[4]) async for row in cur]
