"""
generate_short_scripts.py — Generate 2 complete YouTube Short scripts from a full video script

Uses Claude Sonnet to analyse the full video's segments and write 2 independent short scripts,
each with a hook, core insight, and curiosity-driving CTA. Optimized for <60s spoken delivery.

Usage:
    python3 tools/generate_short_scripts.py \
        --script-file .tmp/scripts/video_1_script.json \
        --video-title "5 Habits That Changed My Life" \
        --niche "Self Development" \
        --channel-name "Growth Daily" \
        --output .tmp/shorts/video_1_shorts_plan.json

Output (stdout): Path to output JSON file
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def build_prompt(segments, video_title, niche, channel_name):
    segments_summary = []
    for i, seg in enumerate(segments):
        seg_type = seg.get("type", "unknown")
        text = seg.get("text", "").strip()
        overlay = seg.get("overlay_text", "")
        word_count = len(text.split())
        segments_summary.append(
            f"[{i}] type={seg_type} overlay='{overlay}' words={word_count}\n    text: {text[:200]}{'...' if len(text) > 200 else ''}"
        )

    segments_text = "\n\n".join(segments_summary)
    niche_tag = niche.replace(" ", "").replace("-", "")

    return f"""You are a YouTube Shorts scriptwriter for a "{niche}" channel called "{channel_name}".

A full YouTube video is being published titled: "{video_title}"
Below are all its script segments (index, type, overlay text, word count, and text preview):

{segments_text}

Your job: Select the 2 segments with the most standalone, surprising, or actionable insights.
For each, write a complete YouTube Short script that fits ~50-55 seconds when spoken at 130 wpm (90-115 words total).

SHORT SCRIPT STRUCTURE:
1. HOOK (1-2 sentences, 8-12s): Open with a surprising fact, bold claim, or counterintuitive statement.
   Do NOT start with "In this video" or "Today". Start mid-idea, like you're already talking.
   The hook should work as a standalone statement — someone seeing this Short cold should be hooked immediately.
2. CORE (3-5 sentences, 30-38s): Deliver the full insight. Be specific, vivid, and direct.
   The viewer should feel they genuinely learned something real. Do not tease — deliver the value.
3. CTA (1-2 sentences, 6-10s): End with a curiosity-driving close. Reference the broader video or channel.
   Example: "This is just one of 5 habits — subscribe to catch the rest @{channel_name}"
   Example: "Follow for one insight like this every day @{channel_name}"
   Make it feel like the viewer is missing out if they don't subscribe.

RULES:
- spoken_script total must be 90-115 words (count carefully — this is strict for timing)
- The 2 shorts must cover DIFFERENT topics — not adjacent or overlapping points
- Prefer segment types: point_1, point_2, point_3, point_4, pattern_interrupt (skip hook/bridge/cta/context — those are context-dependent)
- hook_overlay: 4-6 words ALL CAPS for the on-screen text overlay shown in the first 3 seconds
  (e.g. "THIS HABIT TAKES 2 MINUTES" — creates immediate curiosity or tension)
- cta_overlay: Short on-screen text shown during the CTA (e.g. "Subscribe @{channel_name} for more")
- pexels_queries: 3 portrait-friendly search queries for stock footage
  Focus on human subjects in vertical/portrait orientation
  (e.g. "person writing journal close up vertical", "morning routine sunlight window portrait", "focused person desk working vertical")

Return ONLY a valid JSON object with NO markdown fencing:
{{
  "shorts": [
    {{
      "source_segment_index": 3,
      "source_segment_type": "point_1",
      "spoken_script": "Full spoken script here — hook, then core insight, then CTA. All 90-115 words.",
      "hook_overlay": "THIS HABIT TAKES 2 MINUTES",
      "cta_overlay": "Subscribe @{channel_name} for daily insights",
      "pexels_queries": [
        "person writing journal close up vertical",
        "morning routine sunlight window portrait",
        "focused person working desk vertical"
      ],
      "short_title": "The 2-Minute Habit That Rewires Your Brain #Shorts",
      "short_description": "Most people skip this habit thinking it's too simple. Big mistake.\\n\\nFull video on the channel → @{channel_name}\\n\\n#{niche_tag} #Shorts #SelfImprovement"
    }},
    {{
      "source_segment_index": 7,
      "source_segment_type": "point_3",
      "spoken_script": "...",
      "hook_overlay": "NOBODY TELLS YOU THIS",
      "cta_overlay": "Follow @{channel_name} for more",
      "pexels_queries": ["...", "...", "..."],
      "short_title": "...",
      "short_description": "..."
    }}
  ]
}}"""


def main():
    parser = argparse.ArgumentParser(description="Generate YouTube Short scripts from full video script")
    parser.add_argument("--script-file", required=True, help="Path to video script JSON")
    parser.add_argument("--video-title", required=True, help="Full video title")
    parser.add_argument("--niche", default=None, help="Channel niche (overrides .env NICHE)")
    parser.add_argument("--channel-name", default=None, help="Channel name (overrides .env CHANNEL_NAME)")
    parser.add_argument("--output", required=True, help="Output path for shorts plan JSON")
    args = parser.parse_args()

    niche = args.niche or os.getenv("NICHE", "Self Development")
    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "the channel")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.script_file) as f:
        script = json.load(f)

    segments = script.get("segments", [])
    if not segments:
        print("ERROR: Script has no segments.", file=sys.stderr)
        sys.exit(1)

    prompt = build_prompt(segments, args.video_title, niche, channel_name)

    print(f"Generating short scripts for '{args.video_title}'...", file=sys.stderr)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip any accidental markdown fencing
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
        print(f"Raw response:\n{raw[:500]}", file=sys.stderr)
        sys.exit(1)

    shorts = plan.get("shorts", [])
    if len(shorts) < 2:
        print(f"ERROR: Claude returned fewer than 2 shorts: {len(shorts)}", file=sys.stderr)
        sys.exit(1)

    # Validate required fields
    required_fields = ["source_segment_index", "spoken_script", "hook_overlay", "cta_overlay",
                       "pexels_queries", "short_title", "short_description"]
    for i, short in enumerate(shorts[:2]):
        for field in required_fields:
            if field not in short:
                print(f"ERROR: Short {i+1} missing field '{field}'", file=sys.stderr)
                sys.exit(1)
        word_count = len(short["spoken_script"].split())
        if word_count < 70 or word_count > 130:
            print(f"WARNING: Short {i+1} spoken_script has {word_count} words (target: 90-115)", file=sys.stderr)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(plan, f, indent=2)

    print(f"Generated 2 short scripts → {args.output}", file=sys.stderr)
    print(args.output)


if __name__ == "__main__":
    main()
