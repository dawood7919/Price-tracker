# Prebuilt telegram-bot-api binary (Local Bot API Server, lifts the 50MB cap to
# 2GB). This image is Alpine (musl-libc) based, so the final stage below must
# also be musl-based — copying this binary into a glibc image (e.g.
# python:3.12-slim) fails with "cannot execute: required file not found"
# (missing musl dynamic linker). Building telegram-bot-api from source instead
# was tried and is too RAM/time-heavy for typical Render build machines (tdlib
# compilation needs 1-2GB+ per translation unit and can hang/OOM well before
# finishing) — matching this image's libc is the lightweight fix.
FROM aiogram/telegram-bot-api:latest AS botapi

FROM python:3.12-alpine

# ffmpeg/ffprobe: merging, audio extraction and splitting
# aria2: multi-connection external downloader for max download speed
# curl: used by start.sh to wait for the local Bot API server to come up
# bash: start.sh uses bash-only syntax (/dev/tcp health check)
# chromium: headless "print to PDF" for /pdf — Alpine's own build (musl),
# not Playwright's bundled Chromium, which only ships glibc binaries and
# would hit the exact same "cannot execute: required file not found"
# mismatch the telegram-bot-api binary did before it was matched to Alpine.
RUN apk add --no-cache ffmpeg aria2 curl bash chromium

ENV CHROMIUM_PATH=/usr/bin/chromium-browser

COPY --from=botapi /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY main.py .
COPY start.sh .
RUN chmod +x start.sh

# Non-root user (downloads live in /tmp which stays writable)
RUN adduser -D appuser \
    && mkdir -p /app/botapi-data /app/state \
    && chown -R appuser:appuser /app/botapi-data /app/state
USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["./start.sh"]
