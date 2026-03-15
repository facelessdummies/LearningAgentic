# Workflow: Content Production Pipeline

## Objective

For each approved video idea, generate a complete script, AI voiceover, fetch stock footage, assemble the final video, and upload it to YouTube as unlisted for review.

## Required Inputs

- `state.approved_idea_ids` — list of approved idea numbers (from state.json)
- `.tmp/ideas.json` — the ideas file from this week's run
- `ANTHROPIC_API_KEY` — Claude Sonnet for script generation
- `OPENAI_API_KEY` — OpenAI TTS for voiceover
- `PEXELS_API_KEY` — primary stock footage source
- `PIXABAY_API_KEY` — optional secondary footage source (leave blank to disable)
- `credentials.json` + `token.json` — YouTube OAuth for upload

## Tools Used (per video)

1. `tools/generate_retention_script.py --idea-id N --ideas-file .tmp/ideas.json --output .tmp/scripts/video_N_script.json`
2. `tools/generate_voiceover.py --script-file ... --output .tmp/audio/video_N_voiceover.mp3`
3. `tools/fetch_pexels_footage.py --script-file ... --output-dir .tmp/footage/video_N/`
4. `tools/assemble_video.py --script-file ... --audio-file ... --footage-dir ... --output .tmp/output/video_N_final.mp4`
5. `tools/upload_to_youtube.py --video-file ... --script-file ... --privacy unlisted`

After all videos:

6. `tools/send_email.py` (review email with unlisted links)
7. `tools/manage_state.py --set-phase awaiting_video_approval`

## Steps (executed by `agents/production_agent.py`)

1. Load state, get `approved_idea_ids`
2. Set phase to `production_in_progress`
3. For each approved idea:
   - **Script**: Claude Sonnet generates 8-10 minute segmented script
     - Each segment has: text, visual_cue, overlay_text, pexels_search_queries (array of 3), duration_estimate
     - Structure: Hook → Bridge → Context → 4 Points (with pattern interrupts between) → Engagement → CTA
     - `pexels_search_queries` has 3 varied B-roll queries per segment (specific → complementary → broad fallback)
   - **Voiceover**: OpenAI TTS `tts-1` model
     - Splits long text at sentence boundaries (4096 char API limit)
     - Concatenates chunks with pydub
     - Actual duration written to state
   - **Footage**: multi-source waterfall (Pexels primary, Pixabay optional)
     - Fetches up to 3 clips per segment (one per query in `pexels_search_queries`)
     - Clips saved as `clip_001_0.mp4`, `clip_001_1.mp4`, `clip_001_2.mp4`
     - Manifest value is a list if multiple clips downloaded, string if only one (backward compat)
     - Waterfall order per query:
       1. Original query → Pexels
       2. Original query → Pixabay  *(skipped if PIXABAY_API_KEY not set)*
       3. Simplified query → Pexels → Pixabay  *(only if steps 1+2 both empty)*
       4. Channel/niche name → Pexels → Pixabay  *(last resort)*
       5. Static photo fallback via Pexels  *(if no video found at all)*
     - Pixabay quality selection: large > medium > small
     - Pixabay steps silently skipped if `PIXABAY_API_KEY` not set — fully backward compatible
   - **Assembly**: moviepy renders final video
     - 1920x1080, 30fps, H.264/AAC
     - **Rapid cuts every 3–6 seconds** — each segment splits into multiple sub-clips cycling through downloaded footage
     - Ken Burns zoom alternates direction (zoom-in / zoom-out) per cut for dynamic feel
     - Hard cuts between sub-clips (no cross-fade) for modern YouTube pacing
     - Text overlays span the full segment duration regardless of cuts
     - Optional background music at 10% volume
     - Render time: ~8-15 minutes per 10-min video
   - **Upload**: YouTube resumable upload, unlisted privacy
     - Metadata from script: title, description, tags, category
4. Update state after each video (incremental saves)
5. Compose review email with all unlisted YouTube links
6. Send email, save message ID to state
7. Set phase to `awaiting_video_approval`

## Expected Outputs

- `.tmp/scripts/video_N_script.json` — script per video
- `.tmp/audio/video_N_voiceover.mp3` — voiceover per video
- `.tmp/footage/video_N/` — downloaded stock clips + manifest
- `.tmp/output/video_N_final.mp4` — final rendered video
- YouTube videos uploaded as unlisted
- Review email sent with unlisted links

## Cost Per Run (3 videos)

| Step | Model/API | Est. Cost |
|---|---|---|
| Script (3x) | Claude Sonnet | ~$0.15 |
| Voiceover (3x ~9k chars) | OpenAI TTS `tts-1` | ~$0.40 |
| Footage | Pexels + Pixabay (both free) | $0 |
| Assembly | local CPU | $0 |
| Upload | YouTube API | $0 |
| **Total per week** | | **~$0.55** |

## Triggered By

`agents/approval_poller.py` when `phase = awaiting_idea_approval` and a valid approval reply is found.

## Edge Cases & Notes

**moviepy out of memory on long videos:**
- If video is 15+ segments, process in batches of 5 segments, render partial files, then concatenate
- Add `--batch-size 5` argument to `assemble_video.py` if OOM errors occur
- Monitor: `Activity Monitor` during render

**Footage irrelevant or missing:**
- The script generation prompt explicitly instructs Claude to write concrete, specific queries
- "person journaling coffee desk morning" not "productivity"
- If still failing: edit the `pexels_search_queries` arrays in `.tmp/scripts/video_N_script.json` manually and re-run `fetch_pexels_footage.py`
- Old scripts with `pexels_search_query` (single string) still work — backward compatible
- Add `PIXABAY_API_KEY` to `.env` to enable Pixabay as a secondary source (free at pixabay.com/api/docs/)

**OpenAI TTS character limit:**
- API limit: 4096 chars per request
- `generate_voiceover.py` handles splitting automatically at sentence boundaries
- If pydub throws an error: `brew install ffmpeg` and ensure PATH is set

**YouTube upload quota:**
- Each upload costs 1600 API units
- Daily limit: 10,000 units
- 3 uploads per day = 4800 units — well within limit
- If quota exceeded: error message says to wait 24h; quota resets midnight Pacific

**YouTube video still processing:**
- `publish_youtube_video.py` waits up to 10 minutes for processing before publishing
- If video is still processing after 10 min, it attempts publish anyway (YouTube usually handles it)

**Partial failure (some videos fail, some succeed):**
- `production_agent.py` continues to next video on failure
- Failed video IDs are logged in state.errors
- Review email is still sent for successfully produced videos
- Failed videos noted in email body

**Resume after crash:**
- Each tool checks if its output file already exists and skips if present
- `fetch_pexels_footage.py` skips already-downloaded clips (works for both Pexels and Pixabay clips)
- Re-run `production_agent.py` to resume from where it crashed
