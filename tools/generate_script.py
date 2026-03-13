"""
generate_script.py — Generate a full video script from an approved idea using Claude Sonnet

Usage:
    python3 tools/generate_script.py \
        --idea-id 3 \
        --ideas-file .tmp/ideas.json \
        --output .tmp/scripts/video_3_script.json

Output: JSON file with segmented script
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


def build_prompt(idea, channel_name, niche):
    return f"""You are a professional YouTube scriptwriter specializing in "{niche}" content for a faceless channel.

Write a complete, engaging video script for the following idea:

Title: {idea['title']}
Hook concept: {idea['hook']}
Unique angle: {idea['angle']}
Target emotion: {idea['target_emotion']}

Channel name: {channel_name}

SCRIPT REQUIREMENTS:
- Total length: ~2 minutes spoken at a natural pace (~130 words/minute = 250-280 words total)
- Format: Faceless channel — stock footage + AI voiceover. No "I" statements unless as hypothetical examples.
- Voice: Warm, authoritative, second-person ("you"), not preachy. Like advice from a knowledgeable friend.
- Structure: Hook → 1-2 main points → CTA (keep it tight — this is a short test video)
- Each main point: one sentence setup, one concrete example, one actionable takeaway
- Use pattern interrupts: one rhetorical question or surprising stat

Return ONLY a valid JSON object with this exact structure:
{{
  "idea_id": {idea['id']},
  "title": "{idea['title']}",
  "thumbnail_text": "SHORT THUMBNAIL TEXT (max 5 words, all caps)",
  "description": "YouTube video description (150-300 words, includes timestamps, relevant keywords, CTA to subscribe)",
  "tags": ["tag1", "tag2", ...],  // 10-15 relevant tags
  "category_id": "26",  // YouTube category (26 = Howto & Style)
  "total_duration_estimate": 120,  // seconds
  "segments": [
    {{
      "segment_id": 1,
      "type": "hook",
      "text": "The exact spoken words for this segment",
      "visual_cue": "Description of what footage to show (concrete, specific)",
      "overlay_text": "Optional on-screen text (null if none)",
      "duration_estimate": 20,
      "pexels_search_queries": [
        "primary scene (most specific, e.g. 'person writing notebook coffee shop morning')",
        "different subject or setting for same theme (e.g. 'student studying library books desk')",
        "broader fallback that always returns results (e.g. 'person desk working focused')"
      ]
    }}
  ]
}}

Segment types: hook, point_1, point_2 (optional), cta
Each segment: 15-40 seconds of spoken content
The pexels_search_queries array must have exactly 3 queries, each a different visual angle on the segment:
- Query 1: Most specific scene tied directly to the segment content
- Query 2: Different subject or setting that complements the same theme
- Query 3: Broader fallback that will reliably return results
All queries must use specific, concrete nouns — NOT abstract concepts.
Examples of GOOD queries: "person writing notebook coffee shop", "runner sunrise park trail"
Examples of BAD queries: "productivity", "motivation", "success mindset"
Keep all 3 queries varied — do not repeat the same keywords across all three.

Respond ONLY with the JSON object, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate video script from an idea")
    parser.add_argument("--idea-id", type=int, required=True)
    parser.add_argument("--ideas-file", required=True)
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    if not os.path.exists(args.ideas_file):
        print(f"ERROR: Ideas file not found: {args.ideas_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.ideas_file) as f:
        ideas = json.load(f)

    idea = next((i for i in ideas if i.get("id") == args.idea_id), None)
    if not idea:
        print(f"ERROR: Idea ID {args.idea_id} not found in {args.ideas_file}", file=sys.stderr)
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    channel_name = os.getenv("CHANNEL_NAME", "Our Channel")
    niche = os.getenv("NICHE", "Self Development")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(idea, channel_name, niche)

    print(f"Generating script for: '{idea['title']}'...", file=sys.stderr)

    script = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code block if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            script = json.loads(raw)

            if "segments" not in script or not script["segments"]:
                raise ValueError("Script has no segments")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse script: {e}", file=sys.stderr)
                sys.exit(1)

    # Count words to estimate duration
    total_words = sum(len(s.get("text", "").split()) for s in script.get("segments", []))
    estimated_duration = int(total_words / 130 * 60)  # 130 wpm
    script["total_duration_estimate"] = estimated_duration

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(script, f, indent=2)

    print(f"Script saved → {args.output} (~{estimated_duration}s, {total_words} words)", file=sys.stderr)
    print(args.output)


if __name__ == "__main__":
    main()
