# --------------------------------------------------------
# Telegram Anonymous Suggestion Bot
# Author: @NAWMBAD (2026)
# Licensed under the MIT License
# --------------------------------------------------------

"""
SQLite-backed storage for the suggestion bot.

Tables
------
- users           : user_id (PK), username, full_name, first_seen, is_active
- blocked_users   : user_id (PK), reason, blocked_at
- stats           : key (PK), value  (total_suggestions, approved, rejected, blocked)
- moderator_stats : admin_id (PK), approved, rejected, blocked
- settings        : key (PK), value
- post_queue      : id (PK AUTO), user_id, message_type, content_json, created_at
"""

import json
import logging
from datetime import datetime
from typing import Any
import aiosqlite

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
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT    DEFAULT '',
            full_name  TEXT    DEFAULT '',
            first_seen TEXT    DEFAULT '',
            is_active  INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id    INTEGER PRIMARY KEY,
            reason     TEXT    DEFAULT '',
            blocked_at TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS stats (
            key   TEXT PRIMARY KEY,
            value INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS moderator_stats (
            admin_id   INTEGER PRIMARY KEY,
            admin_name TEXT    DEFAULT '',
            approved   INTEGER DEFAULT 0,
            rejected   INTEGER DEFAULT 0,
            blocked    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS post_queue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            post_type    TEXT,
            payload_json TEXT,
            created_at   TEXT
        );
    """)

    # Seed stats rows if absent
    for key in ("total_suggestions", "approved", "rejected", "blocked"):
        await _db.execute(
            "INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,)
        )

    # Seed default publish delay setting (0 seconds by default)
    await _db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('publish_delay_seconds', '0')"
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


# ─────────────────────── users (for broadcast) ──────────────────────

async def register_user(user_id: int, username: str = "", full_name: str = "") -> None:
    """Register or update user in SQLite."""
    now = datetime.now().isoformat()
    await _db.execute(
        """INSERT INTO users (user_id, username, full_name, first_seen, is_active)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(user_id) DO UPDATE SET
               username = excluded.username,
               full_name = excluded.full_name,
               is_active = 1""",
        (user_id, username or "", full_name or "", now),
    )
    await _db.commit()


async def get_all_active_users() -> list[int]:
    """Return all active user IDs for broadcasting."""
    async with _db.execute("SELECT user_id FROM users WHERE is_active = 1") as cur:
        return [row[0] async for row in cur]


async def get_users_count() -> int:
    """Return total count of registered users."""
    async with _db.execute("SELECT COUNT(*) FROM users") as cur:
        row = await cur.fetchone()
        return row[0] if row else 0


async def set_user_inactive(user_id: int) -> None:
    """Mark user inactive when bot is blocked."""
    await _db.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
    await _db.commit()


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
    try:
        os.rename(json_path, json_path + ".migrated")
    except Exception:
        pass
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


# ────────────────────── settings & post queue ───────────────────────

async def get_setting(key: str, default: str = "") -> str:
    async with _db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    await _db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))
    )
    await _db.commit()


async def add_to_queue(user_id: int, post_type: str, payload: dict) -> int:
    """Add a post payload to publish queue. Returns row ID."""
    now = datetime.now().isoformat()
    cur = await _db.execute(
        "INSERT INTO post_queue (user_id, post_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (user_id, post_type, json.dumps(payload, ensure_ascii=False), now),
    )
    await _db.commit()
    return cur.lastrowid


async def pop_from_queue() -> tuple[int, int, str, dict] | None:
    """Fetch and remove the oldest pending post in queue."""
    async with _db.execute("SELECT id, user_id, post_type, payload_json FROM post_queue ORDER BY id ASC LIMIT 1") as cur:
        row = await cur.fetchone()
        if not row:
            return None
        post_id, user_id, post_type, payload_str = row[0], row[1], row[2], row[3]

    await _db.execute("DELETE FROM post_queue WHERE id = ?", (post_id,))
    await _db.commit()
    return post_id, user_id, post_type, json.loads(payload_str)


async def get_queue_length() -> int:
    async with _db.execute("SELECT COUNT(*) FROM post_queue") as cur:
        row = await cur.fetchone()
        return row[0] if row else 0
