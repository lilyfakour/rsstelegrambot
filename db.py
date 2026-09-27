"""Storage layer, backed by Turso (libSQL) instead of a local SQLite file.

Vercel serverless functions have a read-only filesystem (aside from an
ephemeral, per-invocation /tmp), so a plain SQLite *file* would not survive
between requests. Turso speaks the same SQL dialect as SQLite over the
network, so this module is structurally the same as the original
aiosqlite-based db.py — only the connection and row-shape details changed:

    - one shared libsql_client.Client is reused across warm invocations
      (created lazily on first use in a given serverless instance)
    - every row is returned as a plain dict (via _rows_as_dicts) instead of
      an aiosqlite.Row, since handlers.py/scheduler.py only ever do
      row["column"] lookups

Tables:
    feeds    - one row per subscription (per user)
    posts    - seen-post history used for de-duplication
    settings - per-user settings (update interval)
"""
import time
from typing import Any, Iterable

import libsql_client

import config
from config import default_interval_minutes

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS feeds (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL,
        url            TEXT    NOT NULL,
        title          TEXT    NOT NULL DEFAULT '',
        paused         INTEGER NOT NULL DEFAULT 0,
        interval_minutes INTEGER NOT NULL DEFAULT 0,
        etag           TEXT    NOT NULL DEFAULT '',
        last_modified  TEXT    NOT NULL DEFAULT '',
        last_checked   INTEGER NOT NULL DEFAULT 0,
        last_error     TEXT    NOT NULL DEFAULT '',
        created_at     INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS posts (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
        guid    TEXT    NOT NULL,
        UNIQUE(feed_id, guid)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_posts_feed ON posts(feed_id)",
    """
    CREATE TABLE IF NOT EXISTS settings (
        user_id          INTEGER PRIMARY KEY,
        interval_minutes INTEGER NOT NULL DEFAULT 15
    )
    """,
]

# One client per warm serverless instance, created lazily. libsql_client's
# async client keeps an HTTP(S)/websocket connection to Turso open, so we
# don't want to recreate it on every call.
_client: libsql_client.Client | None = None
_schema_ready = False


def _get_client() -> libsql_client.Client:
    global _client
    if _client is None:
        if not config.TURSO_DATABASE_URL:
            raise RuntimeError(
                "TURSO_DATABASE_URL is not set — see .env.example / DEPLOY.md"
            )
        _client = libsql_client.create_client(
            url=config.TURSO_DATABASE_URL,
            auth_token=config.TURSO_AUTH_TOKEN or None,
        )
    return _client


async def init_db() -> None:
    """Create tables if they don't exist yet. Safe to call repeatedly."""
    global _schema_ready
    if _schema_ready:
        return
    client = _get_client()
    for statement in SCHEMA_STATEMENTS:
        await client.execute(statement)
    _schema_ready = True


def _rows_as_dicts(result: "libsql_client.ResultSet") -> list[dict[str, Any]]:
    columns = result.columns
    return [dict(zip(columns, row)) for row in result.rows]


async def _execute(sql: str, args: Iterable[Any] = ()) -> "libsql_client.ResultSet":
    await init_db()
    return await _get_client().execute(sql, list(args))


# ---------------------------------------------------------------- feeds ----

async def add_feed(user_id: int, url: str, title: str) -> tuple[int, bool]:
    """Insert a feed; returns (feed_id, created)."""
    result = await _execute(
        "SELECT id FROM feeds WHERE user_id = ? AND url = ?", (user_id, url)
    )
    rows = _rows_as_dicts(result)
    if rows:
        return int(rows[0]["id"]), False
    result = await _execute(
        "INSERT INTO feeds (user_id, url, title, created_at) VALUES (?, ?, ?, ?)",
        (user_id, url, title, int(time.time())),
    )
    return int(result.last_insert_rowid), True


async def list_feeds(user_id: int) -> list[dict[str, Any]]:
    query = """
        SELECT f.*,
               COALESCE(NULLIF(f.interval_minutes, 0),
                        COALESCE(s.interval_minutes, ?)) AS eff_interval
        FROM feeds f
        LEFT JOIN settings s ON s.user_id = f.user_id
        WHERE f.user_id = ?
        ORDER BY f.id
    """
    result = await _execute(query, (default_interval_minutes(), user_id))
    return _rows_as_dicts(result)


async def get_feed(user_id: int, feed_id: int) -> dict[str, Any] | None:
    result = await _execute(
        "SELECT * FROM feeds WHERE user_id = ? AND id = ?", (user_id, feed_id)
    )
    rows = _rows_as_dicts(result)
    return rows[0] if rows else None


async def remove_feed(feed_id: int) -> bool:
    await _execute("DELETE FROM posts WHERE feed_id = ?", (feed_id,))
    result = await _execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    return result.rows_affected > 0


async def set_paused(feed_id: int, paused: bool) -> None:
    await _execute(
        "UPDATE feeds SET paused = ? WHERE id = ?",
        (1 if paused else 0, feed_id),
    )


async def update_feed_check(
    feed_id: int,
    etag: str,
    last_modified: str,
    checked_at: int,
    error: str,
) -> None:
    await _execute(
        "UPDATE feeds SET etag = ?, last_modified = ?, last_checked = ?, "
        "last_error = ? WHERE id = ?",
        (etag, last_modified, checked_at, error, feed_id),
    )


# ---------------------------------------------------------------- posts ----

async def seen_guids(feed_id: int) -> set[str]:
    result = await _execute("SELECT guid FROM posts WHERE feed_id = ?", (feed_id,))
    return {str(row[0]) for row in result.rows}


async def mark_seen(feed_id: int, guids: Iterable[str]) -> None:
    values = [(feed_id, g) for g in set(guids)]
    if not values:
        return
    client = _get_client()
    await init_db()
    await client.batch(
        [
            libsql_client.Statement(
                "INSERT OR IGNORE INTO posts (feed_id, guid) VALUES (?, ?)",
                [feed_id, guid],
            )
            for feed_id, guid in values
        ]
    )


# -------------------------------------------------------------- settings ----

async def get_interval(user_id: int) -> int:
    result = await _execute(
        "SELECT interval_minutes FROM settings WHERE user_id = ?", (user_id,)
    )
    return int(result.rows[0][0]) if result.rows else default_interval_minutes()


async def set_interval(user_id: int, minutes: int) -> None:
    await _execute(
        "INSERT INTO settings (user_id, interval_minutes) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET interval_minutes = excluded.interval_minutes",
        (user_id, minutes),
    )


# ------------------------------------------------------------- scheduler ----

async def get_due_feeds(now: int) -> list[dict[str, Any]]:
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
    result = await _execute(query, (default_min, now, default_min))
    return _rows_as_dicts(result)
