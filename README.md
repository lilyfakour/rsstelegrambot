# Telegram RSS Reader Bot

A self-hosted Telegram bot that turns any RSS/Atom feed into beautiful
rich cards delivered straight to your chat — with images, excerpts and an
"Open post" button.

## Features

- ➕ **Subscribe to anything** — `/add` accepts a direct feed URL *or* just a
  website URL (the bot auto-detects the RSS/Atom link in the page).
- 🃏 **Rich cards** — new posts arrive as photo cards (thumbnail extracted from
  `media:thumbnail`, enclosures or the post body) with a bold title, a text
  excerpt, timestamp and an inline **Open post** button. If a post has no
  image, a clean text card is sent instead.
- 🔎 **Feed search** — `/find <topic>` searches a catalogue of millions of
  existing feeds (Feedly index). For any topic you can also subscribe to a
  live **Google News** stream with one tap.
- ⏱ **Adjustable update period** — `/interval 30` sets how often feeds are
  checked (per user, applied instantly, no restart).
- ⏸ **Pause / resume** — temporarily mute a feed without losing its history.
- 📦 **OPML portability** — `/export` downloads your subscriptions as OPML,
  `/import` restores them (just send the .opml file to the bot).
- 🛡 **Whitelist access** — only Telegram IDs listed in `ALLOWED_IDS` can use
  the bot; anyone else is politely told their ID so you can add them.
- 💾 **Durable storage** — a Turso (libSQL) database; seen-post history
  survives restarts/redeploys, and `ETag`/`If-Modified-Since` keep polling
  gentle on feed servers.

## Deploying (Vercel, webhook mode)

This version runs as a serverless webhook app instead of a long-running
polling process, so it can be hosted for free on Vercel. See **[DEPLOY.md](DEPLOY.md)**
for the full walkthrough (Turso database setup, environment variables,
pointing Telegram's webhook at your deployment, and scheduling the feed
checks). The short version:

```bash
npm i -g vercel
vercel --prod
# then, after setting env vars in the Vercel dashboard:
curl "https://<your-app>.vercel.app/api/setup?token=<CRON_SECRET>"
```

## Running it locally (for development)

```bash
cp .env.example .env   # fill in BOT_TOKEN, ALLOWED_IDS, TURSO_*, secrets
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```

Telegram needs a public HTTPS URL to send webhook updates to, so for local
testing you'll also want a tunnel (e.g. `ngrok http 8000`), then run
`python scripts/set_webhook.py set https://<ngrok-url>`.

## Commands

| Command | Description |
|---|---|
| `/start`, `/help` | Welcome & command reference |
| `/add <url>` | Subscribe to a feed (feed URL or website URL) |
| `/list` | Your feeds with status, IDs and intervals |
| `/remove <id>` | Unsubscribe |
| `/pause <id>` / `/resume <id>` | Mute / unmute a feed |
| `/find <topic>` | Search for feeds + one-tap Google News feed |
| `/interval` | Show current update period |
| `/interval <minutes>` | Set update period (1–1440 min) |
| `/export` | Download subscriptions as OPML |
| `/import` | Attach an `.opml` file (or just send the file) |

## How delivery works

- A scheduler ticks every 60 seconds and checks each active feed when its
  interval has elapsed (default **15 minutes**).
- Posts are de-duplicated by GUID; the first fetch after `/add` is a silent
  baseline so you only receive *new* posts.
- Anti-flood: at most `MAX_POSTS_PER_CHECK` (default 5) newest posts are
  delivered per feed per cycle; anything older is marked as seen silently.
- If a feed serves a photo, the card is sent via `sendPhoto` with the story in
  the caption; otherwise a formatted text card is sent. Broken image URLs
  automatically fall back to the text card.
- Failed feeds keep a short error note visible in `/list` and are retried on
  the next cycle.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bot replies "not authorized" | Add your Telegram ID to `ALLOWED_IDS` in the env vars |
| Bot doesn't respond at all | Check `GET /api/setup` output for Telegram's last delivery error, and the Vercel function logs |
| New posts never arrive | Make sure something is actually calling `/api/cron` on a schedule — see step 5 in DEPLOY.md |
| "No RSS/Atom feed found" | Open the site, copy the feed link (often `/feed`, `/rss`, `/atom.xml`) and `/add` that |
| Posts arrive without photos | That post has no extractable image; text card is used |
| Feed shows an error in `/list` | Temporary network/server issue — it retries automatically |

## Project layout

```
api/index.py     FastAPI entrypoint: /api/webhook, /api/cron, /api/setup
telegram_app.py  shared Bot/Dispatcher singletons
config.py        environment configuration (hot-reloaded whitelist)
db.py            Turso/libSQL storage layer (feeds, posts, settings)
feeds.py         HTTP fetching, feed parsing, discovery, image/excerpt extraction
cards.py         rich-card rendering (text + photo caption variants)
search.py        Feedly feed search + Google News topic feeds
opml_io.py       OPML export/import
scheduler.py     single-shot due-feed check, called from /api/cron
handlers.py      all commands, callbacks and whitelist gate
scripts/set_webhook.py   CLI alternative to /api/setup
vercel.json      routes everything to api/index.py
DEPLOY.md        full deployment walkthrough
```
