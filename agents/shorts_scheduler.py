"""
shorts_scheduler.py — Trigger shorts_agent for videos publishing today

Run via cron on Mon/Wed/Fri at 10pm IST (16:30 UTC):
    30 16 * * 1,3,5  /path/to/venv/bin/python3 /path/to/agents/shorts_scheduler.py

Reads state, finds videos whose scheduled_publish_at falls on today (UTC date),
and launches shorts_agent.py for each. This spreads YouTube API quota usage
(~3,200 units/day per video) instead of all 9,600 units hitting on publishing day.

Unit budget per day with this approach:
  - Main pipeline (publishing day, once/week): ~150 units
  - Shorts scheduler (Mon/Wed/Fri nights):     ~3,200 units each day
  Total any single day: well under 10,000 unit limit.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
AGENTS_DIR = os.path.join(PROJECT_ROOT, "agents")
PYTHON = sys.executable


def get_state():
    cmd = [PYTHON, os.path.join(TOOLS_DIR, "manage_state.py"), "--read"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: Could not read state: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout.strip())


def main():
    today_utc = datetime.now(timezone.utc).date()
    print(f"[shorts_scheduler] Running for {today_utc} (UTC)")

    state = get_state()
    videos = state.get("videos", {})

    if not videos:
        print("[shorts_scheduler] No videos in state.")
        sys.exit(0)

    launched = 0

    for video_key, video_data in sorted(videos.items()):
        publish_at = video_data.get("scheduled_publish_at")
        if not publish_at:
            continue

        # Only process videos publishing today
        publish_date = datetime.fromisoformat(publish_at.replace("Z", "+00:00")).date()
        if publish_date != today_utc:
            continue

        # Skip if both shorts already scheduled (resume safety)
        shorts = video_data.get("shorts", {})
        short_1_done = shorts.get("short_1", {}).get("status") == "scheduled"
        short_2_done = shorts.get("short_2", {}).get("status") == "scheduled"
        if short_1_done and short_2_done:
            print(f"[shorts_scheduler] {video_key}: shorts already scheduled, skipping.")
            continue

        title = video_data.get("title", "Untitled")
        print(f"[shorts_scheduler] Launching shorts_agent for {video_key}: '{title}'")

        shorts_script = os.path.join(AGENTS_DIR, "shorts_agent.py")
        subprocess.Popen([
            PYTHON, shorts_script,
            "--video-key", video_key,
            "--full-video-publish-at", publish_at,
        ])
        launched += 1

    if launched == 0:
        print("[shorts_scheduler] No videos found publishing today.")
    else:
        print(f"[shorts_scheduler] Launched {launched} shorts_agent process(es).")


if __name__ == "__main__":
    main()
