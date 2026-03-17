"""
shorts_agent.py — Produce and schedule 2 YouTube Shorts for a given full video

For each full video, generates 2 independent short scripts (via Claude Sonnet),
creates TTS voiceover, assembles portrait 1080x1920 video (pure ffmpeg), uploads
to YouTube as private, and schedules them to go public the day after the full video.

Schedule:
  Full video on Monday  → Short 1: Tuesday  7am IST (01:30 UTC)
                          Short 2: Tuesday  7pm IST (13:30 UTC)
  Full video on Wed     → Short 1: Thursday 7am IST
                          Short 2: Thursday 7pm IST
  Full video on Friday  → Short 1: Saturday 7am IST
                          Short 2: Saturday 7pm IST

Usage:
    python3 agents/shorts_agent.py \
        --video-key video_1 \
        --full-video-publish-at 2026-03-17T03:30:00Z
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp")
SHORTS_DIR = os.path.join(TMP_DIR, "shorts")
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Tool runner helpers (same pattern as publisher_agent.py)
# ---------------------------------------------------------------------------

def run_tool(tool_name, args_list, capture_output=True):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def _run_raw(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def get_state():
    _, stdout, _ = _run_raw("manage_state.py", ["--read"])
    return json.loads(stdout)


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Schedule computation
# ---------------------------------------------------------------------------

def compute_shorts_schedule(full_video_publish_at: str):
    """
    Given a full video publish time (ISO UTC string), return the two short
    publish times: next day 01:30 UTC (7am IST) and 13:30 UTC (7pm IST).

    Formula:
      short_1 = full_video_publish_at + 22 hours
      short_2 = full_video_publish_at + 34 hours
    """
    dt = datetime.fromisoformat(full_video_publish_at.replace("Z", "+00:00"))
    short_1 = dt + timedelta(hours=22)
    short_2 = dt + timedelta(hours=34)
    return (
        short_1.strftime("%Y-%m-%dT%H:%M:%SZ"),
        short_2.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# ---------------------------------------------------------------------------
# Short production pipeline
# ---------------------------------------------------------------------------

def produce_short(
    short_number: int,
    short_plan: dict,
    video_key: str,
    full_youtube_video_id: str,
    publish_at: str,
    niche: str,
    approval_email: str,
):
    """
    Produce and schedule a single YouTube Short.
    Returns dict with short metadata, or None on failure.
    """
    short_id = f"short_{short_number}"
    base_name = f"{video_key}_{short_id}"
    spoken_script = short_plan.get("spoken_script", "")
    hook_overlay = short_plan.get("hook_overlay", "")
    cta_overlay = short_plan.get("cta_overlay", "")
    pexels_queries = short_plan.get("pexels_queries", [])
    short_title = short_plan.get("short_title", f"Short #{short_number} #Shorts")
    short_description = short_plan.get("short_description", "")

    # Append full video link to description
    full_video_url = f"https://youtube.com/watch?v={full_youtube_video_id}"
    if full_video_url not in short_description:
        short_description += f"\n\nFull video: {full_video_url}"

    script_path = os.path.join(SHORTS_DIR, f"{base_name}_script.json")
    audio_path = os.path.join(SHORTS_DIR, f"{base_name}_audio.mp3")
    meta_path = os.path.join(SHORTS_DIR, f"{base_name}_meta.json")
    output_path = os.path.join(SHORTS_DIR, f"{base_name}_final.mp4")

    # Step A: Write short script JSON for generate_voiceover.py
    # generate_voiceover.py reads script.get("segments", []) and joins all "text" fields
    short_script = {
        "title": short_title,
        "segments": [
            {"segment_id": 1, "text": spoken_script}
        ],
    }
    os.makedirs(SHORTS_DIR, exist_ok=True)
    with open(script_path, "w") as f:
        json.dump(short_script, f, indent=2)
    print(f"  [{short_id}] Short script written ({len(spoken_script.split())} words)", file=sys.stderr)

    # Step B: Generate TTS voiceover using existing tool
    print(f"  [{short_id}] Generating voiceover...", file=sys.stderr)
    raw_duration = run_tool("generate_voiceover.py", [
        "--script-file", script_path,
        "--output", audio_path,
    ])
    try:
        audio_duration = float(raw_duration)
    except (ValueError, TypeError):
        # Fallback: estimate from word count at 130 wpm
        audio_duration = len(spoken_script.split()) / 130.0 * 60.0
        print(f"  [{short_id}] WARNING: Could not parse audio duration, estimated {audio_duration:.1f}s", file=sys.stderr)

    print(f"  [{short_id}] Voiceover: {audio_duration:.1f}s", file=sys.stderr)

    if audio_duration > 62:
        print(f"  [{short_id}] WARNING: Short is {audio_duration:.1f}s — YouTube Shorts limit is 60s. Continuing.", file=sys.stderr)

    # Step C: Assemble portrait video using assemble_short.py
    print(f"  [{short_id}] Assembling portrait video...", file=sys.stderr)
    query_args = []
    for q in pexels_queries[:3]:
        query_args.append(q)

    run_tool("assemble_short.py", [
        "--pexels-queries", *query_args,
        "--audio-file", audio_path,
        "--audio-duration", str(audio_duration),
        "--hook-overlay", hook_overlay,
        "--cta-overlay", cta_overlay,
        "--output", output_path,
    ])
    print(f"  [{short_id}] Short assembled: {output_path}", file=sys.stderr)

    # Step D: Write YouTube upload metadata
    tags = [niche, "Shorts", "YouTube Shorts"]
    # Add niche-derived tags
    for word in niche.split():
        if word.lower() not in [t.lower() for t in tags]:
            tags.append(word)
    tags += ["SelfImprovement", "Motivation", "Growth"]

    meta = {
        "title": short_title[:100],  # YouTube title limit
        "description": short_description[:5000],  # YouTube description limit
        "tags": tags[:30],  # reasonable tag limit
        "category_id": "26",
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Step E: Upload to YouTube as private (will be scheduled next)
    print(f"  [{short_id}] Uploading to YouTube (private)...", file=sys.stderr)
    upload_output = run_tool("upload_to_youtube.py", [
        "--video-file", output_path,
        "--script-file", meta_path,
        "--privacy", "private",
    ])

    # Parse video ID from upload output: "{video_id} {video_url}" on a single line
    short_yt_id = None
    short_url = None
    for line in upload_output.splitlines():
        line = line.strip()
        if " " in line and "youtube.com/watch" in line:
            parts = line.split(" ", 1)
            short_yt_id = parts[0].strip()
            short_url = parts[1].strip()
            break

    if not short_yt_id:
        raise RuntimeError(f"Could not parse YouTube video ID from upload output: {upload_output!r}")

    print(f"  [{short_id}] Uploaded: {short_url}", file=sys.stderr)

    # Step F: Schedule the short
    print(f"  [{short_id}] Scheduling for {publish_at}...", file=sys.stderr)
    run_tool("publish_youtube_video.py", [
        "--video-id", short_yt_id,
        "--publish-at", publish_at,
    ])
    print(f"  [{short_id}] Scheduled: {publish_at}", file=sys.stderr)

    return {
        "youtube_video_id": short_yt_id,
        "youtube_url": short_url,
        "short_title": short_title,
        "scheduled_publish_at": publish_at,
        "source_segment_index": short_plan.get("source_segment_index"),
        "source_segment_type": short_plan.get("source_segment_type"),
        "output_path": output_path,
        "status": "scheduled",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Produce and schedule YouTube Shorts for a full video")
    parser.add_argument("--video-key", required=True, help="Video key in state (e.g. video_1)")
    parser.add_argument("--full-video-publish-at", required=True,
                        help="Full video publish time in ISO UTC (e.g. 2026-03-17T03:30:00Z)")
    args = parser.parse_args()

    niche = os.getenv("NICHE", "Self Development")
    channel_name = os.getenv("CHANNEL_NAME", "the channel")
    approval_email = os.getenv("APPROVAL_EMAIL", "")

    print(f"[shorts_agent] Starting for {args.video_key} (full video: {args.full_video_publish_at})")

    # Load state and find video metadata
    state = get_state()
    videos = state.get("videos", {})
    video_data = videos.get(args.video_key)

    if not video_data:
        print(f"ERROR: Video key '{args.video_key}' not found in state", file=sys.stderr)
        sys.exit(1)

    script_path = video_data.get("script_path")
    full_yt_id = video_data.get("youtube_video_id")
    video_title = video_data.get("title", "Untitled")

    if not script_path or not os.path.exists(script_path):
        print(f"ERROR: Script file not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    if not full_yt_id:
        print(f"ERROR: No YouTube video ID found for {args.video_key}", file=sys.stderr)
        sys.exit(1)

    # Resume check: skip if both shorts already scheduled
    existing_shorts = video_data.get("shorts", {})
    short_1_done = existing_shorts.get("short_1", {}).get("status") == "scheduled"
    short_2_done = existing_shorts.get("short_2", {}).get("status") == "scheduled"
    if short_1_done and short_2_done:
        print(f"[shorts_agent] Both shorts already scheduled for {args.video_key}. Nothing to do.")
        sys.exit(0)

    # Compute schedule
    short_1_at, short_2_at = compute_shorts_schedule(args.full_video_publish_at)

    # Convert to IST for display
    def to_ist(utc_str):
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        ist = dt + timedelta(hours=5, minutes=30)
        return ist.strftime("%a %d %b at %-I:%M%p IST")

    print(f"[shorts_agent] Schedule: Short 1 → {to_ist(short_1_at)}, Short 2 → {to_ist(short_2_at)}")

    os.makedirs(SHORTS_DIR, exist_ok=True)

    # Step 1: Generate short scripts via Claude
    shorts_plan_path = os.path.join(SHORTS_DIR, f"{args.video_key}_shorts_plan.json")

    if not os.path.exists(shorts_plan_path):
        print(f"[shorts_agent] Generating short scripts for '{video_title}'...")
        run_tool("generate_short_scripts.py", [
            "--script-file", script_path,
            "--video-title", video_title,
            "--niche", niche,
            "--channel-name", channel_name,
            "--output", shorts_plan_path,
        ])
    else:
        print(f"[shorts_agent] Reusing existing shorts plan: {shorts_plan_path}", file=sys.stderr)

    with open(shorts_plan_path) as f:
        shorts_plan = json.load(f)

    short_specs = shorts_plan.get("shorts", [])
    if len(short_specs) < 2:
        print(f"ERROR: Shorts plan has fewer than 2 entries: {len(short_specs)}", file=sys.stderr)
        sys.exit(1)

    # Save plan path to state
    update_state({
        "videos": {
            args.video_key: {
                **video_data,
                "shorts_plan_path": shorts_plan_path,
            }
        }
    })

    # Step 2: Produce each short
    schedules = [short_1_at, short_2_at]
    results = {}

    for short_number in [1, 2]:
        short_id = f"short_{short_number}"

        # Skip if already done
        if existing_shorts.get(short_id, {}).get("status") == "scheduled":
            print(f"[shorts_agent] {short_id} already scheduled, skipping.")
            results[short_id] = existing_shorts[short_id]
            continue

        spec = short_specs[short_number - 1]
        publish_at = schedules[short_number - 1]

        print(f"\n[shorts_agent] Producing {short_id} (segment {spec.get('source_segment_index')})...")
        try:
            result = produce_short(
                short_number=short_number,
                short_plan=spec,
                video_key=args.video_key,
                full_youtube_video_id=full_yt_id,
                publish_at=publish_at,
                niche=niche,
                approval_email=approval_email,
            )
            results[short_id] = result

            # Update state incrementally after each short
            update_state({
                "videos": {
                    args.video_key: {
                        "shorts": {short_id: result}
                    }
                }
            })
            print(f"[shorts_agent] {short_id} done: {result['youtube_url']}")

        except Exception as e:
            error_msg = f"shorts_agent: {short_id} failed for {args.video_key}: {e}"
            log_error(error_msg)
            print(f"[shorts_agent] WARNING: {short_id} failed — {e}. Continuing to next short.", file=sys.stderr)

    # Step 3: Send notification email if we have results and an email address
    scheduled = {k: v for k, v in results.items() if v.get("status") == "scheduled"}

    if scheduled and approval_email:
        try:
            lines = [
                f"Shorts scheduled for '{video_title}'!",
                "",
            ]
            for short_id, data in sorted(scheduled.items()):
                title = data.get("short_title", short_id)
                url = data.get("youtube_url", "")
                publish_at = data.get("scheduled_publish_at", "")
                ist_display = to_ist(publish_at) if publish_at else "unknown"
                lines.append(f"  {title}")
                lines.append(f"    {ist_display}")
                lines.append(f"    {url}")
                lines.append("")

            lines += [
                "─" * 50,
                "—YouTube Automation",
            ]

            run_tool("send_email.py", [
                "--to", approval_email,
                "--subject", f"[YT Automation] Shorts Scheduled: {video_title}",
                "--body", "\n".join(lines),
            ])
            print(f"[shorts_agent] Notification email sent.")
        except Exception as e:
            print(f"[shorts_agent] WARNING: Could not send notification email: {e}", file=sys.stderr)

    total = len(scheduled)
    print(f"\n[shorts_agent] Done. {total}/2 shorts scheduled for {args.video_key}.")

    if total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
