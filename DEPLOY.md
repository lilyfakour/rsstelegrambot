# Deploying to Vercel (webhook mode)

This version of the bot has been restructured to run as a serverless app on
Vercel instead of a long-running polling process. Two things had to change
to make that possible, and both need a bit of setup before it'll work:

1. **No background loop.** Vercel functions only run in response to a
   request; there's no process left running to tick every 60 seconds. The
   feed-checking scheduler is now triggered by hitting `/api/cron` — you
   need to point *something* at that URL on a schedule (see step 4).
2. **No local file storage.** Vercel's filesystem is read-only except for
   an ephemeral `/tmp` that's wiped between requests and not shared across
   instances, so the SQLite file (`bot.db`) can't live there. Storage moved
   to [Turso](https://turso.tech) — same SQL as SQLite, reached over the
   network, with a free tier that's plenty for personal use (see step 1).

## 1. Create a Turso database

```bash
# install the CLI (macOS/Linux)
curl -sSfL https://get.tur.so/install.sh | bash
turso auth login

turso db create telegram-rss-bot
turso db show telegram-rss-bot --url        # -> TURSO_DATABASE_URL
turso db tokens create telegram-rss-bot      # -> TURSO_AUTH_TOKEN
```

You don't need to create any tables by hand — `db.py` creates them
automatically the first time the app runs.

## 2. Generate two secrets

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice
```

Use one for `TELEGRAM_WEBHOOK_SECRET` and the other for `CRON_SECRET`.

## 3. Deploy to Vercel

```bash
npm i -g vercel     # if you don't have it already
cd telegram-rss-bot
vercel               # first deploy — creates the project
vercel --prod        # promote to production
```

Then, in the Vercel dashboard → your project → **Settings → Environment
Variables**, add (for Production, and Preview if you use it):

| Variable | Value |
|---|---|
| `BOT_TOKEN` | from @BotFather |
| `ALLOWED_IDS` | your Telegram user ID (comma-separated for more than one) |
| `TURSO_DATABASE_URL` | from step 1 |
| `TURSO_AUTH_TOKEN` | from step 1 |
| `TELEGRAM_WEBHOOK_SECRET` | from step 2 |
| `CRON_SECRET` | from step 2 |
| `DEFAULT_INTERVAL_MINUTES` | `15` (optional, this is the default) |
| `MAX_POSTS_PER_CHECK` | `5` (optional, this is the default) |

Redeploy after adding them (`vercel --prod`) so the function picks them up.

## 4. Point Telegram at your deployment

Open, in a browser or with `curl`:

```
https://<your-app>.vercel.app/api/setup?token=<CRON_SECRET>
```

This calls Telegram's `setWebhook` for you, pointing it at
`https://<your-app>.vercel.app/api/webhook`. You should see
`"ok": true` in the response. Re-run this any time your deployment URL
changes (custom domain, new project, etc.).

Message your bot `/start` — you should get a reply. If not, check
**Vercel → your project → Logs**, and `GET /api/setup` again to inspect
`info` in the response (Telegram reports its last delivery error there).

## 5. Schedule the feed check

This is the part that needs a workaround on the **Hobby** plan: Vercel Cron
on Hobby can only fire **once a day**, which defeats the point of a
15-minute default interval. (Pro allows per-minute schedules — if you're on
Pro, add a `crons` entry to `vercel.json` instead and skip the rest of this
step.)

The practical fix is a free external scheduler that just pings your URL:

1. Sign up at [cron-job.org](https://cron-job.org) (or any similar service).
2. Create a job:
   - URL: `https://<your-app>.vercel.app/api/cron`
   - Schedule: every 15 minutes (or whatever interval you want)
   - Add a custom header: `Authorization: Bearer <CRON_SECRET>`
3. Save it — you're done. Every time it fires, the bot checks all due
   feeds and delivers anything new.

You can test it manually first:

```bash
curl -H "Authorization: Bearer <CRON_SECRET>" https://<your-app>.vercel.app/api/cron
```

A `{"ok": true, "feeds_checked": N}` response means it's working.

## Notes on this architecture

- **Concurrency**: `/api/webhook` and `/api/cron` are the same Python
  function under the hood (`api/index.py`), so there's one cold start for
  both instead of two — `vercel.json` rewrites every request to it.
- **Timeouts**: the function is configured for `maxDuration: 60` in
  `vercel.json`. If you subscribe to a lot of feeds and the cron job starts
  timing out, raise that number (Hobby allows up to 300s, Pro up to 800s).
- **Local testing**: `uvicorn api.index:app --reload` runs the same FastAPI
  app locally; you'll need a tunnel (e.g. `ngrok http 8000`) for Telegram to
  reach it, since webhooks need a public HTTPS URL.
- **Nothing else changed** — commands, feed discovery, OPML import/export,
  card rendering etc. are all the same code as before.
