"""
assemble_short.py — Assemble a 9:16 portrait YouTube Short from voiceover + Pexels portrait clips

Pure ffmpeg (no moviepy) for near-zero RAM usage. Fetches portrait-oriented stock clips,
converts them to 1080x1920, concatenates to match audio duration, then burns in
hook overlay text and CTA overlay text.

Usage:
    python3 tools/assemble_short.py \
        --pexels-queries "person writing journal portrait" "morning sunlight window vertical" "focused desk portrait" \
        --audio-file .tmp/shorts/video_1_short_1_audio.mp3 \
        --audio-duration 52.3 \
        --hook-overlay "THIS HABIT TAKES 2 MINUTES" \
        --cta-overlay "Subscribe @GrowthDaily for daily insights" \
        --output .tmp/shorts/video_1_short_1_final.mp4

Output (stdout): Path to output MP4
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
CLIPS_PER_QUERY = 2        # Portrait clips to fetch per query
MIN_CLIP_DURATION = 5      # Minimum clip duration in seconds to bother with
PEXELS_VIDEO_BASE = "https://api.pexels.com/videos"


# ---------------------------------------------------------------------------
# Font resolution (same fallback list as assemble_video.py)
# ---------------------------------------------------------------------------

FONT_PATHS = [
    "/System/Library/Fonts/Futura.ttc",
    "/Library/Fonts/Futura.ttc",
    "/System/Library/Fonts/AvenirNext.ttc",
    "/System/Library/Fonts/Avenir Next.ttc",
    "/System/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font():
    for path in FONT_PATHS:
        if os.path.exists(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Pexels portrait clip fetching
# ---------------------------------------------------------------------------

def search_pexels_portrait(api_key, query, per_page=4):
    """Search Pexels for portrait-oriented video clips."""
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "portrait",
        "size": "medium",
    }
    try:
        resp = requests.get(
            f"{PEXELS_VIDEO_BASE}/search",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("videos", [])
    except Exception as e:
        print(f"  Pexels portrait search failed for '{query}': {e}", file=sys.stderr)
        return []


def search_pexels_landscape(api_key, query, per_page=4):
    """Search Pexels for landscape clips as fallback."""
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape",
        "size": "large",
    }
    try:
        resp = requests.get(
            f"{PEXELS_VIDEO_BASE}/search",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("videos", [])
    except Exception as e:
        print(f"  Pexels landscape search failed for '{query}': {e}", file=sys.stderr)
        return []


def pick_best_clip_url(videos, is_portrait=True, min_duration=5):
    """Pick best clip URL from Pexels video list."""
    if not videos:
        return None

    def score(v):
        duration = v.get("duration", 0)
        files = v.get("video_files", [])
        if is_portrait:
            # For portrait, prefer clips where height > width
            portrait_files = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0)]
            best_res = max((f.get("height", 0) or 0 for f in portrait_files), default=0) if portrait_files else 0
        else:
            best_res = max((f.get("width", 0) or 0 for f in files), default=0)
        return (1 if duration >= min_duration else 0, best_res, duration)

    videos_sorted = sorted(videos, key=score, reverse=True)
    for video in videos_sorted:
        files = video.get("video_files", [])
        if is_portrait:
            # Prefer actual portrait files (height > width)
            portrait_files = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0)]
            target = portrait_files if portrait_files else files
        else:
            target = files

        if not target:
            continue
        target.sort(key=lambda f: f.get("width", 0) or 0, reverse=True)
        url = target[0].get("link")
        if url:
            return url
    return None


def download_clip(url, output_path, max_retries=3):
    """Download a video clip with retries."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Download attempt {attempt+1} failed: {e}. Retrying...", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"  Download failed after {max_retries} attempts: {e}", file=sys.stderr)
                return False
    return False


def fetch_portrait_clips(api_key, queries, clips_dir, clips_per_query=CLIPS_PER_QUERY):
    """
    Fetch portrait clips for each query. Returns list of downloaded file paths.
    Waterfall: portrait search → landscape search (with pillarbox flag) → skip.
    """
    os.makedirs(clips_dir, exist_ok=True)
    clips = []  # list of (path, is_landscape_fallback)
    seen_urls = set()

    for qi, query in enumerate(queries):
        print(f"  Fetching portrait clips for query: '{query}'...", file=sys.stderr)
        videos = search_pexels_portrait(api_key, query, per_page=clips_per_query + 2)

        fetched = 0
        for vi, video in enumerate(videos[:clips_per_query + 1]):
            if fetched >= clips_per_query:
                break
            url = pick_best_clip_url([video], is_portrait=True, min_duration=MIN_CLIP_DURATION)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            clip_path = os.path.join(clips_dir, f"clip_{qi:02d}_{vi}.mp4")
            if download_clip(url, clip_path):
                clips.append((clip_path, False))
                fetched += 1
                print(f"    Downloaded portrait clip: {os.path.basename(clip_path)}", file=sys.stderr)
            time.sleep(0.2)  # polite rate limiting

        if fetched == 0:
            # Fallback: try landscape
            print(f"  No portrait clips found for '{query}', trying landscape fallback...", file=sys.stderr)
            landscape_videos = search_pexels_landscape(api_key, query, per_page=3)
            url = pick_best_clip_url(landscape_videos, is_portrait=False, min_duration=MIN_CLIP_DURATION)
            if url and url not in seen_urls:
                seen_urls.add(url)
                clip_path = os.path.join(clips_dir, f"clip_{qi:02d}_ls.mp4")
                if download_clip(url, clip_path):
                    clips.append((clip_path, True))  # True = is landscape
                    print(f"    Downloaded landscape fallback: {os.path.basename(clip_path)}", file=sys.stderr)

    return clips


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def get_video_dimensions(path):
    """Get video width and height using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", path],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
        if streams:
            return streams[0].get("width", 0), streams[0].get("height", 0)
    except Exception:
        pass
    return 0, 0


def convert_to_portrait(input_path, output_path):
    """
    Convert a clip to 1080x1920 portrait format.
    Portrait-native clips: scale+crop to 1080x1920.
    Landscape clips: blurred pillarbox (standard YouTube Shorts technique).
    """
    w, h = get_video_dimensions(input_path)
    is_portrait = h > w

    if is_portrait:
        # Portrait-native: scale to cover 1080x1920, then crop to exact dimensions
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an", output_path,
        ]
    else:
        # Landscape: blurred pillarbox background + centered foreground
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=25:5[bg];"
            "[0:v]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an", output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {result.stderr[-500:]}")


def build_concat_video(portrait_clips, audio_duration, output_path):
    """
    Concatenate portrait clips to fill the audio duration.
    Loops through available clips if needed.
    """
    if not portrait_clips:
        # Create black video as fallback
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30",
            "-t", str(audio_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return

    # Build concat list, looping clips if needed
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_file = f.name
        total = 0.0
        idx = 0
        while total < audio_duration:
            clip = portrait_clips[idx % len(portrait_clips)]
            f.write(f"file '{os.path.abspath(clip)}'\n")
            # Estimate each clip is ~10s; we overshoot and trim with -t
            total += 10.0
            idx += 1

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-t", str(audio_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-500:]}")
    finally:
        os.unlink(concat_file)


def escape_drawtext(text):
    """Escape special characters for ffmpeg drawtext filter."""
    # Order matters: backslash first, then others
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")   # replace apostrophe with right single quote (safest)
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return text


def burn_overlays_and_merge_audio(video_path, audio_path, hook_text, cta_text,
                                  audio_duration, output_path, font_path):
    """
    Burn hook overlay (first 3.5s) and CTA overlay (last 8s) onto video,
    then merge with audio.
    """
    hook_escaped = escape_drawtext(hook_text)
    cta_escaped = escape_drawtext(cta_text)
    cta_start = max(0, audio_duration - 8)

    font_arg = f":fontfile={font_path}" if font_path else ""

    filter_complex = (
        f"[0:v]drawtext="
        f"text='{hook_escaped}'"
        f":fontsize=68"
        f":fontcolor=white"
        f":x=(w-text_w)/2"
        f":y=(h/2)-140"
        f":box=1:boxcolor=black@0.65:boxborderw=20"
        f"{font_arg}"
        f":enable='between(t,0,3.5)',"
        f"drawtext="
        f"text='{cta_escaped}'"
        f":fontsize=46"
        f":fontcolor=white"
        f":x=(w-text_w)/2"
        f":y=h-200"
        f":box=1:boxcolor=black@0.7:boxborderw=14"
        f"{font_arg}"
        f":enable='between(t,{cta_start:.1f},{audio_duration:.1f})'[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg overlay+merge failed: {result.stderr[-500:]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Assemble a 9:16 YouTube Short")
    parser.add_argument("--pexels-queries", nargs="+", required=True,
                        help="Portrait-friendly Pexels search queries (1-3)")
    parser.add_argument("--audio-file", required=True, help="TTS voiceover MP3 for this short")
    parser.add_argument("--audio-duration", type=float, required=True,
                        help="Duration of the audio in seconds")
    parser.add_argument("--hook-overlay", required=True, help="On-screen hook text (shown at start)")
    parser.add_argument("--cta-overlay", required=True, help="On-screen CTA text (shown at end)")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    args = parser.parse_args()

    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.audio_file):
        print(f"ERROR: Audio file not found: {args.audio_file}", file=sys.stderr)
        sys.exit(1)

    if args.audio_duration <= 0:
        print("ERROR: audio-duration must be > 0", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    # Resolve font
    font_path = find_font()
    if font_path:
        print(f"Using font: {font_path}", file=sys.stderr)
    else:
        print("WARNING: No font file found, using ffmpeg built-in font", file=sys.stderr)

    # Working directory for intermediate files (alongside output)
    output_dir = os.path.dirname(os.path.abspath(args.output))
    base_name = os.path.splitext(os.path.basename(args.output))[0]
    clips_dir = os.path.join(output_dir, f"{base_name}_clips")
    raw_portrait_path = os.path.join(output_dir, f"{base_name}_raw.mp4")

    try:
        # Step 1: Fetch portrait clips from Pexels
        print(f"Fetching portrait footage ({len(args.pexels_queries)} queries)...", file=sys.stderr)
        raw_clips = fetch_portrait_clips(api_key, args.pexels_queries, clips_dir)

        if not raw_clips:
            print("WARNING: No clips fetched, will use black background", file=sys.stderr)

        # Step 2: Convert each clip to 1080x1920
        portrait_clips = []
        for raw_path, is_landscape in raw_clips:
            portrait_path = raw_path.replace(".mp4", "_portrait.mp4")
            try:
                print(f"  Converting to 1080x1920: {os.path.basename(raw_path)}", file=sys.stderr)
                convert_to_portrait(raw_path, portrait_path)
                portrait_clips.append(portrait_path)
            except Exception as e:
                print(f"  WARNING: Could not convert {raw_path}: {e}", file=sys.stderr)

        # Step 3: Concatenate portrait clips to match audio duration
        print(f"Concatenating {len(portrait_clips)} clips for {args.audio_duration:.1f}s...", file=sys.stderr)
        build_concat_video(portrait_clips, args.audio_duration, raw_portrait_path)

        # Step 4: Burn overlays and merge audio
        print("Burning overlays and merging audio...", file=sys.stderr)
        burn_overlays_and_merge_audio(
            raw_portrait_path,
            args.audio_file,
            args.hook_overlay,
            args.cta_overlay,
            args.audio_duration,
            args.output,
            font_path,
        )

        if not os.path.exists(args.output):
            print("ERROR: Output file was not created", file=sys.stderr)
            sys.exit(1)

        size_mb = os.path.getsize(args.output) / (1024 * 1024)
        print(f"Short assembled: {args.output} ({size_mb:.1f} MB)", file=sys.stderr)

    finally:
        # Cleanup intermediate files
        if os.path.exists(raw_portrait_path):
            os.remove(raw_portrait_path)
        if os.path.exists(clips_dir):
            shutil.rmtree(clips_dir, ignore_errors=True)

    print(args.output)


if __name__ == "__main__":
    main()
