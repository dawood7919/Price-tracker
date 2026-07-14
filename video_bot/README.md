# Video Download Bot

A personal-use Telegram bot that downloads video/audio from **authorized sources only**
and sends the result back to its owner in Telegram.

## Authorization model

This bot enforces authorization automatically. There's no way to make it download
content that doesn't pass one of these three checks:

1. **Domain allowlist** — the URL's domain is in `ALLOWED_DOMAINS` (default:
   `archive.org`, whose ToS explicitly permits downloading). Add more domains
   yourself only if you've confirmed the site permits it.
2. **Your own YouTube channel** — the video's `channel_id` (from yt-dlp metadata)
   matches one of the IDs in `OWNED_YOUTUBE_CHANNEL_IDS`.
3. **Verified Creative Commons** — for other YouTube videos, the bot calls the
   YouTube Data API (`videos.list`, `status.license`) and only proceeds if the
   video is licensed Creative Commons. Requires `YOUTUBE_API_KEY`; without it,
   this check is always skipped (fails closed, not open).

Anything else is rejected with an explanation.

The bot only responds to its configured `OWNER_TELEGRAM_ID` — everyone else gets
"This is a private bot." It is not a public redistribution service.

## Commands

- `/start` — welcome message
- `/help` — how to use the bot
- `/about` — authorization policy

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `OWNER_TELEGRAM_ID` — your numeric Telegram user ID, from
     [@userinfobot](https://t.me/userinfobot)
   - `YOUTUBE_API_KEY` *(optional)* — from the
     [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com),
     needed only for the Creative Commons check
   - `OWNED_YOUTUBE_CHANNEL_IDS` *(optional)* — comma-separated channel IDs you own
   - `ALLOWED_DOMAINS` *(optional)* — comma-separated domains, defaults to `archive.org`
2. Install dependencies (requires `ffmpeg` on your system for audio extraction/merging):
   ```bash
   pip install -r requirements.txt
   ```
3. Run:
   ```bash
   python main.py
   ```

## Deployment (Render / Koyeb, Docker-based)

This project ships with a `Dockerfile` because yt-dlp needs the `ffmpeg` binary,
which isn't installable via `pip` alone.

1. Push this repo to GitHub.
2. On Render (or Koyeb), create a new **Web Service** from this repo, root
   directory `video_bot/`, and let it detect the `Dockerfile`.
3. Add all variables from `.env.example` under **Environment**.
4. Deploy. The bot binds an HTTP health-check server on `$PORT` (set
   automatically by the platform) purely to satisfy the platform's health
   check — the bot itself talks to Telegram via polling, not HTTP.

### Known limits

- **Telegram Bot API upload cap**: bots can upload files up to 50MB through the
  standard Bot API. `MAX_VIDEO_SIZE_MB` defaults to 50 for this reason — larger
  downloads are deleted and rejected with a clear error instead of failing
  silently at upload time. Sending larger files requires self-hosting the
  [Telegram Bot API server](https://github.com/tdlib/telegram-bot-api), which is
  out of scope for this deployment.
- **yt-dlp needs updates**: sites change their pages periodically, which can
  break extraction. If downloads start failing, try `pip install -U yt-dlp`
  first before assuming the bot's code is broken.
- **Ephemeral filesystem**: on Render/Koyeb free plans, `video_bot.db` (download
  history) and any temp files are wiped on redeploy/restart. Persist a volume,
  or migrate to a managed database, if history needs to survive restarts.

## Architecture

```
video_bot/
├── main.py                    # entrypoint: health-check server, handler registration, polling
├── config.py                   # loads and validates .env
├── bot/
│   ├── handlers.py              # /start /help /about, URL handler, callback query handler
│   ├── messages.py              # user-facing text templates
│   └── keyboards.py             # inline keyboard for quality selection
├── downloaders/
│   ├── manager.py                # validates, dispatches, orchestrates authorization + download
│   ├── authorization.py          # the 3 authorization checks — the only gate that matters
│   └── ytdlp_engine.py            # shared yt-dlp wrapper (extract metadata / download)
├── services/
│   ├── downloader_service.py      # runs downloads off the event loop, progress, size limit, cleanup
│   └── telegram_upload.py         # sends video/audio + thumbnail + caption
├── database/
│   ├── models.py                   # schema: users, download_history, settings, statistics
│   └── repository.py               # sqlite3 CRUD
├── utils/
│   ├── validators.py                # URL parsing, size/duration formatting
│   └── logging_config.py            # console + rotating file logging
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

Adding a new authorized domain never requires a code change — just add it to
`ALLOWED_DOMAINS`. Adding a new authorization *method* (beyond the three above)
is the only thing that requires touching `downloaders/authorization.py`, by design.
