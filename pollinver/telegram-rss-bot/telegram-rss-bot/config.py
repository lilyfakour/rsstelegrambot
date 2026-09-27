"""Configuration loading for the Telegram RSS bot.

Values are read from a .env file (or real environment variables).
ALLOWED_IDS is re-read on every request so you can add a user to .env
without restarting the bot.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def get_allowed_ids() -> set[int]:
    """Read ALLOWED_IDS from the environment on every call.

    This makes it possible to authorize a new user by simply editing .env —
    no restart needed. Accepts comma or semicolon separated IDs.
    """
    load_dotenv(override=True)
    raw = os.getenv("ALLOWED_IDS", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def default_interval_minutes() -> int:
    """Global default minutes between feed checks (1-1440)."""
    try:
        value = int(os.getenv("DEFAULT_INTERVAL_MINUTES", "15"))
    except ValueError:
        value = 15
    return max(1, min(value, 1440))


def max_posts_per_check() -> int:
    """Anti-flood cap: max posts delivered per feed per check cycle."""
    try:
        value = int(os.getenv("MAX_POSTS_PER_CHECK", "5"))
    except ValueError:
        value = 5
    return max(1, min(value, 25))


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "bot.db")
