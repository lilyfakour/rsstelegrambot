"""Configuration loading for the Telegram RSS bot.

Values are read from environment variables (set in the Vercel project
settings in production; from a local .env file for local testing).
ALLOWED_IDS is re-read on every call so you can add a user without
redeploying.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def get_allowed_ids() -> set[int]:
    """Read ALLOWED_IDS from the environment on every call.

    This makes it possible to authorize a new user by editing the env var
    (and redeploying, or just editing it in the Vercel dashboard — no code
    change needed). Accepts comma or semicolon separated IDs.
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

# --- Persistent storage (Turso / libSQL) -----------------------------------
# Vercel's filesystem is read-only (apart from an ephemeral /tmp that is
# wiped between invocations and not shared across instances), so a local
# SQLite *file* cannot be used here. Turso is libSQL — the same SQL dialect
# as SQLite — reached over the network, which is why db.py looks almost
# identical to a normal aiosqlite version.
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# --- Webhook / cron security -------------------------------------------
# Telegram sends this back on every webhook request (as the
# X-Telegram-Bot-Api-Secret-Token header) so we can reject anything that
# didn't come from Telegram. Generate any random string for it.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

# Shared secret for the /api/cron endpoint. Whoever triggers the periodic
# feed check (Vercel Cron, or an external scheduler like cron-job.org) must
# send it as "Authorization: Bearer <CRON_SECRET>".
CRON_SECRET = os.getenv("CRON_SECRET", "")
