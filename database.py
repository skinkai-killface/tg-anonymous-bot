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
- archive_posts   : full suggestion history with moderation metadata
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

        CREATE TABLE IF NOT EXISTS archive_posts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER,
            user_name      TEXT DEFAULT '',
            user_handle    TEXT DEFAULT '',
            content_type   TEXT DEFAULT 'text',
            text_content   TEXT DEFAULT '',
            edited_text    TEXT DEFAULT '',
            media_json     TEXT DEFAULT '[]',
            status         TEXT DEFAULT 'pending',
            is_anonymous   INTEGER DEFAULT 1,
            moderator_id   INTEGER DEFAULT NULL,
            moderator_name TEXT DEFAULT '',
            channel_msg_id INTEGER DEFAULT NULL,
            orig_msg_id    INTEGER DEFAULT NULL,
            admin_msg_id   INTEGER DEFAULT NULL,
            created_at     TEXT DEFAULT '',
            moderated_at   TEXT DEFAULT ''
        );
    """)

    # Attempt to add is_anonymous column to archive_posts if upgrading existing DB
    try:
        await _db.execute("ALTER TABLE archive_posts ADD COLUMN is_anonymous INTEGER DEFAULT 1")
    except Exception:
        pass

    # Seed stats rows if absent
    for key in ("total_suggestions", "approved", "rejected", "blocked"):
        await _db.execute(
            "INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,)
        )

    # Seed default settings
    await _db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('publish_delay_seconds', '0')"
    )
    await _db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('subcheck_enabled', '1')"
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


# ─────────────────────────── restore ────────────────────────────────

async def restore_db(new_db_path: str) -> dict:
    """
    Replace the active database with a new SQLite file.

    Steps:
    1. Validate the file is a real SQLite database.
    2. Close the current connection.
    3. Rename current bot_data.db → bot_data.db.bak (safety net).
    4. Copy the new file to bot_data.db.
    5. Re-open the connection and rebuild caches.

    Returns a dict with result info:
      {'ok': bool, 'error': str | None, 'users': int, 'blocked': int}
    """
    import os
    import shutil

    global _db, _blocked_cache

    # 1. Validate: SQLite files start with the magic header string
    SQLITE_MAGIC = b"SQLite format 3\x00"
    try:
        with open(new_db_path, "rb") as f:
            header = f.read(16)
        if header != SQLITE_MAGIC:
            return {"ok": False, "error": "Файл не является корректной SQLite-базой данных."}
    except Exception as e:
        return {"ok": False, "error": f"Не удалось прочитать файл: {e}"}

    # 2. Close current connection
    if _db:
        try:
            await _db.close()
        except Exception:
            pass
        _db = None

    # 3. Back up the current database
    bak_path = DB_PATH + ".bak"
    if os.path.exists(DB_PATH):
        try:
            shutil.copy2(DB_PATH, bak_path)
            logger.info("Current DB backed up to %s", bak_path)
        except Exception as e:
            logger.warning("Could not backup current DB: %s", e)

    # 4. Replace the database file
    try:
        shutil.copy2(new_db_path, DB_PATH)
        logger.info("Database replaced with restored file from %s", new_db_path)
    except Exception as e:
        # Try to recover from backup
        if os.path.exists(bak_path):
            shutil.copy2(bak_path, DB_PATH)
        return {"ok": False, "error": f"Не удалось заменить файл БД: {e}"}

    # 5. Re-initialise connection and caches
    try:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row

        # Run migrations so any missing columns/tables are created
        await _db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
                full_name TEXT DEFAULT '', first_seen TEXT DEFAULT '', is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id INTEGER PRIMARY KEY, reason TEXT DEFAULT '', blocked_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS moderator_stats (
                admin_id INTEGER PRIMARY KEY, admin_name TEXT DEFAULT '',
                approved INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS post_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                post_type TEXT, payload_json TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS archive_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, user_name TEXT DEFAULT '', user_handle TEXT DEFAULT '',
                content_type TEXT DEFAULT 'text', text_content TEXT DEFAULT '',
                edited_text TEXT DEFAULT '', media_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending', is_anonymous INTEGER DEFAULT 1,
                moderator_id INTEGER DEFAULT NULL, moderator_name TEXT DEFAULT '',
                channel_msg_id INTEGER DEFAULT NULL, orig_msg_id INTEGER DEFAULT NULL,
                admin_msg_id INTEGER DEFAULT NULL, created_at TEXT DEFAULT '',
                moderated_at TEXT DEFAULT ''
            );
        """)
        # Add is_anonymous column if upgrading from older backup
        try:
            await _db.execute("ALTER TABLE archive_posts ADD COLUMN is_anonymous INTEGER DEFAULT 1")
        except Exception:
            pass

        await _db.commit()

        # Rebuild blocked users cache
        _blocked_cache.clear()
        async with _db.execute("SELECT user_id FROM blocked_users") as cur:
            async for row in cur:
                _blocked_cache.add(row[0])

        # Count users for report
        async with _db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            users_count = row[0] if row else 0

        blocked_count = len(_blocked_cache)
        logger.info(
            "Database restored. Users: %d, Blocked: %d",
            users_count, blocked_count,
        )
        return {"ok": True, "error": None, "users": users_count, "blocked": blocked_count}

    except Exception as e:
        logger.error("Failed to re-open restored DB: %s", e)
        return {"ok": False, "error": f"БД заменена, но переподключение не удалось: {e}. Перезапустите бота."}


async def import_archive_from_json(records: list[dict]) -> dict:
    """
    Import archive posts from a JSON export (as produced by /archive export).
    Uses INSERT OR REPLACE so duplicate IDs are overwritten.

    Returns {'ok': bool, 'imported': int, 'skipped': int, 'error': str | None}.
    """
    if _db is None:
        return {"ok": False, "imported": 0, "skipped": 0, "error": "База данных не инициализирована."}

    imported = 0
    skipped = 0

    for rec in records:
        # Validate required fields
        if not isinstance(rec, dict) or "id" not in rec:
            skipped += 1
            continue
        try:
            media_json = json.dumps(rec.get("media", []), ensure_ascii=False)
            await _db.execute(
                """
                INSERT OR REPLACE INTO archive_posts (
                    id, user_id, user_name, user_handle,
                    content_type, text_content, edited_text, media_json,
                    status, is_anonymous, moderator_id, moderator_name,
                    channel_msg_id, orig_msg_id, admin_msg_id,
                    created_at, moderated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.get("id"),
                    rec.get("user_id"),
                    rec.get("user_name", ""),
                    rec.get("user_handle", ""),
                    rec.get("content_type", "text"),
                    rec.get("text_content", ""),
                    rec.get("edited_text", ""),
                    media_json,
                    rec.get("status", "pending"),
                    int(rec.get("is_anonymous", 1)),
                    rec.get("moderator_id"),
                    rec.get("moderator_name", ""),
                    rec.get("channel_msg_id"),
                    rec.get("orig_msg_id"),
                    rec.get("admin_msg_id"),
                    rec.get("created_at", ""),
                    rec.get("moderated_at", ""),
                ),
            )
            imported += 1
        except Exception as e:
            logger.warning("Skipped archive record id=%s: %s", rec.get("id"), e)
            skipped += 1

    try:
        await _db.commit()
    except Exception as e:
        return {"ok": False, "imported": imported, "skipped": skipped, "error": f"Ошибка commit: {e}"}

    logger.info("Archive import: %d imported, %d skipped", imported, skipped)
    return {"ok": True, "imported": imported, "skipped": skipped, "error": None}


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


# ────────────────────────── Archive API ──────────────────────────

async def add_to_archive(
    user_id: int,
    user_name: str,
    user_handle: str,
    content_type: str,
    text_content: str,
    media_list: list[dict],
    orig_msg_id: int,
    admin_msg_id: int | None = None,
) -> int:
    """Save a suggestion into the archive table with full metadata."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    media_str = json.dumps(media_list, ensure_ascii=False) if media_list else "[]"

    cur = await _db.execute(
        """
        INSERT INTO archive_posts (
            user_id, user_name, user_handle, content_type,
            text_content, media_json, status,
            orig_msg_id, admin_msg_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            user_id,
            user_name,
            user_handle,
            content_type,
            text_content,
            media_str,
            orig_msg_id,
            admin_msg_id,
            now,
        ),
    )
    await _db.commit()
    return cur.lastrowid


async def update_archive_status(
    orig_msg_id: int,
    status: str,
    moderator_id: int | None = None,
    moderator_name: str = "",
    channel_msg_id: int | None = None,
) -> None:
    """Update status of an archived suggestion when moderated."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await _db.execute(
        """
        UPDATE archive_posts
        SET status = ?, moderator_id = ?, moderator_name = ?, channel_msg_id = ?, moderated_at = ?
        WHERE orig_msg_id = ?
        """,
        (status, moderator_id, moderator_name, channel_msg_id, now, orig_msg_id),
    )
    await _db.commit()


async def update_archive_text(
    new_text: str,
    admin_msg_id: int | None = None,
    orig_msg_id: int | None = None,
) -> None:
    """Update edited text in the archive."""
    if admin_msg_id:
        await _db.execute(
            "UPDATE archive_posts SET edited_text = ? WHERE admin_msg_id = ?",
            (new_text, admin_msg_id),
        )
    elif orig_msg_id:
        await _db.execute(
            "UPDATE archive_posts SET edited_text = ? WHERE orig_msg_id = ?",
            (new_text, orig_msg_id),
        )
    await _db.commit()


async def get_archive_stats() -> dict[str, int]:
    """Return summary counts of archive posts by status."""
    stats = {"total": 0, "approved": 0, "rejected": 0, "blocked": 0, "pending": 0}
    async with _db.execute("SELECT status, COUNT(*) FROM archive_posts GROUP BY status") as cur:
        async for row in cur:
            st, cnt = row[0], row[1]
            if st in stats:
                stats[st] = cnt
            stats["total"] += cnt
    return stats


async def get_recent_archive(limit: int = 5) -> list[dict]:
    """Return recent archived posts."""
    async with _db.execute(
        "SELECT id, user_id, user_name, user_handle, content_type, text_content, edited_text, status, created_at FROM archive_posts ORDER BY id DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "user_name": r[2],
                "user_handle": r[3],
                "content_type": r[4],
                "text_content": r[5],
                "edited_text": r[6],
                "status": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]


async def get_archive_by_id(archive_id: int) -> dict | None:
    """Get full details of a specific archived post."""
    async with _db.execute(
        """
        SELECT id, user_id, user_name, user_handle, content_type,
               text_content, edited_text, media_json, status,
               moderator_id, moderator_name, channel_msg_id,
               orig_msg_id, admin_msg_id, created_at, moderated_at
        FROM archive_posts WHERE id = ?
        """,
        (archive_id,),
    ) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "user_name": row[2],
            "user_handle": row[3],
            "content_type": row[4],
            "text_content": row[5],
            "edited_text": row[6],
            "media_list": json.loads(row[7]) if row[7] else [],
            "status": row[8],
            "moderator_id": row[9],
            "moderator_name": row[10],
            "channel_msg_id": row[11],
            "orig_msg_id": row[12],
            "admin_msg_id": row[13],
            "created_at": row[14],
            "moderated_at": row[15],
        }


async def export_full_archive_json() -> str:
    """Export all archive rows as a formatted JSON string."""
    async with _db.execute(
        """
        SELECT id, user_id, user_name, user_handle, content_type,
               text_content, edited_text, media_json, status,
               moderator_id, moderator_name, channel_msg_id,
               orig_msg_id, admin_msg_id, created_at, moderated_at
        FROM archive_posts ORDER BY id ASC
        """
    ) as cur:
        rows = await cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "user_id": r[1],
                "user_name": r[2],
                "user_handle": r[3],
                "content_type": r[4],
                "text_content": r[5],
                "edited_text": r[6],
                "media": json.loads(r[7]) if r[7] else [],
                "status": r[8],
                "moderator_id": r[9],
                "moderator_name": r[10],
                "channel_msg_id": r[11],
                "orig_msg_id": r[12],
                "admin_msg_id": r[13],
                "created_at": r[14],
                "moderated_at": r[15],
            })
        return json.dumps(result, ensure_ascii=False, indent=2)


async def get_approved_archive_posts() -> list[dict]:
    """Return all archived posts with status='approved' ordered by id ASC."""
    async with _db.execute(
        """
        SELECT id, user_id, user_name, user_handle, content_type,
               text_content, edited_text, media_json, status, is_anonymous,
               moderator_id, moderator_name, channel_msg_id,
               orig_msg_id, admin_msg_id, created_at, moderated_at
        FROM archive_posts WHERE status = 'approved' ORDER BY id ASC
        """
    ) as cur:
        rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "user_name": r[2],
                "user_handle": r[3],
                "content_type": r[4],
                "text_content": r[5],
                "edited_text": r[6],
                "media_list": json.loads(r[7]) if r[7] else [],
                "status": r[8],
                "is_anonymous": bool(r[9]) if r[9] is not None else True,
                "moderator_id": r[10],
                "moderator_name": r[11],
                "channel_msg_id": r[12],
                "orig_msg_id": r[13],
                "admin_msg_id": r[14],
                "created_at": r[15],
                "moderated_at": r[16],
            }
            for r in rows
        ]


async def set_post_anonymity(orig_msg_id: int, is_anonymous: bool) -> None:
    """Toggle anonymity flag for a suggestion."""
    val = 1 if is_anonymous else 0
    await _db.execute("UPDATE archive_posts SET is_anonymous = ? WHERE orig_msg_id = ?", (val, orig_msg_id))
    await _db.commit()


async def get_post_anonymity(orig_msg_id: int) -> bool:
    """Return True if suggestion should be published anonymously, False if with author credit."""
    async with _db.execute("SELECT is_anonymous FROM archive_posts WHERE orig_msg_id = ?", (orig_msg_id,)) as cur:
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else True
