"""
publisher_agent.py — Publish approved YouTube videos (unlisted → public)

Triggered by approval_poller.py when video approval reply is received.

Usage:
    python3 agents/publisher_agent.py
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
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp")
PYTHON = sys.executable
REGISTRY_PATH = os.path.join(TMP_DIR, "published_videos_registry.json")


def run_tool(tool_name, args_list, capture_output=True):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def get_state():
    _, stdout, _ = _run_raw("manage_state.py", ["--read"])
    return json.loads(stdout)


def _run_raw(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def set_phase(phase: str):
    run_tool("manage_state.py", ["--set-phase", phase])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


def main():
    state = get_state()
    approved_video_ids = state.get("approved_video_ids", [])
    videos = state.get("videos", {})
    niche = os.getenv("NICHE", "Self Development")
    approval_email = os.getenv("APPROVAL_EMAIL")

    if not approved_video_ids:
        print("[publisher_agent] No approved video IDs found.")
        sys.exit(0)

    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    set_phase("publishing_in_progress")
    print(f"[publisher_agent] Publishing {len(approved_video_ids)} video(s)...")

    published = []
    failed = []

    for video_key, video_data in videos.items():
        yt_id = video_data.get("youtube_video_id")
        title = video_data.get("title", "Untitled")

        if not yt_id or yt_id not in approved_video_ids:
            continue

        print(f"  Publishing: '{title}' ({yt_id})...")
        try:
            public_url = run_tool("publish_youtube_video.py", ["--video-id", yt_id])

            # Update video status in state
            update_state({
                "videos": {
                    video_key: {
                        **video_data,
                        "published": True,
                        "public_url": public_url,
                        "published_at": datetime.now(timezone.utc).isoformat(),
                    }
                }
            })

            published.append({"title": title, "url": public_url})
            print(f"  ✓ Published: {public_url}")

            # Append to persistent registry (survives weekly state resets — used by analytics)
            try:
                registry = json.load(open(REGISTRY_PATH)) if os.path.exists(REGISTRY_PATH) else []
                registry.append({
                    "youtube_video_id": yt_id,
                    "title": title,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "week": state.get("week"),
                    "public_url": public_url,
                })
                os.makedirs(TMP_DIR, exist_ok=True)
                with open(REGISTRY_PATH, "w") as f:
                    json.dump(registry, f, indent=2)
            except Exception as reg_err:
                print(f"  WARNING: Could not update video registry: {reg_err}", file=sys.stderr)

        except Exception as e:
            error_msg = f"Failed to publish {title} ({yt_id}): {e}"
            log_error(error_msg)
            print(f"  ✗ {error_msg}", file=sys.stderr)
            failed.append(title)

    # Send completion email
    week_str = state.get("week", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    if published:
        lines = [
            f"{len(published)} video(s) published successfully this week!",
            "",
        ]
        for vid in published:
            lines.append(f"  ✓ \"{vid['title']}\"")
            lines.append(f"    {vid['url']}")
            lines.append("")

        if failed:
            lines.append(f"Note: {len(failed)} video(s) failed to publish: {', '.join(failed)}")
            lines.append("")

        lines += [
            "─" * 50,
            "Next steps:",
            "  • Share your videos on social media",
            "  • Check analytics in 48 hours",
            f"  • New ideas will be generated next Sunday",
            "",
            "—YouTube Automation",
        ]

        subject = f"[YT Automation] {len(published)} Video(s) Published! 🎉"
        if failed:
            subject += f" ({len(failed)} failed)"

    else:
        lines = [
            "Publishing failed for all videos.",
            "",
            f"Failed: {', '.join(failed)}",
            "",
            "The videos remain unlisted on YouTube. You can publish them manually.",
        ]
        subject = "[YT Automation] ERROR: Video publishing failed"

    try:
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", "\n".join(lines),
        ])
        print(f"  → Completion email sent.")
    except Exception as e:
        print(f"WARNING: Could not send completion email: {e}", file=sys.stderr)

    set_phase("completed")
    print(f"[publisher_agent] Done. Published: {len(published)}, Failed: {len(failed)}")


if __name__ == "__main__":
    main()
