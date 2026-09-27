"""Single serverless entrypoint for Vercel.

vercel.json rewrites every request to this file, and this FastAPI app does
its own internal routing — that way there's one Python function (one cold
start, one Bot/Dispatcher instance) instead of one function per route.

Routes:
    POST /api/webhook  - Telegram sends updates here (set via set_webhook.py)
    GET|POST /api/cron - triggered periodically to check due feeds
                         (Vercel Cron and/or an external scheduler)
    GET  /api/setup    - one-time helper: points Telegram's webhook at this
                         deployment (safe to call again after every deploy)
    GET  /             - plain health check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

from aiogram.types import Update
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

import config
import scheduler
from telegram_app import get_bot, get_dispatcher

log = logging.getLogger("rss.api")

app = FastAPI()


@app.get("/")
async def health() -> dict:
    return {"ok": True, "service": "telegram-rss-bot"}


@app.post("/api/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    if config.TELEGRAM_WEBHOOK_SECRET and (
        x_telegram_bot_api_secret_token != config.TELEGRAM_WEBHOOK_SECRET
    ):
        return JSONResponse({"ok": False, "error": "bad secret token"}, status_code=401)

    bot = get_bot()
    dp = get_dispatcher()
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    try:
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("Error while handling update")
    # Always 200 — returning an error to Telegram just triggers retries of
    # an update we've already accepted.
    return JSONResponse({"ok": True})


def _cron_authorized(authorization: str | None) -> bool:
    if not config.CRON_SECRET:
        # No secret configured: allow it, but this endpoint is then
        # unauthenticated — set CRON_SECRET before going live.
        return True
    return authorization == f"Bearer {config.CRON_SECRET}"


@app.api_route("/api/cron", methods=["GET", "POST"])
async def cron(authorization: str | None = Header(default=None)) -> JSONResponse:
    if not _cron_authorized(authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    bot = get_bot()
    checked = await scheduler.check_due_feeds(bot)
    return JSONResponse({"ok": True, "feeds_checked": checked})


@app.get("/api/setup")
async def setup(
    request: Request, token: str | None = None, base_url: str | None = None
) -> JSONResponse:
    """Point Telegram's webhook at this deployment.

    Call this once after your first deploy (and again any time the deployment
    URL changes): https://<your-app>.vercel.app/api/setup?token=<CRON_SECRET>
    Re-uses CRON_SECRET as a simple guard so this isn't a public button
    anyone can mash to repoint your bot's webhook. Pass ?base_url=... to
    override the auto-detected host, e.g. if you're behind a custom domain
    and the auto-detected one is wrong.
    """
    if config.CRON_SECRET and token != config.CRON_SECRET:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    if not base_url:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        base_url = f"https://{host}"
    webhook_url = f"{base_url.rstrip('/')}/api/webhook"
    bot = get_bot()
    ok = await bot.set_webhook(
        webhook_url,
        secret_token=config.TELEGRAM_WEBHOOK_SECRET or None,
        drop_pending_updates=False,
    )
    info = await bot.get_webhook_info()
    return JSONResponse({"ok": ok, "webhook_url": webhook_url, "info": info.model_dump()})
