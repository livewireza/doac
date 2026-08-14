#!/usr/bin/env python3
"""
TheDiaryOfACEO — daily YouTube digest.

Fetches the latest 5 videos, skips any already seen, extracts wisdom via
Fabric, then emails a digest through Mailgun.  The set of processed video IDs
is persisted in seen_videos.json alongside this script.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi

# ── Config (all secrets come from environment / GitHub Actions secrets) ───────
CHANNEL_URL  = "https://www.youtube.com/@TheDiaryOfACEO/videos"
PLAYLIST_END = 5
STATE_FILE   = Path(__file__).parent / "seen_videos.json"

MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN  = os.getenv("MAILGUN_DOMAIN", "")
MAILGUN_TO      = os.getenv("MAILGUN_TO", "")
MAILGUN_FROM    = os.getenv("MAILGUN_FROM", "doac-digest@example.com")

# ── State persistence ─────────────────────────────────────────────────────────

def load_seen() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))

# ── Fetch latest videos via yt-dlp ────────────────────────────────────────────

def fetch_latest_videos() -> list:
    """Return up to PLAYLIST_END videos as dicts with id/title/date/url keys."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(PLAYLIST_END),
        "--print", "%(upload_date)s\t%(id)s\t%(title)s\t%(webpage_url)s",
        CHANNEL_URL,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)

    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            upload_date, vid_id, title, url = parts
            videos.append({"id": vid_id, "title": title, "date": upload_date, "url": url})
            print(f"  {upload_date} — {title}: {url}")
    return videos

# ── Summarise via yt + Fabric ─────────────────────────────────────────────────

def get_transcript(url: str) -> str:
    video_id = parse_qs(urlparse(url).query).get("v", [None])[0]
    if not video_id:
        raise ValueError(f"Could not extract video ID from {url}")
    entries = YouTubeTranscriptApi().fetch(video_id)
    return " ".join(e.text for e in entries)


def summarise(url: str) -> str:
    """Pipe a video transcript through Fabric's extract_wisdom pattern."""
    transcript_text = get_transcript(url)
    wisdom = subprocess.run(
        ["fabric", "--pattern", "extract_wisdom"],
        input=transcript_text,
        capture_output=True, text=True, check=True,
    )
    return wisdom.stdout.strip()

# ── Email via Mailgun ─────────────────────────────────────────────────────────
# Adapted from https://github.com/livewireza/cbbh-exam-check/blob/main/check_htb_exam.py

def mailgun_send(subject: str, text: str):
    if not MAILGUN_API_KEY or not MAILGUN_DOMAIN:
        print("Mailgun not configured (MAILGUN_API_KEY or MAILGUN_DOMAIN missing) — skipping email.")
        return None

    mg_url = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages"
    data = {
        "from":    MAILGUN_FROM,
        "to":      MAILGUN_TO,
        "subject": subject,
        "text":    text,
    }
    try:
        r = requests.post(mg_url, auth=("api", MAILGUN_API_KEY), data=data, timeout=10)
        r.raise_for_status()
        print(f"Mailgun: message sent ({r.status_code})")
        return r
    except requests.RequestException as exc:
        print(f"Mailgun send failed: {exc}", file=sys.stderr)
        try:
            print(f"Mailgun response body: {r.text}", file=sys.stderr)
        except Exception:
            pass
        return None

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    seen   = load_seen()
    videos = fetch_latest_videos()

    new_videos = [v for v in videos if v["id"] not in seen]
    if not new_videos:
        print("No new videos — nothing to send.")
        return

    print(f"\nProcessing {len(new_videos)} new video(s)…")
    sections = []

    for v in new_videos:
        print(f"\n  → {v['title']}")
        try:
            summary = summarise(v["url"])
            sections.append(
                f"{'=' * 60}\n"
                f"{v['date']} — {v['title']}\n"
                f"{v['url']}\n\n"
                f"{summary}"
            )
            seen.add(v["id"])
        except subprocess.CalledProcessError as exc:
            print(f"  WARNING: could not summarise {v['url']}: {exc}", file=sys.stderr)

    if sections:
        body = "\n\n".join(sections)
        mailgun_send("TheDiaryOfACEO — New Video Summaries", body)
        save_seen(seen)
        print(f"\nDone — emailed {len(sections)} summary/summaries.")


if __name__ == "__main__":
    main()
