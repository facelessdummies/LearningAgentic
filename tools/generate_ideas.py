"""
generate_ideas.py — Generate 10 trending video ideas from scraped YouTube data using Claude

Usage:
    python3 tools/generate_ideas.py \
        --scraped-file .tmp/scraped_videos.json \
        --niche "Self Development" \
        --count 10

Output: .tmp/ideas.json
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
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".tmp", "ideas.json")


def build_prompt(scraped_videos, niche, count, analytics_context=""):
    # Use top 20 by views for context
    top_videos = sorted(scraped_videos, key=lambda v: v.get("views", 0), reverse=True)[:20]

    video_list = ""
    for i, v in enumerate(top_videos, 1):
        video_list += (
            f"{i}. \"{v['title']}\" — {v['views']:,} views\n"
            f"   Channel: {v['channel']} | Tags: {', '.join(v.get('tags', [])[:5])}\n\n"
        )

    analytics_section = ""
    if analytics_context and analytics_context.strip():
        analytics_section = f"""
PAST PERFORMANCE INSIGHTS FROM THIS CHANNEL (last 4 weeks):
{analytics_context}

Apply these insights: double down on what worked, avoid what flopped, follow the observed patterns.
"""

    return f"""You are a YouTube content strategist specializing in the "{niche}" niche.

Below are the top trending videos from the past 2 weeks in this niche. Analyze the patterns — what titles work, what angles resonate, what emotional hooks appear most, what topics cluster together.

TOP TRENDING VIDEOS:
{video_list}
{analytics_section}
Based on this analysis, generate {count} ORIGINAL video ideas for a faceless YouTube channel. These must be:
1. NOT copies of the above videos — find gaps, unexplored angles, or fresh takes
2. Optimized for the algorithm — clear hook, curiosity gap, strong emotional pull
3. Suitable for stock footage + AI voiceover format (no talking head required)
4. 8-12 minute video length potential
5. Targeted at people seeking growth, improvement, or motivation

Return ONLY a valid JSON array with exactly {count} objects. Each object must have these fields:
- "id": integer 1-{count}
- "title": compelling video title (max 80 chars, use power words)
- "hook": the opening sentence of the video (creates immediate curiosity, max 150 chars)
- "angle": what makes this unique vs existing content (max 200 chars)
- "target_emotion": one of: curiosity, inspiration, fear, aspiration, surprise
- "potential": one of: High, Medium, Low (based on search volume and engagement likelihood)
- "pexels_search_query": concrete search query for stock footage (use specific nouns, e.g., "person journaling morning coffee desk" not "productivity")

Respond ONLY with the JSON array, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate video ideas from scraped data")
    parser.add_argument("--scraped-file", required=True, help="Path to scraped_videos.json")
    parser.add_argument("--niche", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--analytics-context", default="", help="Performance insights context (optional)")
    args = parser.parse_args()

    if not os.path.exists(args.scraped_file):
        print(f"ERROR: Scraped file not found: {args.scraped_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.scraped_file) as f:
        scraped_videos = json.load(f)

    if not scraped_videos:
        print("ERROR: Scraped videos file is empty.", file=sys.stderr)
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(scraped_videos, args.niche, args.count, args.analytics_context)

    print(f"Generating {args.count} ideas for '{args.niche}'...", file=sys.stderr)

    ideas = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code block if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            ideas = json.loads(raw)
            if not isinstance(ideas, list) or len(ideas) == 0:
                raise ValueError("Expected a non-empty JSON array")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse ideas from Claude response: {e}", file=sys.stderr)
                print(f"Raw response: {raw[:500]}", file=sys.stderr)
                sys.exit(1)

    # Validate and clean up
    cleaned = []
    required_fields = ["id", "title", "hook", "angle", "target_emotion", "potential", "pexels_search_query"]
    for idea in ideas:
        for field in required_fields:
            if field not in idea:
                idea[field] = ""
        cleaned.append(idea)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(cleaned, f, indent=2)

    print(f"Generated {len(cleaned)} ideas → {OUTPUT_PATH}", file=sys.stderr)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
