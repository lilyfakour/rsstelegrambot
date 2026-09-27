"""One-off CLI helper to manage the Telegram webhook.

You usually don't need this — hitting GET /api/setup on your deployment
does the same thing over HTTP. This script is here for cases where you'd
rather not expose that endpoint, or want to double check things locally.

Usage:
    python scripts/set_webhook.py set https://your-app.vercel.app
    python scripts/set_webhook.py info
    python scripts/set_webhook.py delete
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot

import config


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    if not config.BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not set (check your .env).")
        sys.exit(1)

    bot = Bot(token=config.BOT_TOKEN)
    try:
        if action == "set":
            if len(sys.argv) < 3:
                print("Usage: python scripts/set_webhook.py set https://your-app.vercel.app")
                sys.exit(1)
            base_url = sys.argv[2].rstrip("/")
            url = f"{base_url}/api/webhook"
            ok = await bot.set_webhook(
                url,
                secret_token=config.TELEGRAM_WEBHOOK_SECRET or None,
            )
            print(f"set_webhook({url}) -> {ok}")
        elif action == "info":
            info = await bot.get_webhook_info()
            print(info.model_dump_json(indent=2))
        elif action == "delete":
            ok = await bot.delete_webhook()
            print(f"delete_webhook() -> {ok}")
        else:
            print(__doc__)
            sys.exit(1)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
