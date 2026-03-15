"""
fetch_pexels_footage.py — Download stock video clips from Pexels (+ optional Pixabay) for each script segment

Usage:
    python3 tools/fetch_pexels_footage.py \
        --script-file .tmp/scripts/video_1_script.json \
        --output-dir .tmp/footage/video_1/

Output:
    .tmp/footage/video_1/clip_001_0.mp4  (up to 3 per segment)
    .tmp/footage/video_1/footage_manifest.json
Exit code: 0 on success, 1 on failure

Waterfall order per query (Pixabay steps silently skipped if PIXABAY_API_KEY not set):
  1. Original query → Pexels
  2. Original query → Pixabay
  3. Simplified query → Pexels → Pixabay  (only if steps 1+2 both empty)
  4. Channel/niche name → Pexels → Pixabay  (last resort)
  5. Static photo fallback via Pexels (if no video found at all)
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

PEXELS_VIDEO_BASE = "https://api.pexels.com/videos"
PEXELS_PHOTO_BASE = "https://api.pexels.com/v1"
PIXABAY_VIDEO_BASE = "https://pixabay.com/api/videos/"
QUERIES_PER_SEGMENT = 3  # max clips to fetch per segment


# ---------------------------------------------------------------------------
# Pexels helpers
# ---------------------------------------------------------------------------

def search_pexels_videos(api_key, query, per_page=5):
    """Search Pexels for videos matching a query. Returns list of video objects."""
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape",
        "size": "large",  # min 1920x1080
    }
    resp = requests.get(f"{PEXELS_VIDEO_BASE}/search", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("videos", [])


def pick_best_clip(videos, min_duration=10):
    """
    Pick the best Pexels video clip.
    - Prefer duration >= min_duration seconds
    - Prefer highest resolution (HD/Full HD)
    Returns (video_obj, download_url) or (None, None).
    """
    def score(video):
        duration = video.get("duration", 0)
        best_width = max((f.get("width", 0) or 0 for f in video.get("video_files", [])), default=0)
        duration_ok = 1 if duration >= min_duration else 0
        return (duration_ok, best_width, duration)

    if not videos:
        return None, None

    best_video = sorted(videos, key=score, reverse=True)[0]
    files = best_video.get("video_files", [])
    if not files:
        return None, None

    hd_files = [f for f in files if (f.get("width") or 0) >= 1280]
    target_files = hd_files if hd_files else files
    target_files.sort(key=lambda f: f.get("width", 0) or 0, reverse=True)
    return best_video, target_files[0].get("link")


def search_pexels_photos(api_key, query, per_page=5):
    """Search Pexels photos as a last-resort static fallback. Returns list of photo objects."""
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": per_page, "orientation": "landscape"}
    resp = requests.get(f"{PEXELS_PHOTO_BASE}/search", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("photos", [])


# ---------------------------------------------------------------------------
# Pixabay helpers
# ---------------------------------------------------------------------------

def search_pixabay_videos(api_key, query, per_page=5):
    """Search Pixabay for videos. Returns list of hit objects (empty if key not set)."""
    if not api_key:
        return []
    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "orientation": "horizontal",
        "per_page": per_page,
    }
    resp = requests.get(PIXABAY_VIDEO_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("hits", [])


def pick_best_pixabay_clip(hits, min_duration=10):
    """
    Pick the best Pixabay clip.
    Quality preference: large > medium > small.
    Returns (hit_obj, download_url) or (None, None).
    """
    def score(hit):
        duration = hit.get("duration", 0)
        videos = hit.get("videos", {})
        best_width = 0
        for quality in ("large", "medium", "small"):
            w = (videos.get(quality) or {}).get("width", 0) or 0
            if w > best_width:
                best_width = w
        duration_ok = 1 if duration >= min_duration else 0
        return (duration_ok, best_width, duration)

    if not hits:
        return None, None

    best = sorted(hits, key=score, reverse=True)[0]
    videos = best.get("videos", {})
    url = None
    for quality in ("large", "medium", "small"):
        url = (videos.get(quality) or {}).get("url")
        if url:
            break

    return (best, url) if url else (None, None)


# ---------------------------------------------------------------------------
# Query utilities
# ---------------------------------------------------------------------------

def simplify_query(query):
    """Remove common descriptive adjectives/adverbs from a query as a fallback."""
    stopwords = {
        "beautiful", "amazing", "inspiring", "motivating", "happy", "sad",
        "successful", "positive", "negative", "bright", "dark", "young", "old",
        "slow", "fast", "busy", "calm", "peaceful", "energetic",
    }
    words = query.split()
    filtered = [w for w in words if w.lower() not in stopwords]
    return " ".join(filtered) if filtered else query


def get_queries_for_segment(seg):
    """
    Return a list of search queries for a segment.
    Supports both new pexels_search_queries (list) and old pexels_search_query (str).
    """
    queries = seg.get("pexels_search_queries")
    if isinstance(queries, list) and queries:
        return [q.strip() for q in queries[:QUERIES_PER_SEGMENT] if q.strip()]
    single = seg.get("pexels_search_query", "").strip()
    return [single] if single else []


# ---------------------------------------------------------------------------
# Multi-source waterfall
# ---------------------------------------------------------------------------

def _try_pexels(api_key, query, label):
    """Attempt a Pexels search, returning (videos, source_label) or ([], None)."""
    try:
        videos = search_pexels_videos(api_key, query)
        time.sleep(0.3)
        if videos:
            return videos, f"pexels:{label}"
    except requests.HTTPError as e:
        if e.response.status_code == 429:
            print("  Rate limit hit on Pexels. Waiting 60s...", file=sys.stderr)
            time.sleep(60)
            try:
                videos = search_pexels_videos(api_key, query)
                if videos:
                    return videos, f"pexels:{label}"
            except Exception:
                pass
        else:
            print(f"  WARNING: Pexels search failed ({label}): {e}", file=sys.stderr)
    except Exception as e:
        print(f"  WARNING: Pexels search error ({label}): {e}", file=sys.stderr)
    return [], None


def _try_pixabay(api_key, query, label):
    """Attempt a Pixabay search, returning (hits, source_label) or ([], None)."""
    if not api_key:
        return [], None
    try:
        hits = search_pixabay_videos(api_key, query)
        time.sleep(0.3)
        if hits:
            return hits, f"pixabay:{label}"
    except Exception as e:
        print(f"  WARNING: Pixabay search error ({label}): {e}", file=sys.stderr)
    return [], None


def search_videos_all_sources(pexels_key, pixabay_key, query, channel_name):
    """
    Multi-source waterfall search. Returns (source_tag, results_list).

    Waterfall:
      Step 1: original query → Pexels
      Step 2: original query → Pixabay
      Step 3: simplified query → Pexels → Pixabay  (only if steps 1+2 both empty)
      Step 4: channel_name → Pexels → Pixabay      (last resort)
      Step 5: static photo fallback via Pexels      (no video found at all)
    """
    # Step 1 — original query, Pexels
    results, source = _try_pexels(pexels_key, query, "original")
    if results:
        return source, results

    # Step 2 — original query, Pixabay
    results, source = _try_pixabay(pixabay_key, query, "original")
    if results:
        return source, results

    # Steps 3+: simplified query (only if it differs from original)
    simplified = simplify_query(query)
    if simplified != query:
        print(f"  Fallback query: '{simplified}'", file=sys.stderr)
        # Step 3a — simplified, Pexels
        results, source = _try_pexels(pexels_key, simplified, "simplified")
        if results:
            return source, results
        # Step 3b — simplified, Pixabay
        results, source = _try_pixabay(pixabay_key, simplified, "simplified")
        if results:
            return source, results

    # Step 4 — channel/niche name, Pexels then Pixabay
    print(f"  Last resort query: '{channel_name}'", file=sys.stderr)
    results, source = _try_pexels(pexels_key, channel_name, "channel")
    if results:
        return source, results
    results, source = _try_pixabay(pixabay_key, channel_name, "channel")
    if results:
        return source, results

    # Step 5 — static photo fallback (Pexels photos API)
    print(f"  Photo fallback: '{query}'", file=sys.stderr)
    try:
        photos = search_pexels_photos(pexels_key, query)
        if photos:
            return "pexels_photo:original", photos
    except Exception as e:
        print(f"  WARNING: Photo fallback failed: {e}", file=sys.stderr)

    return None, []


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_clip(url, output_path, max_retries=3):
    """Download a video (or image) to disk."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Download attempt {attempt+1} failed: {e}. Retrying...", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"  ERROR: Download failed after {max_retries} attempts: {e}", file=sys.stderr)
                return False
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch stock footage for script segments (Pexels + optional Pixabay)")
    parser.add_argument("--script-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.script_file) as f:
        script = json.load(f)

    pexels_key = os.getenv("PEXELS_API_KEY")
    if not pexels_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    pixabay_key = os.getenv("PIXABAY_API_KEY")  # Optional — silently skipped if absent
    if pixabay_key:
        print("Pixabay enabled as secondary source.", file=sys.stderr)

    niche = os.getenv("NICHE", "self development")
    channel_name = os.getenv("CHANNEL_NAME", niche)

    segments = script.get("segments", [])
    if not segments:
        print("ERROR: Script has no segments.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = {}
    failed_segments = []
    query_cache = {}  # query -> clip_path (avoid re-downloading identical queries)

    for seg in segments:
        seg_id = seg.get("segment_id", 0)
        queries = get_queries_for_segment(seg)

        if not queries:
            print(f"  Segment {seg_id}: no search query, skipping.", file=sys.stderr)
            continue

        downloaded_clips = []

        for q_idx, query in enumerate(queries):
            clip_filename = f"clip_{seg_id:03d}_{q_idx}.mp4"
            clip_path = os.path.join(args.output_dir, clip_filename)

            # Resume support — skip already-downloaded clips
            if os.path.exists(clip_path) and os.path.getsize(clip_path) > 10000:
                downloaded_clips.append(clip_filename)
                query_cache[query] = clip_path
                print(f"  Segment {seg_id} clip {q_idx}: already exists, skipping.", file=sys.stderr)
                continue

            # Reuse cached clip for identical queries
            if query in query_cache and os.path.exists(query_cache[query]):
                import shutil
                shutil.copy2(query_cache[query], clip_path)
                downloaded_clips.append(clip_filename)
                print(f"  Segment {seg_id} clip {q_idx}: reused cached clip for '{query}'", file=sys.stderr)
                continue

            print(f"  Segment {seg_id} clip {q_idx}: searching for '{query}'...", file=sys.stderr)

            source, results = search_videos_all_sources(pexels_key, pixabay_key, query, channel_name)

            if not results:
                print(f"  WARNING: No footage found for segment {seg_id} query {q_idx}.", file=sys.stderr)
                continue

            duration_needed = seg.get("duration_estimate", 15)

            # Branch on source to use the right clip-picker
            if source and source.startswith("pixabay"):
                clip_obj, clip_url = pick_best_pixabay_clip(results, min_duration=duration_needed)
                clip_duration = clip_obj.get("duration", "?") if clip_obj else "?"
            elif source == "pexels_photo:original":
                # Static photo fallback — download the original/large image
                photo = results[0]
                clip_url = (photo.get("src") or {}).get("original") or (photo.get("src") or {}).get("large2x")
                clip_obj = photo
                clip_duration = "photo"
                # Save as .jpg since it's an image, not a video
                clip_filename = clip_filename.replace(".mp4", ".jpg")
                clip_path = clip_path.replace(".mp4", ".jpg")
            else:
                clip_obj, clip_url = pick_best_clip(results, min_duration=duration_needed)
                clip_duration = clip_obj.get("duration", "?") if clip_obj else "?"

            if not clip_url:
                print(f"  WARNING: Could not get clip URL for segment {seg_id} query {q_idx}.", file=sys.stderr)
                continue

            print(f"  [{source}] Downloading ({clip_duration}s)...", file=sys.stderr)
            success = download_clip(clip_url, clip_path)

            if success:
                downloaded_clips.append(clip_filename)
                query_cache[query] = clip_path
                print(f"  Segment {seg_id} clip {q_idx}: saved {clip_filename}", file=sys.stderr)

        # Store in manifest
        if len(downloaded_clips) == 1:
            manifest[str(seg_id)] = downloaded_clips[0]
        elif len(downloaded_clips) > 1:
            manifest[str(seg_id)] = downloaded_clips
        else:
            print(f"  WARNING: No clips downloaded for segment {seg_id}. Will be skipped in assembly.", file=sys.stderr)
            failed_segments.append(seg_id)

    manifest_path = os.path.join(args.output_dir, "footage_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    total = len(segments)
    fetched = len(manifest)
    print(f"\nFetched {fetched}/{total} segments → {args.output_dir}", file=sys.stderr)
    if failed_segments:
        print(f"Failed segments: {failed_segments}", file=sys.stderr)

    if fetched == 0:
        print("ERROR: No clips fetched at all.", file=sys.stderr)
        sys.exit(1)

    print(args.output_dir)


if __name__ == "__main__":
    main()
