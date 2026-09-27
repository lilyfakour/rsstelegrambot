"""Rendering of rich post cards for Telegram.

A card consists of:
    - text    : full HTML message (used when no photo is available, 4096 limit)
    - caption : shorter HTML version (used as photo caption, 1024 limit)
    - image   : thumbnail URL or None
    - keyboard: inline button "Open post"
"""
import html as html_lib

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


def esc(value) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _keyboard(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔗 Open post", url=link)]]
    )


def _truncate_escaping(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _trim_excerpt(excerpt: str, prefix_len: int, limit: int) -> str:
    """Shrink the excerpt so that prefix + excerpt fits into limit."""
    if excerpt:
        room = limit - prefix_len - 5  # reserve for ellipsis/newlines
        if room < 40:
            return ""
        if len(excerpt) > room:
            cut = excerpt[:room]
            boundary = cut.rfind(" ")
            if boundary > room * 0.6:
                cut = cut[:boundary]
            return cut.rstrip(" .,;:!?-–—") + "…"
    return excerpt


def render(feed_title: str, post: dict) -> tuple[str, str, str | None, InlineKeyboardMarkup]:
    """Build (text, caption, image_url, keyboard) for a post."""
    feed = f"📰 <b>{esc(feed_title)}</b>"
    divider = "━━━━━━━━━━━━━━━"

    link = post.get("link") or ""
    if link:
        title_line = f'<b><a href="{esc(link)}">{esc(post.get("title"))}</a></b>'
    else:
        title_line = f"<b>{esc(post.get('title'))}</b>"

    date_line = f"🕒 {esc(post['date'])}" if post.get("date") else ""
    image = post.get("image")

    def assemble(excerpt: str) -> str:
        parts = [title_line]
        body = "\n".join(p for p in (excerpt, date_line) if p)
        if body:
            parts.append(body)
        return "\n\n".join(parts)

    # ---- caption (photo path, 1024 chars) ----
    cap_head = f"{feed}\n{divider}\n"
    cap_excerpt = _trim_excerpt(esc(post.get("excerpt")), len(cap_head) + 5, CAPTION_LIMIT)
    caption = cap_head + assemble(cap_excerpt)
    caption = _truncate_escaping(caption, CAPTION_LIMIT)

    # ---- full text (no-photo path, 4096 chars) ----
    txt_head = f"{feed}\n{divider}\n"
    txt_excerpt = _trim_excerpt(esc(post.get("excerpt")), len(txt_head) + 5, TEXT_LIMIT)
    text = txt_head + assemble(txt_excerpt)
    text = _truncate_escaping(text, TEXT_LIMIT)

    return text, caption, image, _keyboard(link) if link else None
