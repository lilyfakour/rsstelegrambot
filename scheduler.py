"""Feed checking: finds due feeds and delivers new posts.

On Vercel there is no long-running process to host a "tick every 60
seconds" loop, so the old scheduler_loop() is gone. Instead, check_due_feeds()
below runs once per invocation and is triggered by /api/cron — see
DEPLOY.md for how to schedule that (Vercel Cron on Hobby only fires once a
day, so an external scheduler such as cron-job.org is the practical option
for the default 15-minute interval).

Design (unchanged from the original):
    - a feed is due when (now - last_checked) >= effective_interval * 60
      (effective interval = feed override, else per-user /interval setting)
    - new entries are delivered as rich cards (photo when possible)
    - anti-flood: at most MAX_POSTS_PER_CHECK newest posts per feed per cycle,
      everything older is silently marked as seen
"""
import asyncio
import logging
import time

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

import cards
import config
import db
import feeds

log = logging.getLogger("rss.scheduler")

_CONCURRENT_FEEDS = asyncio.Semaphore(3)


async def check_due_feeds(bot: Bot) -> int:
    """Check every due feed once. Returns how many feeds were checked."""
    now = int(time.time())
    due = await db.get_due_feeds(now)
    if not due:
        return 0
    log.info("Checking %d due feed(s)", len(due))
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(check_feed(bot, session, row) for row in due))
    return len(due)


async def check_feed(bot: Bot, session: aiohttp.ClientSession, feed: dict) -> None:
    async with _CONCURRENT_FEEDS:
        now = int(time.time())
        try:
            status, data, headers = await feeds.fetch(
                session,
                feed["url"],
                etag=feed["etag"] or None,
                last_modified=feed["last_modified"] or None,
            )

            if status == 304:  # Not Modified — nothing new
                await db.update_feed_check(
                    feed["id"], feed["etag"], feed["last_modified"], now, ""
                )
                return

            parsed = feeds.parse_feed(data)
            if parsed.get("bozo") and not parsed.get("entries"):
                raise feeds.FeedError("The URL did not return a valid feed.")

            entries = list(parsed.get("entries") or [])
            guids = [feeds.entry_guid(e) for e in entries]
            seen = await db.seen_guids(feed["id"])
            fresh = [(g, e) for g, e in zip(guids, entries) if g not in seen]

            # Remember everything now, deliver only the newest burst.
            await db.mark_seen(feed["id"], guids)
            # `fresh` is newest-first (feeds list newest first); send oldest first.
            to_send = list(reversed(fresh[: config.max_posts_per_check()]))
            for _guid, entry in to_send:
                await send_card(bot, feed, entry)

            await db.update_feed_check(
                feed["id"],
                headers.get("etag", ""),
                headers.get("last-modified", ""),
                now,
                "",
            )
        except Exception as exc:
            log.warning("Feed #%s (%s): %s", feed["id"], feed["url"], exc)
            await db.update_feed_check(
                feed["id"],
                feed["etag"],
                feed["last_modified"],
                now,
                f"{exc.__class__.__name__}: {exc}"[:250],
            )


async def send_card(bot: Bot, feed: dict, entry) -> None:
    post = {
        "title": feeds.entry_title(entry),
        "link": feeds.entry_link(entry),
        "excerpt": feeds.entry_excerpt(entry),
        "date": feeds.entry_date(entry),
        "image": feeds.entry_image(entry),
    }
    text, caption, image, keyboard = cards.render(feed["title"] or "RSS Feed", post)
    chat_id = feed["user_id"]

    if image:
        try:
            await bot.send_photo(
                chat_id,
                photo=image,
                caption=caption,
                reply_markup=keyboard,
                disable_notification=False,
            )
            return
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await bot.send_photo(
                    chat_id, photo=image, caption=caption, reply_markup=keyboard
                )
                return
            except Exception:
                pass
        except TelegramBadRequest:
            pass  # unreachable photo URL etc. — fall through to text card
        except Exception:
            pass

    await bot.send_message(chat_id, text=text, reply_markup=keyboard)
