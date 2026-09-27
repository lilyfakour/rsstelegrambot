"""SQLite storage layer (aiosqlite).

Tables:
    feeds    - one row per subscription (per user)
    posts    - seen-post history used for de-duplication
    settings - per-user settings (update interval)
"""
import time
from typing import Iterable

import aiosqlite

from config import DB_PATH, default_interval_minutes

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    url            TEXT    NOT NULL,
    title          TEXT    NOT NULL DEFAULT '',
    paused         INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER NOT NULL DEFAULT 0,   -- 0 = use user default
    etag           TEXT    NOT NULL DEFAULT '',
    last_modified  TEXT    NOT NULL DEFAULT '',
    last_checked   INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT    NOT NULL DEFAULT '',
    created_at     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, url)
);

CREATE TABLE IF NOT EXISTS posts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    guid    TEXT    NOT NULL,
    UNIQUE(feed_id, guid)
);

CREATE INDEX IF NOT EXISTS idx_posts_feed ON posts(feed_id);

CREATE TABLE IF NOT EXISTS settings (
    user_id          INTEGER PRIMARY KEY,
    interval_minutes INTEGER NOT NULL DEFAULT 15
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as con:
        await con.executescript(SCHEMA)
        await con.commit()


# ---------------------------------------------------------------- feeds ----

async def add_feed(user_id: int, url: str, title: str) -> tuple[int, bool]:
    """Insert a feed; returns (feed_id, created)."""
    async with aiosqlite.connect(DB_PATH) as con:
        cur = await con.execute(
            "SELECT id FROM feeds WHERE user_id = ? AND url = ?", (user_id, url)
        )
        row = await cur.fetchone()
        if row:
            return int(row[0]), False
        cur = await con.execute(
            "INSERT INTO feeds (user_id, url, title, created_at) VALUES (?, ?, ?, ?)",
            (user_id, url, title, int(time.time())),
        )
        await con.commit()
        return int(cur.lastrowid), True


async def list_feeds(user_id: int) -> list[aiosqlite.Row]:
    query = """
        SELECT f.*,
               COALESCE(NULLIF(f.interval_minutes, 0),
                        COALESCE(s.interval_minutes, ?)) AS eff_interval
        FROM feeds f
        LEFT JOIN settings s ON s.user_id = f.user_id
        WHERE f.user_id = ?
        ORDER BY f.id
    """
    async with aiosqlite.connect(DB_PATH) as con:
        con.row_factory = aiosqlite.Row
        cur = await con.execute(query, (default_interval_minutes(), user_id))
        return list(await cur.fetchall())


async def get_feed(user_id: int, feed_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as con:
        con.row_factory = aiosqlite.Row
        cur = await con.execute(
            "SELECT * FROM feeds WHERE user_id = ? AND id = ?", (user_id, feed_id)
        )
        return await cur.fetchone()


async def remove_feed(feed_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as con:
        await con.execute("DELETE FROM posts WHERE feed_id = ?", (feed_id,))
        cur = await con.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        await con.commit()
        return cur.rowcount > 0


async def set_paused(feed_id: int, paused: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as con:
        await con.execute(
            "UPDATE feeds SET paused = ? WHERE id = ?",
            (1 if paused else 0, feed_id),
        )
        await con.commit()


async def update_feed_check(
    feed_id: int,
    etag: str,
    last_modified: str,
    checked_at: int,
    error: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as con:
        await con.execute(
            "UPDATE feeds SET etag = ?, last_modified = ?, last_checked = ?, "
            "last_error = ? WHERE id = ?",
            (etag, last_modified, checked_at, error, feed_id),
        )
        await con.commit()


# ---------------------------------------------------------------- posts ----

async def seen_guids(feed_id: int) -> set[str]:
    async with aiosqlite.connect(DB_PATH) as con:
        cur = await con.execute(
            "SELECT guid FROM posts WHERE feed_id = ?", (feed_id,)
        )
        return {str(r[0]) for r in await cur.fetchall()}


async def mark_seen(feed_id: int, guids: Iterable[str]) -> None:
    values = [(feed_id, g) for g in set(guids)]
    if not values:
        return
    async with aiosqlite.connect(DB_PATH) as con:
        await con.executemany(
            "INSERT OR IGNORE INTO posts (feed_id, guid) VALUES (?, ?)", values
        )
        await con.commit()


# -------------------------------------------------------------- settings ----

async def get_interval(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as con:
        cur = await con.execute(
            "SELECT interval_minutes FROM settings WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return int(row[0]) if row else default_interval_minutes()


async def set_interval(user_id: int, minutes: int) -> None:
    async with aiosqlite.connect(DB_PATH) as con:
        await con.execute(
            "INSERT INTO settings (user_id, interval_minutes) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET interval_minutes = excluded.interval_minutes",
            (user_id, minutes),
        )
        await con.commit()


# ------------------------------------------------------------- scheduler ----

async def get_due_feeds(now: int) -> list[aiosqlite.Row]:
    """Active feeds whose effective interval has elapsed."""
    default_min = default_interval_minutes()
    effective = "COALESCE(NULLIF(f.interval_minutes, 0), COALESCE(s.interval_minutes, ?))"
    query = f"""
        SELECT f.id, f.user_id, f.url, f.title, f.etag, f.last_modified,
               f.last_checked, f.last_error,
               {effective} AS eff_interval
        FROM feeds f
        LEFT JOIN settings s ON s.user_id = f.user_id
        WHERE f.paused = 0
          AND (? - f.last_checked) >= {effective} * 60
        ORDER BY f.last_checked ASC
    """
    async with aiosqlite.connect(DB_PATH) as con:
        con.row_factory = aiosqlite.Row
        cur = await con.execute(query, (default_min, now, default_min))
        return list(await cur.fetchall())
