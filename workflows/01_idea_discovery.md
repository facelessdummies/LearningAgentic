# Workflow: Weekly Idea Discovery

## Objective

Every Sunday night, scrape YouTube for trending content in the configured niche, use Claude to generate 10 original video ideas, write them to a Google Sheet, and email the user for approval.

## Required Inputs

- `NICHE` — from `.env` (e.g., "Self Development")
- `YOUTUBE_API_KEY` — YouTube Data API v3 key (read-only)
- `ANTHROPIC_API_KEY` — for Claude Haiku idea generation
- `APPROVAL_EMAIL` — where to send the ideas email
- `credentials.json` + `token.json` — Google OAuth for Sheets + Gmail

## Tools Used (in order)

1. `tools/manage_state.py --reset` — reset state for new week
2. `tools/scrape_youtube_trending.py --niche "Self Development" --max-results 50`
3. `tools/generate_ideas.py --scraped-file .tmp/scraped_videos.json --niche "Self Development" --count 10`
4. `tools/write_ideas_to_sheet.py --ideas-file .tmp/ideas.json`
5. `tools/send_email.py --to $APPROVAL_EMAIL --subject "..." --body "..."`
6. `tools/manage_state.py --write '{"ideas_email_message_id": "..."}'`
7. `tools/manage_state.py --set-phase awaiting_idea_approval`

## Steps (executed by `agents/idea_agent.py`)

1. Reset state machine for the new week (`--reset`)
2. Scrape top 50 trending YouTube videos from the past 14 days for the niche
   - Runs 4 search queries: niche name, "niche tips", "niche motivation", "how to niche"
   - Also fetches 5 recent videos from top 5 niche channels
   - Batch fetches stats (views, likes, comments) for all videos
   - Writes to `.tmp/scraped_videos.json`
3. Generate 10 ideas using Claude Haiku
   - Analyzes top 20 videos by view count
   - Produces ideas with: title, hook, angle, target_emotion, potential (High/Medium/Low), pexels_search_query
   - Writes to `.tmp/ideas.json`
4. Write ideas to Google Sheet (creates new sheet if `GOOGLE_SHEET_ID` is empty)
   - Formatted with headers, frozen row, color-coded Potential column
   - New sheet ID is saved back to `.env` automatically
5. Compose approval email with inline idea preview and instructions
6. Send email via Gmail API, capture message ID
7. Save message ID and timestamp to state for polling later
8. Set phase to `awaiting_idea_approval`

## Expected Outputs

- `.tmp/scraped_videos.json` — raw YouTube data
- `.tmp/ideas.json` — 10 generated ideas
- Google Sheet updated/created with ideas
- Approval request email sent to `APPROVAL_EMAIL`
- `state.phase = "awaiting_idea_approval"`

## Triggered By

Cron: `0 22 * * 0` (Sunday 10pm)

## Edge Cases & Notes

**YouTube API quota exceeded:**
- Each run uses ~150-300 units (well within 10k/day free quota)
- If quota is exceeded (rare), the agent sends an error email and exits
- Quota resets at midnight Pacific time

**Google Sheets auth expiry:**
- `google-auth-oauthlib` handles token auto-refresh
- If refresh token is revoked, user sees clear error: "Run setup.sh to re-authenticate"

**Claude API timeout:**
- `generate_ideas.py` retries once on malformed JSON
- On second failure, exits with error — idea_agent sends error notification email

**Mac asleep at 10pm:**
- The cron job uses `caffeinate` consideration: if Mac is asleep, job queues until wake
- Or: configure Energy Saver to never sleep on Sunday nights
- Impact is low: ideas just arrive Monday morning instead of Sunday night

**Google Sheet ID changes:**
- If `GOOGLE_SHEET_ID` in `.env` is empty, a new sheet is created every week
- To reuse the same sheet (appending weeks), keep the ID in `.env`
- Current behavior: creates new sheet per week (cleaner for review)

**First-ever run:**
- `write_ideas_to_sheet.py` creates the sheet and saves ID to `.env`
- Subsequent runs update the same sheet (unless ID is cleared)
