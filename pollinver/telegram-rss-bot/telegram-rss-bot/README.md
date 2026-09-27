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
- 💾 **Zero-setup storage** — a single SQLite file; seen-post history survives
  restarts, and `ETag`/`If-Modified-Since` keep polling gentle on feed servers.

## Quick start

1. **Create the bot** — open [@BotFather](https://t.me/BotFather) in Telegram,
   send `/newbot`, follow the prompts and copy the token.

2. **Configure**

   ```bash
   cp .env.example .env
   ```

   Then edit `.env`:

   ```ini
   BOT_TOKEN=1234567890:AA...your-token...
   ALLOWED_IDS=123456789
   ```

   > Don't know your Telegram ID? Run the bot first and send it `/start` —
   > it will reply with your ID to whitelist. (You can also ask
   > [@userinfobot](https://t.me/userinfobot).)
   > `ALLOWED_IDS` is re-read on every message, so adding a friend needs no
   > restart.

3. **Install & run**

   ```bash
   python3 -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   python bot.py
   ```

That's it — the bot uses long polling, so it works behind NAT, on a laptop,
a Raspberry Pi or any cheap VPS.

### Running it in the background (Linux)

```bash
nohup python bot.py > rssbot.log 2>&1 &
```

or inside a `tmux`/`screen` session. To stop it: `pkill -f "python bot.py"`.

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
| Bot replies "not authorized" | Add your Telegram ID to `ALLOWED_IDS` in `.env` |
| `Conflict: terminated by other getUpdates` | Another copy of the bot is running — stop it |
| "No RSS/Atom feed found" | Open the site, copy the feed link (often `/feed`, `/rss`, `/atom.xml`) and `/add` that |
| Posts arrive without photos | That post has no extractable image; text card is used |
| Feed shows an error in `/list` | Temporary network/server issue — it retries automatically |

## Project layout

```
bot.py         entry point: wiring, polling, scheduler start
config.py      .env configuration (hot-reloaded whitelist)
db.py          SQLite layer (feeds, posts, settings)
feeds.py       HTTP fetching, feed parsing, discovery, image/excerpt extraction
cards.py       rich-card rendering (text + photo caption variants)
search.py      Feedly feed search + Google News topic feeds
opml_io.py     OPML export/import
scheduler.py   background loop checking due feeds
handlers.py    all commands, callbacks and whitelist gate
```
