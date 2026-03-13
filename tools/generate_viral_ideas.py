"""
generate_viral_ideas.py — Generate viral video ideas using a trend researcher prompt (Claude Sonnet)

Acts as a YouTube trend researcher and viral content strategist. Produces richer idea output
than generate_ideas.py, with viral_reason and content_format fields added.

Output schema is a superset of generate_ideas.py — fully compatible with the existing pipeline.

Usage:
    python3 tools/generate_viral_ideas.py \
        --scraped-file .tmp/scraped_videos.json \
        --niche "Self Development" \
        --count 10

    # Without scraped file (topic-based, no trending context):
    python3 tools/generate_viral_ideas.py \
        --niche "Self Development" \
        --topic "morning routines" \
        --count 10

Output: .tmp/viral_ideas.json
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
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".tmp", "viral_ideas.json")
DEFAULT_STRATEGY_PATH = os.path.join(PROJECT_ROOT, "channel_strategy.json")


def load_strategy(path):
    """Load channel strategy JSON if it exists, return {} otherwise."""
    try:
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def build_strategy_section(strategy):
    """Build the CHANNEL STRATEGY context block to inject into the prompt."""
    if not strategy:
        return ""
    positioning = strategy.get("channel_positioning", {})
    brand_voice = positioning.get("brand_voice", "")
    differentiation = positioning.get("differentiation", "")
    pillars = strategy.get("content_pillars", [])
    formats = strategy.get("content_formats", [])

    parts = []
    if brand_voice:
        parts.append(f"Brand Voice: {brand_voice}")
    if differentiation:
        parts.append(f"Differentiation: {differentiation}")
    if pillars:
        pillar_lines = "\n".join(
            f"  - {p['name']}: {p['description']}" for p in pillars if p.get("name")
        )
        parts.append(f"Content Pillars (each idea must map to one of these):\n{pillar_lines}")
    if formats:
        format_lines = "\n".join(
            f"  - {f['format']}: {f.get('why_it_works', '')[:130]}"
            for f in formats if f.get("format")
        )
        parts.append(f"Proven Content Formats (use these structures):\n{format_lines}")

    if not parts:
        return ""
    return "\nCHANNEL STRATEGY — align all ideas to this:\n" + "\n\n".join(parts) + "\n"


def build_prompt(niche, count, scraped_videos=None, topic="", analytics_context="", strategy=None):
    trending_section = ""
    if scraped_videos:
        top_videos = sorted(scraped_videos, key=lambda v: v.get("views", 0), reverse=True)[:20]
        video_list = ""
        for i, v in enumerate(top_videos, 1):
            video_list += (
                f"{i}. \"{v['title']}\" — {v['views']:,} views\n"
                f"   Channel: {v['channel']} | Tags: {', '.join(v.get('tags', [])[:5])}\n\n"
            )
        trending_section = f"""
CURRENTLY TRENDING IN THIS NICHE (last 2 weeks):
{video_list}
Analyze these for patterns — what titles work, what emotions they target, what gaps exist.
"""

    topic_section = ""
    if topic:
        topic_section = f"\nFocus area: {topic}\n"

    analytics_section = ""
    if analytics_context and analytics_context.strip():
        analytics_section = f"""
PAST PERFORMANCE INSIGHTS FROM THIS CHANNEL (last 4 weeks):
{analytics_context}

Apply these insights: double down on what worked, avoid what flopped.
"""

    strategy_section = build_strategy_section(strategy)

    return f"""Act as a YouTube trend researcher and viral content strategist for the "{niche}" niche.

Generate {count} high-potential video ideas based on search demand, trending topics, algorithm patterns, and audience pain points.
{topic_section}{trending_section}{analytics_section}{strategy_section}
Each idea must have a realistic path to 100k+ views. Focus on:
- Scroll-stopping titles that create immediate curiosity or tension
- Hooks that grab attention in the first 10 seconds
- Topics with viral potential RIGHT NOW (trending angles, timely pain points, emerging awareness)
- Target emotions that drive clicks: curiosity, fear of missing out, surprise, aspiration, inspiration
- Content formats suited for faceless channels: tutorial, breakdown, story, experiment, listicle

Return ONLY a valid JSON array with exactly {count} objects. Each object must have:
- "id": integer 1-{count}
- "title": scroll-stopping title (max 80 chars, use power words and tension)
- "hook": the core hook for the first 10 seconds — one punchy sentence that creates immediate curiosity (max 150 chars)
- "angle": what makes this unique vs existing content, why viewers would choose this over similar videos (max 200 chars)
- "target_emotion": one of: curiosity, fear, surprise, aspiration, inspiration
- "potential": one of: High, Medium, Low (based on search demand and viral likelihood)
- "pexels_search_query": concrete stock footage query using specific nouns (e.g. "person journaling coffee desk morning" not "productivity")
- "content_format": one of: tutorial, breakdown, story, experiment, listicle
- "viral_reason": why this topic has viral potential RIGHT NOW — trend momentum, cultural timing, or audience pain point (max 200 chars)

Respond ONLY with the JSON array, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate viral video ideas with Claude Sonnet")
    parser.add_argument("--scraped-file", default="", help="Path to scraped_videos.json (optional)")
    parser.add_argument("--niche", required=True)
    parser.add_argument("--topic", default="", help="Optional topic focus (used when no scraped file)")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--analytics-context", default="", help="Performance insights context (optional)")
    parser.add_argument("--output", default="", help="Output path (default: .tmp/viral_ideas.json)")
    parser.add_argument("--strategy-file", default="", help="Path to channel_strategy.json (auto-detected if not set)")
    args = parser.parse_args()

    output_path = args.output or OUTPUT_PATH

    strategy_path = args.strategy_file or DEFAULT_STRATEGY_PATH
    strategy = load_strategy(strategy_path)
    if strategy:
        print(f"  Strategy loaded: {strategy_path}", file=sys.stderr)

    scraped_videos = None
    if args.scraped_file:
        if not os.path.exists(args.scraped_file):
            print(f"ERROR: Scraped file not found: {args.scraped_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.scraped_file) as f:
            scraped_videos = json.load(f)
        if not scraped_videos:
            print("WARNING: Scraped videos file is empty, proceeding without trending context.", file=sys.stderr)
            scraped_videos = None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(args.niche, args.count, scraped_videos, args.topic, args.analytics_context, strategy)

    print(f"Generating {args.count} viral ideas for '{args.niche}'...", file=sys.stderr)

    ideas = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

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

    # Validate and ensure all fields present
    required_fields = ["id", "title", "hook", "angle", "target_emotion", "potential",
                       "pexels_search_query", "content_format", "viral_reason"]
    cleaned = []
    for idea in ideas:
        for field in required_fields:
            if field not in idea:
                idea[field] = ""
        cleaned.append(idea)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(cleaned, f, indent=2)

    print(f"Generated {len(cleaned)} viral ideas → {output_path}", file=sys.stderr)
    print(output_path)


if __name__ == "__main__":
    main()
