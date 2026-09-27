"""Telegram RSS Reader bot — entry point.

Usage:
    1. Copy .env.example to .env and fill in BOT_TOKEN and ALLOWED_IDS
    2. pip install -r requirements.txt
    3. python bot.py
"""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
import db
import handlers
import scheduler


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    if not config.BOT_TOKEN:
        print(
            "ERROR: BOT_TOKEN is not set.\n"
            "Copy .env.example to .env, paste the token from @BotFather, "
            "put your Telegram ID in ALLOWED_IDS and run again."
        )
        sys.exit(1)

    await db.init_db()
    log = logging.getLogger("rss.bot")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_routers(handlers.router, handlers.fallback_router)

    scheduler_task = asyncio.create_task(scheduler.scheduler_loop(bot))

    me = await bot.get_me()
    log.info("Bot @%s started — polling for updates.", me.username)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
