"""Search system for discovering RSS feeds.

Primary source: the public Feedly feed-search API (no key required) which
indexes millions of existing blogs and news feeds.

Always offered as an extra option: a live Google News topic feed —
news.google.com generates an RSS stream for any search query, so users can
subscribe to *anything* even if no dedicated feed exists.
"""
from urllib.parse import quote_plus

import aiohttp

from feeds import USER_AGENT

FEEDLY_ENDPOINT = "https://cloud.feedly.com/v3/search/feeds"


async def search_feedly(session: aiohttp.ClientSession, query: str, count: int = 6) -> list[dict]:
    """Search existing feeds. Never raises — returns [] on any failure."""
    try:
        async with session.get(
            FEEDLY_ENDPOINT,
            params={"query": query, "count": str(count)},
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except Exception:
        return []

    results: list[dict] = []
    for item in (data or {}).get("results", [])[:count]:
        feed_id = item.get("feedId") or ""
        url = feed_id[len("feed/"):] if feed_id.startswith("feed/") else feed_id
        if not url.startswith("http"):
            continue
        results.append(
            {
                "title": item.get("title") or url,
                "description": item.get("description") or "",
                "subscribers": item.get("subscribers") or 0,
                "website": item.get("website") or "",
                "url": url,
            }
        )
    return results


def google_news_url(query: str, lang: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    """A subscribable RSS feed of fresh news for any topic."""
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={lang}&gl={gl}&ceid={ceid}"
    )


def format_subscribers(count: int) -> str:
    if not count:
        return ""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)
