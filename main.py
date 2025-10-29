import os
import json
import asyncio
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from flask import Flask, request
from threading import Thread
from telegram import Bot

# -----------------------
# Configuration
# -----------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # <- set on Render
CHAT_ID = int(os.environ.get("CHAT_ID", "-1003207645424"))
DEFAULT_THREAD_ID = int(os.environ.get("THREAD_ID", "10"))
EVENTS_FILE = Path("events.json")
SENT_FILE = Path("sent_records.json")
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "5"))  # window for sending (minutes)
SELF_PING_INTERVAL = int(os.environ.get("SELF_PING_INTERVAL", "300"))  # seconds (5 min)

if not BOT_TOKEN:
    raise SystemExit("Please set BOT_TOKEN environment variable")

bot = Bot(token=BOT_TOKEN)

# -----------------------
# Logging
# -----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bcl_bot")

# -----------------------
# Flask keep-alive (runs in separate thread)
# -----------------------
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def home():
    return "✅ Bot is alive and running!", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}, 200

def run_flask():
    port = int(os.environ.get("PORT", "8080"))
    # disable reloader, debug etc.
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def start_flask_thread():
    t = Thread(target=run_flask, daemon=True)
    t.start()
    log.info("Flask keep-alive started")

# -----------------------
# JSON utilities
# -----------------------
def load_json(path: Path, default=None):
    try:
        if not path.exists():
            return default if default is not None else []
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to load JSON %s: %s", path, e)
        return default if default is not None else []

def save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error("Failed to save JSON %s: %s", path, e)

def file_hash(path: Path):
    if not path.exists():
        return None
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()

# -----------------------
# Messaging helpers
# -----------------------
async def send_message(text: str, thread_id: int | None = None):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, message_thread_id=thread_id)
        log.info("Sent message to chat %s (thread=%s): %s", CHAT_ID, thread_id, text.replace("\n"," | ")[:200])
    except Exception as e:
        log.exception("Failed to send message: %s", e)

def format_pre_announcement(ev):
    # Template:
    # 📢 Event starts soon! (in 1 hour)
    # 🕓 AGB — 12:00 UTC
    # (KR)
    en = f"📢 Event starts soon! (in 1 hour)\n🕓 {ev['name_en']} — {ev['time']} UTC"
    kr = f"곧 이벤트가 시작됩니다! (1시간 후)\n🕓 {ev['name_kr']} — {ev['time']} UTC"
    return f"{en}\n\n{kr}"

def format_start_announcement(ev):
    en = f"🔥 Event {ev['name_en']} has started! Join now!"
    kr = f"{ev['name_kr']} 이벤트가 시작되었습니다! 지금 참여하세요!"
    return f"{en}\n\n{kr}"

# -----------------------
# Event model & scheduling
# -----------------------
# events.json uses:
# [
#   {
#     "name_en": "AGB",
#     "name_kr": "AGB",
#     "time": "12:00",
#     "days": ["Mon","Fri"],
#     "thread_id": 10
#   },
#   ...
# ]

def next_event_datetime_for_day(time_str: str, target_date: datetime.date):
    hh, mm = map(int, time_str.split(":"))
    return datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hh,
        minute=mm,
        second=0,
        microsecond=0,
    )

async def check_and_send_once(events):
    """Check events for *today* and send due messages within WINDOW_MINUTES window."""
    now = datetime.utcnow()
    weekday = now.strftime("%a")  # Mon, Tue, ...
    today_str = now.strftime("%Y-%m-%d")
    sent = load_json(SENT_FILE, {})
    if today_str not in sent:
        sent[today_str] = {}

    changed = False

    for ev in events:
        if weekday not in ev.get("days", []):
            continue

        name_key = ev.get("name_en", ev.get("name", "event"))
        thread_id = ev.get("thread_id", DEFAULT_THREAD_ID)
        event_dt = next_event_datetime_for_day(ev["time"], now.date())
        pre_dt = event_dt - timedelta(hours=1)

        sent_today = sent[today_str].get(name_key, [])

        # Pre-announcement (1 hour before)
        if pre_dt <= now <= pre_dt + timedelta(minutes=WINDOW_MINUTES) and "pre" not in sent_today:
            text = format_pre_announcement(ev)
            await send_message(text, thread_id)
            sent_today.append("pre")
            changed = True

        # Start announcement
        if event_dt <= now <= event_dt + timedelta(minutes=WINDOW_MINUTES) and "start" not in sent_today:
            text = format_start_announcement(ev)
            await send_message(text, thread_id)
            sent_today.append("start")
            changed = True

        if sent_today:
            sent[today_str][name_key] = sent_today

    if changed:
        save_json(SENT_FILE, sent)

# -----------------------
# Auto-ping self (keeps service alive on some hosts)
# -----------------------
async def self_ping_loop(public_url: str):
    if not public_url:
        log.warning("No PUBLIC_URL set; self-ping disabled")
        return
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(public_url, timeout=25) as resp:
                    log.info("Self-ping %s -> %s", public_url, resp.status)
            except Exception as e:
                log.warning("Self-ping failed: %s", e)
            await asyncio.sleep(SELF_PING_INTERVAL)

# -----------------------
# File watch loop to auto-reload events.json
# -----------------------
async def events_watch_loop():
    last_hash = file_hash(EVENTS_FILE)
    events = load_json(EVENTS_FILE, [])
    log.info("Loaded %d events", len(events))
    # initial immediate check (in case events are due now)
    await check_and_send_once(events)
    while True:
        await asyncio.sleep(60)  # check every minute
        current_hash = file_hash(EVENTS_FILE)
        if current_hash != last_hash:
            log.info("Detected change in events.json - reloading")
            events = load_json(EVENTS_FILE, [])
            log.info("Reloaded %d events", len(events))
            # clear old sent_records for future days? keep existing
            # run an immediate check so edits for near-future events are applied
            await check_and_send_once(events)
            last_hash = current_hash
        else:
            # regular scheduled check
            await check_and_send_once(events)

# -----------------------
# Main entry
# -----------------------
async def main_async():
    # Start Flask keep-alive thread
    start_flask_thread()

    # Determine public URL for self-ping (Render will set)
    PUBLIC_URL = os.environ.get("PUBLIC_URL")  # recommended to set on Render to https://<app>.onrender.com
    # If not set, attempt to build from RENDER_INTERNAL_HOST or leave disabled
    if not PUBLIC_URL:
        # Try common Render host pattern (not guaranteed). Better to set PUBLIC_URL in env on Render dashboard.
        render_service_name = os.environ.get("RENDER_SERVICE_NAME")
        render_account = os.environ.get("RENDER_ACCOUNT")
        # If not available, keep disabled.
    # start self-ping loop if PUBLIC_URL provided
    if PUBLIC_URL:
        asyncio.create_task(self_ping_loop(PUBLIC_URL))
    else:
        log.info("PUBLIC_URL not set; self-ping disabled. It's recommended to set PUBLIC_URL environment var to your https URL.")

    # Start events watch loop (this includes periodic checks)
    await events_watch_loop()

def main():
    log.info("Starting bot main")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    except Exception:
        log.exception("Unhandled exception in main()")

if __name__ == "__main__":
    main()
