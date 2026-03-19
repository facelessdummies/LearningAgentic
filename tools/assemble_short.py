"""
assemble_short.py — Assemble a 9:16 portrait YouTube Short from voiceover + Pexels portrait clips

Pure ffmpeg (no moviepy) for near-zero RAM usage. Fetches portrait-oriented stock clips,
converts them to 1080x1920, concatenates to match audio duration, then burns in
hook overlay text, CTA overlay text, and per-sentence captions.

Usage:
    python3 tools/assemble_short.py \
        --pexels-queries "person writing journal portrait" "morning sunlight window vertical" "focused desk portrait" \
        --audio-file .tmp/shorts/video_1_short_1_audio.mp3 \
        --audio-duration 52.3 \
        --hook-overlay "THIS HABIT TAKES 2 MINUTES" \
        --cta-overlay "Subscribe @GrowthDaily for daily insights" \
        --script "Did you know writing rewires your brain? Just two minutes every morning activates your brain spotlight. It helps you focus on what matters." \
        --output .tmp/shorts/video_1_short_1_final.mp4

Output (stdout): Path to output MP4
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

# Allow overriding ffmpeg binary via env var (e.g. FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

CLIPS_PER_QUERY = 3        # Portrait clips to fetch per query (up from 2)

# Aesthetic caption colors — vibrant but not neon, readable on video with a dark stroke
CAPTION_COLORS = [
    "#FFD54D",  # Mustard Yellow
    "#FF6E61",  # Coral Red
    "#FFB84D",  # Warm Orange
    "#AFE3CE",  # Mint Green
    "#4EB7AC",  # Teal
    "#F88379",  # Coral Pink
    "#FFC300",  # Golden Yellow
    "#FF9800",  # Deep Orange
    "#FFFFFF",  # White
]
MIN_CLIP_DURATION = 5      # Minimum clip duration in seconds to bother with
PEXELS_VIDEO_BASE = "https://api.pexels.com/videos"

MONTSERRAT_URL = (
    "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-Bold.ttf"
)
FONTS_CACHE_DIR = os.path.join(PROJECT_ROOT, ".tmp", "fonts")
MONTSERRAT_PATH = os.path.join(FONTS_CACHE_DIR, "Montserrat-Bold.ttf")

# System font fallbacks (most modern-aesthetic first)
FONT_PATHS = [
    MONTSERRAT_PATH,
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Futura.ttc",
    "/Library/Fonts/Futura.ttc",
    "/System/Library/Fonts/AvenirNext.ttc",
    "/System/Library/Fonts/Avenir Next.ttc",
    "/System/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# ---------------------------------------------------------------------------
# Font resolution — auto-download Montserrat Bold if needed
# ---------------------------------------------------------------------------

def download_font_if_needed():
    """Download Montserrat Bold to .tmp/fonts/ if not already cached."""
    if os.path.exists(MONTSERRAT_PATH):
        return MONTSERRAT_PATH
    os.makedirs(FONTS_CACHE_DIR, exist_ok=True)
    print("  Downloading Montserrat Bold font...", file=sys.stderr)
    try:
        resp = requests.get(MONTSERRAT_URL, timeout=30)
        resp.raise_for_status()
        with open(MONTSERRAT_PATH, "wb") as f:
            f.write(resp.content)
        print(f"  Font saved to {MONTSERRAT_PATH}", file=sys.stderr)
        return MONTSERRAT_PATH
    except Exception as e:
        print(f"  WARNING: Could not download Montserrat: {e}", file=sys.stderr)
        return None


def find_font():
    """Try to download Montserrat, then fall back to system fonts."""
    downloaded = download_font_if_needed()
    if downloaded and os.path.exists(downloaded):
        return downloaded
    for path in FONT_PATHS[1:]:  # skip MONTSERRAT_PATH (already tried)
        if os.path.exists(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Caption / text helpers
# ---------------------------------------------------------------------------

def split_sentences(text):
    """Split script text into sentences on . ! ? boundaries."""
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


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
            portrait_files = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0)]
            best_res = max((f.get("height", 0) or 0 for f in portrait_files), default=0) if portrait_files else 0
        else:
            best_res = max((f.get("width", 0) or 0 for f in files), default=0)
        return (1 if duration >= min_duration else 0, best_res, duration)

    videos_sorted = sorted(videos, key=score, reverse=True)
    for video in videos_sorted:
        files = video.get("video_files", [])
        if is_portrait:
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


def fetch_portrait_clips(api_key, queries, clips_dir, clips_per_query=CLIPS_PER_QUERY, min_clips=0):
    """
    Fetch portrait clips for each query. Returns list of downloaded file paths.
    Waterfall: portrait search → landscape search (with pillarbox flag) → skip.
    min_clips: if total fetched < min_clips, increase per_page on remaining queries.
    """
    os.makedirs(clips_dir, exist_ok=True)
    clips = []  # list of (path, is_landscape_fallback)
    seen_urls = set()

    for qi, query in enumerate(queries):
        # Dynamically increase per_page if we still need more clips
        remaining_needed = max(0, min_clips - len(clips))
        remaining_queries = len(queries) - qi
        effective_per_query = max(
            clips_per_query,
            math.ceil(remaining_needed / max(remaining_queries, 1))
        )

        print(f"  Fetching portrait clips for query: '{query}' (target: {effective_per_query})...", file=sys.stderr)
        videos = search_pexels_portrait(api_key, query, per_page=effective_per_query + 2)

        fetched = 0
        for vi, video in enumerate(videos[:effective_per_query + 1]):
            if fetched >= effective_per_query:
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


def get_clip_duration(path):
    """Get clip duration in seconds using ffprobe. Fallback: 10.0."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 10.0


def convert_to_portrait(input_path, output_path):
    """
    Convert a clip to 1080x1920 portrait format.
    Portrait-native clips: scale+crop to 1080x1920.
    Landscape clips: blurred pillarbox (standard YouTube Shorts technique).
    """
    w, h = get_video_dimensions(input_path)
    is_portrait = h > w

    if is_portrait:
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        cmd = [
            FFMPEG_BIN, "-y", "-i", input_path,
            "-vf", vf,
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an", output_path,
        ]
    else:
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=25:5[bg];"
            "[0:v]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )
        cmd = [
            FFMPEG_BIN, "-y", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an", output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {result.stderr[-500:]}")


def build_concat_video(portrait_clips, audio_duration, output_path):
    """
    Concatenate portrait clips to fill the audio duration using actual clip durations.
    Loops through available clips if needed.
    """
    if not portrait_clips:
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30",
            "-t", str(audio_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return

    # Use actual clip durations to decide how many clips to add
    clip_durations = [get_clip_duration(c) for c in portrait_clips]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_file = f.name
        total = 0.0
        idx = 0
        while total < audio_duration:
            clip = portrait_clips[idx % len(portrait_clips)]
            dur = clip_durations[idx % len(clip_durations)]
            f.write(f"file '{os.path.abspath(clip)}'\n")
            total += dur
            idx += 1

    try:
        cmd = [
            FFMPEG_BIN, "-y",
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


def build_sentence_concat_video(portrait_clips, sentence_durations, output_path):
    """
    Build video with clip switches at sentence boundaries.
    Each sentence gets exactly its required duration covered by one or more clips.
    If a clip is shorter than the sentence duration, additional clips fill the gap.
    clip_index advances globally so we never unnecessarily repeat the same clip.
    """
    if not portrait_clips:
        total = sum(sentence_durations)
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30",
            "-t", str(total),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return

    tmp_dir = tempfile.mkdtemp()
    all_segments = []  # ordered list of trimmed segment file paths
    clip_index = 0

    try:
        for sent_i, sent_dur in enumerate(sentence_durations):
            remaining = sent_dur
            sent_segments = []

            while remaining > 0.05:  # stop when < 50ms left (avoid tiny tail clips)
                clip = portrait_clips[clip_index % len(portrait_clips)]
                clip_dur = get_clip_duration(clip)
                use_dur = min(remaining, clip_dur)

                seg_path = os.path.join(tmp_dir, f"seg_{sent_i:03d}_{len(sent_segments):02d}.mp4")
                cmd = [
                    FFMPEG_BIN, "-y", "-i", clip,
                    "-t", str(use_dur),
                    "-r", "30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-an", seg_path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"  WARNING: Could not trim clip for sentence {sent_i}: {result.stderr[-200:]}", file=sys.stderr)
                    seg_path = clip  # use full clip as fallback
                    remaining = 0   # accept imperfect duration for this sentence
                else:
                    # Use actual output duration (may be shorter than use_dur if clip ran out)
                    actual_dur = get_clip_duration(seg_path)
                    remaining -= actual_dur

                sent_segments.append(seg_path)
                clip_index += 1

            all_segments.extend(sent_segments)

        # Concat all segments, trim to exact total audio duration
        total_audio = sum(sentence_durations)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            concat_file = f.name
            for seg in all_segments:
                f.write(f"file '{os.path.abspath(seg)}'\n")

        try:
            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "concat", "-safe", "0", "-i", concat_file,
                "-t", str(total_audio),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-an", output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg sentence-concat failed: {result.stderr[-500:]}")
        finally:
            os.unlink(concat_file)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def burn_overlays_and_merge_audio(video_path, audio_path, hook_text, cta_text,
                                  caption_sentences, audio_duration, output_path, font_path,
                                  caption_color="white"):
    """
    Burn overlays and captions onto video, then merge with audio.

    Layout (1080x1920):
    - Hook text:    y=200, font 50px, shown 0–3.5s, white with dark box
    - CTA text:     y=200, font 38px, shown last 8s, white with dark box
    - Captions:     y=h*0.75, font 46px, word-count-proportional timing,
                    colored (caption_color) with dark stroke — no background box

    All text written to temp files (textfile= option) to avoid ffmpeg filter escaping issues.
    Caption timing is proportional to word count so captions track the spoken audio more closely.
    """
    cta_start = max(0, audio_duration - 8)
    font_arg = f":fontfile={font_path}" if font_path else ""
    text_files = []

    def make_text_file(text, max_chars):
        """Wrap text and write to a temp file with real newlines. Returns file path."""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tf.write("\n".join(lines))
        tf.close()
        text_files.append(tf.name)
        return tf.name

    # --- Hook overlay (top, first 3.5s) ---
    hook_file = make_text_file(hook_text, max_chars=22)
    filter_parts = [
        f"[0:v]drawtext="
        f"textfile='{hook_file}'"
        f":fontsize=50"
        f":fontcolor=white"
        f":x=(w-text_w)/2"
        f":y=200"
        f":box=1:boxcolor=black@0.65:boxborderw=16"
        f":fix_bounds=1"
        f"{font_arg}"
        f":enable='between(t,0,3.5)'"
    ]

    # --- CTA overlay (top, last 8s) ---
    cta_file = make_text_file(cta_text, max_chars=28)
    filter_parts.append(
        f"drawtext="
        f"textfile='{cta_file}'"
        f":fontsize=38"
        f":fontcolor=white"
        f":x=(w-text_w)/2"
        f":y=200"
        f":box=1:boxcolor=black@0.7:boxborderw=12"
        f":fix_bounds=1"
        f"{font_arg}"
        f":enable='between(t,{cta_start:.1f},{audio_duration:.1f})'"
    )

    # --- Captions (word-count proportional timing, anchored above bottom edge) ---
    if caption_sentences:
        word_counts = [len(s.split()) for s in caption_sentences]
        total_words = sum(word_counts) or 1
        t = 0.0
        for i, sentence in enumerate(caption_sentences):
            sent_dur = (word_counts[i] / total_words) * audio_duration
            t_start = t
            t_end = t + sent_dur
            t += sent_dur
            cap_file = make_text_file(sentence, max_chars=26)
            filter_parts.append(
                f"drawtext="
                f"textfile='{cap_file}'"
                f":fontsize=46"
                f":fontcolor={caption_color}"
                f":bordercolor=black:borderw=3"
                f":x=(w-text_w)/2"
                f":y=h*0.75"
                f":fix_bounds=1"
                f"{font_arg}"
                f":enable='between(t,{t_start:.2f},{t_end:.2f})'"
            )

    filter_complex = ",".join(filter_parts) + "[v]"

    cmd = [
        FFMPEG_BIN, "-y",
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
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg overlay+merge failed: {result.stderr[-500:]}")
    finally:
        for tf_path in text_files:
            try:
                os.unlink(tf_path)
            except Exception:
                pass


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
    parser.add_argument("--script", default="",
                        help="Full spoken script text for auto-captioning and clip-per-sentence editing")
    parser.add_argument("--caption-color", default=None,
                        help="Hex color for captions (e.g. #FFD54D). Defaults to random from palette.")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    args = parser.parse_args()

    caption_color = args.caption_color if args.caption_color else random.choice(CAPTION_COLORS)
    print(f"Caption color: {caption_color}", file=sys.stderr)

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

    # Parse sentences from script
    caption_sentences = split_sentences(args.script) if args.script.strip() else []
    num_sentences = len(caption_sentences)
    print(f"Script has {num_sentences} sentences → targeting {num_sentences} clips", file=sys.stderr)

    # Resolve font
    font_path = find_font()
    if font_path:
        print(f"Using font: {font_path}", file=sys.stderr)
    else:
        print("WARNING: No font file found, using ffmpeg built-in font", file=sys.stderr)

    # Working directory for intermediate files
    output_dir = os.path.dirname(os.path.abspath(args.output))
    base_name = os.path.splitext(os.path.basename(args.output))[0]
    clips_dir = os.path.join(output_dir, f"{base_name}_clips")
    raw_portrait_path = os.path.join(output_dir, f"{base_name}_raw.mp4")

    try:
        # Step 1: Fetch portrait clips — ensure at least 1 per sentence
        min_clips = max(num_sentences, len(args.pexels_queries))
        print(f"Fetching portrait footage ({len(args.pexels_queries)} queries, min {min_clips} clips)...", file=sys.stderr)
        raw_clips = fetch_portrait_clips(api_key, args.pexels_queries, clips_dir,
                                         min_clips=min_clips)

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

        # Step 3: Build video — sentence-aligned if script provided, else simple concat
        print(f"Building video with {len(portrait_clips)} clips...", file=sys.stderr)
        if caption_sentences and portrait_clips:
            # Distribute audio_duration evenly across sentences for clip trimming
            per_sentence = args.audio_duration / num_sentences
            sentence_durations = [per_sentence] * num_sentences
            print(f"  Sentence-aligned mode: {num_sentences} clips × {per_sentence:.1f}s each", file=sys.stderr)
            build_sentence_concat_video(portrait_clips, sentence_durations, raw_portrait_path)
        else:
            print(f"  Simple concat mode (no script provided)", file=sys.stderr)
            build_concat_video(portrait_clips, args.audio_duration, raw_portrait_path)

        # Step 4: Burn overlays + captions and merge audio
        print("Burning overlays, captions, and merging audio...", file=sys.stderr)
        burn_overlays_and_merge_audio(
            raw_portrait_path,
            args.audio_file,
            args.hook_overlay,
            args.cta_overlay,
            caption_sentences,
            args.audio_duration,
            args.output,
            font_path,
            caption_color=caption_color,
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
