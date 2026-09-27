"""Shared Bot/Dispatcher setup for the serverless (webhook) deployment.

api/index.py imports get_bot()/get_dispatcher() from here rather than
building them itself, so both the /api/webhook and /api/cron routes reuse
the exact same instances within a warm serverless container instead of
each recreating aiogram's Bot/Dispatcher (and its aiohttp session) per call.
"""
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
import handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not config.BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN is not set — add it in the Vercel project settings.")
        _bot = Bot(
            token=config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        _dp.include_routers(handlers.router, handlers.fallback_router)
    return _dp
