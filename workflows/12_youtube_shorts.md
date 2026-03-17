# Workflow 12: YouTube Shorts Production & Scheduling

## Objective

For each full video published (Mon/Wed/Fri), produce 2 independent YouTube Shorts (<60s, 9:16 portrait) and schedule them to go live the following day at 7am and 7pm IST. Shorts have their own hook, engaging content, and curiosity-driving CTA — they are not raw cuts of the full video.

## Trigger

Triggered by `agents/shorts_scheduler.py` via cron on Mon/Wed/Fri at 10pm IST (16:30 UTC) — the same days the full videos publish. This spreads YouTube API quota usage (~3,200 units/day) instead of uploading all 6 shorts on publishing day (~9,600 units at once), keeping each day well under the 10,000 unit daily limit.

Cron entry to add:
```
30 16 * * 1,3,5   /path/to/venv/bin/python3 /path/to/agents/shorts_scheduler.py >> /path/to/.tmp/shorts_scheduler.log 2>&1
```

`shorts_scheduler.py` reads state, finds videos whose `scheduled_publish_at` falls on today (UTC date), and launches `shorts_agent.py` for each as a non-blocking background process. Failures do not affect the main publishing pipeline.

## Inputs

| Input | Source |
|---|---|
| `--video-key` | Video key in `state.json` (e.g. `video_1`) |
| `--full-video-publish-at` | ISO UTC timestamp of full video's scheduled publish time |
| `NICHE` | `.env` |
| `CHANNEL_NAME` | `.env` |
| `PEXELS_API_KEY` | `.env` |
| `ANTHROPIC_API_KEY` | `.env` |
| `OPENAI_API_KEY` | `.env` |
| `APPROVAL_EMAIL` | `.env` (for notification email) |

## Schedule Formula

| Full video publish | Short 1 | Short 2 |
|---|---|---|
| Monday 9am IST (03:30 UTC) | **Tuesday 7am IST** (01:30 UTC) = +22h | **Tuesday 7pm IST** (13:30 UTC) = +34h |
| Wednesday 9am IST | **Thursday 7am IST** | **Thursday 7pm IST** |
| Friday 9am IST | **Saturday 7am IST** | **Saturday 7pm IST** |

Rationale: Shorts publish the day after the full video drives fresh traffic. Morning and evening slots maximise reach across timezones.

## Short Content Structure

Each short (~50-55s, 90-115 words spoken) follows a 3-part structure:

1. **Hook (8-12s):** A surprising, bold, or counterintuitive opening statement. Starts mid-idea — no "In this video" or "Today". Immediately grabs attention.
2. **Core (30-38s):** Full delivery of the micro-insight. Self-contained — viewers learn something real without needing to watch the full video.
3. **CTA (6-10s):** Curiosity-driving close referencing the broader content on the channel. e.g. "This is just one of 5 habits — subscribe to catch the rest @ChannelName"

Visual overlays:
- **hook_overlay:** 4-6 words ALL CAPS shown during first 3.5s (e.g. `THIS HABIT TAKES 2 MINUTES`)
- **cta_overlay:** Subscribe prompt shown during last 8s

## Tools Called (in order)

### Step 1: Generate Short Scripts
```
tools/generate_short_scripts.py
  --script-file     .tmp/scripts/video_N_script.json
  --video-title     "Full video title"
  --niche           "Self Development"
  --channel-name    "Growth Daily"
  --output          .tmp/shorts/video_N_shorts_plan.json
```

Uses Claude Sonnet to analyse the full video's segments and write 2 independent short scripts. Selects `point_N` and `pattern_interrupt` segment types (avoids hook/bridge/cta — context-dependent). Output: JSON with 2 short specs.

### Step 2 (per short): Generate Voiceover
```
tools/generate_voiceover.py   ← EXISTING TOOL
  --script-file     .tmp/shorts/video_N_short_M_script.json
  --output          .tmp/shorts/video_N_short_M_audio.mp3
```

Uses OpenAI TTS-1-HD. The short script JSON contains a single segment with the full `spoken_script`. Returns audio duration in seconds on stdout.

### Step 3 (per short): Assemble Portrait Video
```
tools/assemble_short.py
  --pexels-queries  "query 1" "query 2" "query 3"
  --audio-file      .tmp/shorts/video_N_short_M_audio.mp3
  --audio-duration  52.3
  --hook-overlay    "ON-SCREEN HOOK TEXT"
  --cta-overlay     "Subscribe @ChannelName for more"
  --output          .tmp/shorts/video_N_short_M_final.mp4
```

Pure ffmpeg — no moviepy. Fetches portrait-oriented Pexels clips, converts to 1080×1920 (portrait-native scale or blurred pillarbox fallback), concatenates to match audio, burns in overlays. Output: 1080×1920 H.264/AAC MP4, ≤60s.

### Step 4 (per short): Upload to YouTube
```
tools/upload_to_youtube.py   ← EXISTING TOOL
  --video-file    .tmp/shorts/video_N_short_M_final.mp4
  --script-file   .tmp/shorts/video_N_short_M_meta.json
  --privacy       private
```

Uploads as `private` (not unlisted — will be scheduled next). Returns `{video_id} {url}` on stdout.

### Step 5 (per short): Schedule
```
tools/publish_youtube_video.py   ← EXISTING TOOL
  --video-id      {short_yt_id}
  --publish-at    {short_N_publish_at}
```

Sets YouTube scheduled publish time. Short goes from `Private` to `Public` automatically at the scheduled time.

## State Updates

New keys written to `state.videos[video_key]` via `manage_state.py --write`:

```json
{
  "shorts_plan_path": ".tmp/shorts/video_1_shorts_plan.json",
  "shorts": {
    "short_1": {
      "youtube_video_id": "aB3xYZ123",
      "youtube_url": "https://youtube.com/watch?v=aB3xYZ123",
      "short_title": "The 2-Minute Habit That Rewires Your Brain #Shorts",
      "scheduled_publish_at": "2026-03-17T01:30:00Z",
      "source_segment_index": 3,
      "source_segment_type": "point_1",
      "output_path": ".tmp/shorts/video_1_short_1_final.mp4",
      "status": "scheduled",
      "created_at": "2026-03-16T04:15:00Z"
    },
    "short_2": { "...": "..." }
  }
}
```

State is updated **incrementally** after each short (not just at the end), enabling resume on crash.

## Output

- 2 YouTube Shorts uploaded and scheduled per full video (6 shorts/week total)
- Notification email sent to `APPROVAL_EMAIL` with short URLs and schedule times
- `state.videos[video_key].shorts` populated with metadata

## Resume Behaviour

`shorts_agent.py` checks `state.videos[video_key].shorts` at startup. If a short already has `status="scheduled"`, it is skipped. Safe to re-run after a crash.

## Intermediate Files (`.tmp/shorts/`)

```
video_N_shorts_plan.json          ← Claude's 2 short specs (persisted for resume)
video_N_short_M_script.json       ← Single-segment script for generate_voiceover
video_N_short_M_audio.mp3         ← TTS voiceover for this short
video_N_short_M_clips/            ← Portrait Pexels clips (auto-deleted after assembly)
video_N_short_M_final.mp4         ← Final 1080×1920 short (kept for reference)
video_N_short_M_meta.json         ← YouTube upload metadata
```

## Cost (per week, 6 shorts)

| Step | API | Cost |
|---|---|---|
| 3× generate_short_scripts.py | Claude Sonnet (~$0.024/call) | ~$0.072 |
| 6× generate_voiceover.py (~650 chars each) | OpenAI TTS-1-HD | ~$0.12 |
| 6× Pexels portrait fetches | Pexels free API | $0 |
| 6× ffmpeg assembly | Local | $0 |
| 6× YouTube uploads + scheduling | YouTube API (quota) | $0 |
| **Total additional cost per week** | | **~$0.20** |

## YouTube API Quota Note

Each short upload consumes 1,600 API units. By triggering via `shorts_scheduler.py` cron (Mon/Wed/Fri at 10pm IST) instead of all at once on publishing day, quota usage per day stays well under the 10,000 unit limit:

| Day | Operations | Units |
|---|---|---|
| Publishing day (Sunday/Monday) | 3 full video schedules (50 units each) + polls | ~200 |
| Monday night | 2 short uploads + schedules | ~3,350 |
| Wednesday night | 2 short uploads + schedules | ~3,350 |
| Friday night | 2 short uploads + schedules | ~3,350 |

If quota errors occur, they are logged to `state.errors` and remaining shorts are skipped (core pipeline unaffected). Safe to re-run `shorts_scheduler.py` manually — already-scheduled shorts are skipped.

## RAM Usage

All shorts processing uses ffmpeg subprocesses — no Python video data in memory. Peak RAM per `shorts_agent.py` process: ~80MB. Three agents running concurrently (one per full video): ~240MB total — negligible.

## Edge Cases

| Scenario | Handling |
|---|---|
| Pexels returns no portrait clips | Fallback to landscape query → blurred pillarbox; final fallback: black background |
| Short voiceover > 60s | Log warning and continue (YouTube technically allows up to 60s; spoken_script prompt targets 90-115 words ≈ 50-55s) |
| YouTube quota exhausted | Log error, skip remaining shorts, email user |
| Crash mid-production | Resume check at startup skips already-scheduled shorts |
| No `captions.json` | Not needed — shorts use fresh TTS voiceover, not slices of the full video audio |
| Font file not found | ffmpeg falls back to built-in Liberation Sans (no crash) |
