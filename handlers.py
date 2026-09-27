"""All Telegram command and callback handlers (aiogram 3 routers)."""
import asyncio
import html as html_mod
import logging
import time

import aiohttp
import feedparser
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from urllib.parse import urlparse

import cards
import db
import feeds
import opml_io
import search
from config import get_allowed_ids

log = logging.getLogger("rss.handlers")

router = Router(name="main")
fallback_router = Router(name="fallback")

# In-memory caches so inline-button callbacks can find what the user saw.
# Keyed by chat id, pruned after 30 minutes.
CAND_CACHE: dict[int, dict] = {}   # /add: candidate feed URLs
SEARCH_CACHE: dict[int, dict] = {} # /find: search results
GNEWS_CACHE: dict[int, dict] = {}  # /find: google news topic feed URL

# --------------------------------------------------------------- helpers ----

HELP_TEXT = (
    "📖 <b>Commands</b>\n"
    "/add &lt;url&gt; — subscribe (feed URL or a website URL, auto-detected)\n"
    "/list — show your feeds\n"
    "/remove &lt;id&gt; — unsubscribe\n"
    "/pause &lt;id&gt; — stop updates for a feed\n"
    "/resume &lt;id&gt; — resume updates\n"
    "/find &lt;topic&gt; — search the web for RSS feeds\n"
    "/interval — view or set the update period\n"
    "/export — download your subscriptions as OPML\n"
    "/import — attach an .opml file (or just send me the file)\n\n"
    "🃏 New posts arrive as rich cards with images and an "
    "“Open post” button."
)


def esc(value) -> str:
    return html_mod.escape(str(value or ""), quote=True)


def trunc(value, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def args_of(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def normalize_url(raw: str) -> str | None:
    raw = raw.strip().strip("<>").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        if raw.startswith("ftp://") or raw.startswith("file://"):
            return None
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return raw


def _prune_cache(cache: dict, ttl: int = 1800) -> None:
    now = time.time()
    for key in [k for k, v in cache.items() if now - v.get("ts", 0) > ttl]:
        cache.pop(key, None)


def _safe_edit(message: Message, text: str, **kwargs) -> None:
    try:
        message.bot  # ensure bound
        asyncio.create_task(_do_edit(message, text, **kwargs))
    except Exception:
        pass


async def _do_edit(message: Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest:
        pass


# -------------------------------------------------------------- whitelist ----


class Authorized(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user) and user.id in get_allowed_ids()


router.message.filter(Authorized())
router.callback_query.filter(Authorized())


@fallback_router.message()
async def not_authorized_message(message: Message) -> None:
    await message.answer(
        "⛔️ You are not authorized to use this bot.\n"
        f"Your Telegram ID: <code>{message.from_user.id}</code>\n"
        "Send it to the bot owner to be added to ALLOWED_IDS in .env."
    )


@fallback_router.callback_query()
async def not_authorized_callback(callback: CallbackQuery) -> None:
    await callback.answer("Not authorized.", show_alert=True)


# ----------------------------------------------------------- add helpers ----


async def add_feed_flow(
    session: aiohttp.ClientSession, target: Message, user_id: int, url: str
) -> None:
    """Validate a feed URL, store it (baseline = current posts marked seen),
    then confirm by editing `target`."""
    try:
        _status, data, _headers = await feeds.fetch(session, url, timeout=20)
        parsed = feeds.parse_feed(data)
        if parsed.get("bozo") and not parsed.get("entries"):
            raise feeds.FeedError("The URL did not return a valid feed.")
    except feeds.FeedError as exc:
        await _safe_edit(target, f"😕 {esc(exc)}")
        return
    except Exception as exc:
        await _safe_edit(
            target, f"😕 Network error while fetching the feed ({esc(exc.__class__.__name__)})."
        )
        return

    title = feeds.strip_html((parsed.feed or {}).get("title") or url)[:120]
    feed_id, created = await db.add_feed(user_id, url, title)
    if not created:
        await _safe_edit(
            target,
            f"ℹ️ You are already subscribed to <b>{esc(title)}</b> (#{feed_id}).\n"
            "See /list.",
        )
        return

    await db.mark_seen(feed_id, [feeds.entry_guid(e) for e in parsed.entries])
    interval = await db.get_interval(user_id)

    lines = [f"✅ <b>{esc(title)}</b> added as <b>#{feed_id}</b>."]
    if parsed.entries:
        lines.append(f"🆕 Latest post there: {esc(trunc(feeds.entry_title(parsed.entries[0]), 100))}")
    lines.append(f"⏱ New posts will arrive about every {interval} min (change with /interval).")
    await _safe_edit(target, "\n".join(lines))


# ------------------------------------------------------------- commands ----


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Telegram RSS Reader</b>\n\n"
        "Send me <code>/add &lt;url&gt;</code> to subscribe to any RSS/Atom feed — "
        "a website URL works too, I will detect its feed automatically.\n\n"
        f"{HELP_TEXT}"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("add"))
async def cmd_add(message: Message) -> None:
    raw = args_of(message)
    if not raw:
        await message.answer("Usage: <code>/add https://example.com</code>")
        return
    url = normalize_url(raw)
    if not url:
        await message.answer("Please send a valid http(s) URL.")
        return

    status = await message.answer("🔎 Looking for a feed…")
    async with aiohttp.ClientSession() as session:
        try:
            candidates = await feeds.discover_feed(session, url)
        except feeds.FeedError as exc:
            await _safe_edit(status, f"😕 {esc(exc)}")
            return

        if not candidates:
            await _safe_edit(
                status,
                "😕 No RSS/Atom feed found at that address.\n"
                "Try the direct feed URL — blogs usually expose it at "
                "<code>/feed</code>, <code>/rss</code> or <code>/atom.xml</code>.",
            )
        elif len(candidates) == 1:
            await add_feed_flow(session, status, message.from_user.id, candidates[0])
        else:
            lines = ["Multiple feeds found — pick one:"]
            buttons = []
            for i, cand in enumerate(candidates):
                lines.append(f"• <code>{esc(trunc(cand, 90))}</code>")
                buttons.append(
                    [InlineKeyboardButton(text=f"➕ Option {i + 1}", callback_data=f"cand:{i}")]
                )
            _prune_cache(CAND_CACHE)
            CAND_CACHE[message.chat.id] = {"items": candidates, "ts": time.time()}
            await _safe_edit(status, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("cand:"))
async def cb_candidate(callback: CallbackQuery) -> None:
    try:
        index = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Bad selection.", show_alert=True)
        return
    entry = CAND_CACHE.get(callback.message.chat.id) or {}
    items = entry.get("items", [])
    if index >= len(items):
        await callback.answer("Selection expired — run /add again.", show_alert=True)
        return
    await callback.answer()
    async with aiohttp.ClientSession() as session:
        await add_feed_flow(session, callback.message, callback.from_user.id, items[index])


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    rows = await db.list_feeds(message.from_user.id)
    if not rows:
        await message.answer(
            "📭 No feeds yet.\nAdd your first one: <code>/add https://example.com</code>\n"
            "Or search: <code>/find technology news</code>"
        )
        return

    lines: list[str] = ["📰 <b>Your feeds</b>", ""]
    for row in rows:
        state = "⏸ paused" if row["paused"] else "▶️ active"
        err = f"\n   ⚠️ {esc(trunc(row['last_error'], 90))}" if row["last_error"] else ""
        lines.append(
            f"<b>#{row['id']}</b> · {state} · every {row['eff_interval']} min\n"
            f"<b>{esc(trunc(row['title'] or row['url'], 70))}</b>\n"
            f"<code>{esc(trunc(row['url'], 85))}</code>{err}"
        )
        lines.append("")

    text = "\n".join(lines)
    for chunk in _chunks(text, 3800):
        await message.answer(chunk)


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            parts.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current)
    return parts


async def _feed_by_id(message: Message, raw: str):
    try:
        feed_id = int(raw.split()[0])
    except (ValueError, IndexError):
        await message.answer("Usage: <code>/remove 3</code> — see /list for IDs.")
        return None
    row = await db.get_feed(message.from_user.id, feed_id)
    if not row:
        await message.answer(f"No feed with ID <b>#{feed_id}</b> — check /list.")
        return None
    return row


@router.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    row = await _feed_by_id(message, args_of(message))
    if row is None:
        return
    await db.remove_feed(row["id"])
    await message.answer(f"🗑 Removed <b>{esc(row['title'] or row['url'])}</b> (#{row['id']}).")


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    row = await _feed_by_id(message, args_of(message))
    if row is None:
        return
    await db.set_paused(row["id"], True)
    await message.answer(f"⏸ Paused <b>{esc(row['title'] or row['url'])}</b> (#{row['id']}).")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    row = await _feed_by_id(message, args_of(message))
    if row is None:
        return
    await db.set_paused(row["id"], False)
    await message.answer(f"▶️ Resumed <b>{esc(row['title'] or row['url'])}</b> (#{row['id']}).")


@router.message(Command("interval"))
async def cmd_interval(message: Message) -> None:
    raw = args_of(message)
    if not raw:
        current = await db.get_interval(message.from_user.id)
        await message.answer(
            f"⏱ Current update period: <b>{current} min</b>.\n\n"
            "Change it: <code>/interval 30</code> (allowed: 1–1440 minutes).\n"
            "The scheduler re-reads this value on the next cycle — no restart needed."
        )
        return
    try:
        minutes = int(raw.split()[0])
    except ValueError:
        await message.answer("Usage: <code>/interval 30</code>")
        return
    if not 1 <= minutes <= 1440:
        await message.answer("Please choose a value between 1 and 1440 minutes.")
        return
    await db.set_interval(message.from_user.id, minutes)
    await message.answer(f"✅ Update period set to <b>{minutes} min</b>.")


# ---------------------------------------------------------------- /find ----


@router.message(Command("find"))
async def cmd_find(message: Message) -> None:
    query = args_of(message)
    if not query:
        await message.answer(
            '🔎 Usage: <code>/find python news</code>\n'
            "I search a catalogue of existing feeds, and can also build a "
            "live Google News feed for any topic."
        )
        return
    if query.lower().startswith(("http://", "https://")) or "www." in query.lower():
        await message.answer("That looks like a URL — use <code>/add</code> for it.")
        return

    status = await message.answer("🔎 Searching for feeds…")
    async with aiohttp.ClientSession() as session:
        results = await search.search_feedly(session, query, count=6)

    _prune_cache(GNEWS_CACHE)
    gnews = search.google_news_url(query)
    GNEWS_CACHE[message.chat.id] = {"url": gnews, "ts": time.time()}

    lines = [f'🔎 <b>Results for “{esc(query)}”</b>', ""]
    buttons: list[list[InlineKeyboardButton]] = []

    if results:
        for i, item in enumerate(results):
            subs = search.format_subscribers(item["subscribers"])
            meta = f" · 👥 {subs} subscribers" if subs else ""
            lines.append(
                f"<b>{i + 1}. {esc(trunc(item['title'], 60))}</b>{meta}\n"
                f"<i>{esc(trunc(item['description'], 110))}</i>\n"
                f"<code>{esc(trunc(item['url'], 85))}</code>"
            )
            lines.append("")
            buttons.append(
                [InlineKeyboardButton(text=f"➕ Add #{i + 1}", callback_data=f"fadd:{i}")]
            )
    else:
        lines.append("😕 No catalogued feeds found for that topic.")
        lines.append("")

    lines.append("Or subscribe to a live Google News stream for this topic:")
    buttons.append(
        [InlineKeyboardButton(text=f"📰 Google News: {trunc(query, 22)}", callback_data="gnadd")]
    )

    _prune_cache(SEARCH_CACHE)
    SEARCH_CACHE[message.chat.id] = {"items": results, "ts": time.time()}
    await _safe_edit(
        status, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("fadd:"))
async def cb_search_add(callback: CallbackQuery) -> None:
    try:
        index = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Bad selection.", show_alert=True)
        return
    entry = SEARCH_CACHE.get(callback.message.chat.id) or {}
    items = entry.get("items", [])
    if index >= len(items):
        await callback.answer("Selection expired — run /find again.", show_alert=True)
        return
    await callback.answer()
    async with aiohttp.ClientSession() as session:
        await add_feed_flow(session, callback.message, callback.from_user.id, items[index]["url"])


@router.callback_query(F.data == "gnadd")
async def cb_gnews_add(callback: CallbackQuery) -> None:
    entry = GNEWS_CACHE.get(callback.message.chat.id) or {}
    url = entry.get("url")
    if not url:
        await callback.answer("Selection expired — run /find again.", show_alert=True)
        return
    await callback.answer()
    async with aiohttp.ClientSession() as session:
        await add_feed_flow(session, callback.message, callback.from_user.id, url)


# ----------------------------------------------------------- OPML io ----


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    rows = await db.list_feeds(message.from_user.id)
    if not rows:
        await message.answer("📭 You have no feeds to export yet.")
        return
    xml = opml_io.build_opml([(r["title"], r["url"]) for r in rows])
    document = BufferedInputFile(xml, filename="rss-feeds.opml")
    await message.answer_document(document, caption=f"📦 {len(rows)} feed(s) exported.")


async def _run_import(message: Message, document) -> None:
    downloaded = await message.bot.download(document)
    if downloaded is None:
        await message.answer("😕 Could not download the file. Try again.")
        return
    try:
        entries = opml_io.parse_opml(downloaded.read())
    except Exception:
        await message.answer("😕 Could not parse the file as OPML/XML.")
        return
    if not entries:
        await message.answer("😕 No feed URLs (xmlUrl attributes) found in the file.")
        return

    entries = entries[:50]
    status = await message.answer(f"📦 Found {len(entries)} feed(s) — importing (this can take a moment)…")

    added = dup = bad = 0

    async def one(session: aiohttp.ClientSession, url: str) -> str:
        try:
            _s, data, _h = await feeds.fetch(session, url, timeout=15)
            parsed = feeds.parse_feed(data)
            if parsed.get("bozo") and not parsed.get("entries"):
                return "bad"
        except Exception:
            return "bad"
        title = feeds.strip_html((parsed.feed or {}).get("title") or url)[:120]
        feed_id, created = await db.add_feed(message.from_user.id, url, title)
        if not created:
            return "dup"
        await db.mark_seen(feed_id, [feeds.entry_guid(e) for e in parsed.entries])
        return "added"

    sem = asyncio.Semaphore(4)
    async with aiohttp.ClientSession() as session:

        async def guarded(url: str) -> str:
            async with sem:
                return await one(session, url)

        outcomes = await asyncio.gather(*(guarded(u) for _t, u in entries))

    for outcome in outcomes:
        if outcome == "added":
            added += 1
        elif outcome == "dup":
            dup += 1
        else:
            bad += 1

    await _safe_edit(
        status,
        f"✅ Import finished — added: <b>{added}</b>, already subscribed: <b>{dup}</b>, "
        f"invalid/unreachable: <b>{bad}</b>.",
    )


@router.message(Command("import"))
async def cmd_import(message: Message) -> None:
    if message.reply_to_message and message.reply_to_message.document:
        await _run_import(message, message.reply_to_message.document)
        return
    await message.answer(
        "📎 To import, attach an <b>.opml</b> file — either together with the /import "
        "command as its caption, or simply send me the file directly. "
        "Export yours anytime with /export."
    )


@router.message(F.document)
async def on_document(message: Message) -> None:
    """Accept OPML files sent with or without the /import command."""
    document = message.document
    name = (document.file_name or "").lower()
    if name.endswith((".opml", ".xml")):
        await _run_import(message, document)
    else:
        await message.answer(
            "🤔 I only accept <b>.opml</b> files for import (see /help)."
        )
