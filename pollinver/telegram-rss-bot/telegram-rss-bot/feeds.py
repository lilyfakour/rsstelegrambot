"""Feed fetching, parsing and helper extraction.

Responsibilities:
    - fetch()            async HTTP GET with conditional-request support
    - looks_like_feed()  sniff whether bytes are an RSS/Atom document
    - discover_feed()    given any website URL, find its RSS/Atom link(s)
    - entry_* helpers    title / link / date / excerpt / image extraction
"""
import hashlib
import html as html_lib
import re
import time
from urllib.parse import urljoin

import aiohttp
import feedparser
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; TelegramRSSBot/1.0; +https://core.telegram.org/bots)"
)

_FEED_LINK_TYPES = ("rss", "atom", "feed")


class FeedError(Exception):
    """Raised when a URL cannot be fetched or is not a valid feed."""


async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    timeout: int = 20,
) -> tuple[int, bytes, dict]:
    """GET a URL; returns (status, body_bytes, lowercased_header_dict)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/rss+xml, application/atom+xml, application/xml, "
            "text/xml, text/html;q=0.8, */*;q=0.5"
        ),
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    async with session.get(
        url,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
        allow_redirects=True,
    ) as resp:
        data = await resp.read()
        return resp.status, data, {k.lower(): v for k, v in resp.headers.items()}


def looks_like_feed(data: bytes) -> bool:
    """Heuristic check that the payload is an RSS/Atom document."""
    if not data:
        return False
    try:
        parsed = feedparser.parse(data)
    except Exception:
        return False
    if parsed.get("version"):
        return True
    return bool(parsed.get("entries")) and bool((parsed.get("feed") or {}).get("title"))


def parse_feed(data: bytes):
    return feedparser.parse(data)


async def discover_feed(session: aiohttp.ClientSession, url: str) -> list[str]:
    """Return candidate feed URLs for a given page/feed URL (max 5).

    If the URL itself is already a feed, returns [url].
    Otherwise scans the HTML <head> for <link rel="alternate"> hints.
    Raises FeedError when the URL cannot be fetched at all.
    """
    try:
        status, data, headers = await fetch(session, url, timeout=15)
    except Exception as exc:
        raise FeedError(f"Could not fetch the URL ({exc.__class__.__name__}).") from exc
    if status >= 400:
        raise FeedError(f"The server responded with HTTP {status}.")

    if looks_like_feed(data):
        return [url]

    try:
        soup = BeautifulSoup(data, "lxml")
    except Exception:
        soup = BeautifulSoup(data, "html.parser")

    candidates: list[str] = []
    seen: set[str] = set()
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        ltype = (link.get("type") or "").lower()
        if "alternate" in [r.lower() for r in rel] and any(
            t in ltype for t in _FEED_LINK_TYPES
        ):
            href = link.get("href")
            if not href:
                continue
            full = urljoin(url, href.strip())
            if full not in seen:
                seen.add(full)
                candidates.append(full)
    return candidates[:5]


# ------------------------------------------------------------ extraction ----

def strip_html(text: str) -> str:
    """Remove tags/scripts and collapse whitespace."""
    if not text:
        return ""
    try:
        soup = BeautifulSoup(text, "html.parser")
        for bad in soup(["script", "style"]):
            bad.decompose()
        raw = soup.get_text(" ")
    except Exception:
        raw = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", raw).strip()


def _entry_content_html(entry) -> str:
    for item in entry.get("content") or []:
        value = item.get("value")
        if value:
            return value
    return entry.get("summary") or entry.get("description") or ""


def entry_title(entry) -> str:
    title = strip_html(entry.get("title") or "")
    return title or "Untitled post"


def entry_link(entry) -> str:
    return (entry.get("link") or "").strip()


def entry_date(entry) -> str | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    try:
        return time.strftime("%b %d, %Y · %H:%M", st)
    except Exception:
        return None


def entry_guid(entry) -> str:
    guid = (entry.get("id") or entry.get("link") or "").strip()
    if guid:
        return guid
    base = (entry.get("title") or "") + str(entry.get("published_parsed") or "")
    return "hash:" + hashlib.md5(base.encode("utf-8", "ignore")).hexdigest()


def entry_excerpt(entry, limit: int = 280) -> str:
    text = strip_html(_entry_content_html(entry))
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind(" ")
    if boundary > limit * 0.6:
        cut = cut[:boundary]
    return cut.rstrip(" .,;:!?-–—") + "…"


def entry_image(entry) -> str | None:
    """Best-effort thumbnail extraction for rich cards."""
    # 1) media:thumbnail
    for thumb in entry.get("media_thumbnail") or []:
        url = (thumb.get("url") or "").strip()
        if url.startswith("http"):
            return url
    # 2) media:content
    for media in entry.get("media_content") or []:
        is_img = media.get("medium") == "image" or (
            media.get("type") or ""
        ).startswith("image")
        url = (media.get("url") or "").strip()
        if is_img and url.startswith("http"):
            return url
    # 3) enclosures
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and (link.get("type") or "").startswith(
            "image"
        ):
            url = (link.get("href") or "").strip()
            if url.startswith("http"):
                return url
    # 4) first <img src=...> inside the content HTML
    html_text = _entry_content_html(entry)
    match = re.search(
        r"<img[^>]+src=[\"']([^\"']+)[\"']", html_text, flags=re.IGNORECASE
    )
    if match:
        url = html_lib.unescape(match.group(1)).strip()
        if url.startswith("http"):
            return url
    return None
