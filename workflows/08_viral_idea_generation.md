# Workflow 08: Viral Idea Generation

## Objective
Generate high-potential video ideas using a viral trend researcher prompt (Claude Sonnet).
This is an on-demand supplement to the weekly cron `idea_agent.py`. Use it for:
- Mid-week brainstorming on a specific topic
- Generating deeper, richer ideas with viral potential analysis
- Exploring a niche topic before committing to production

## When to Run
- Manually, any time you want viral ideas
- After discovering a trending topic you want to capitalize on
- When the weekly cron ideas feel generic or low-potential

## Agent
`agents/viral_idea_agent.py`

## Tools Used (in order)
1. `tools/scrape_youtube_trending.py` — optional, fetches trending context
2. `tools/generate_viral_ideas.py` — Claude Sonnet viral researcher prompt
3. `tools/write_ideas_to_sheet.py` — writes to "Ideas" tab in Google Sheet
4. `tools/send_email.py` — sends ideas email for review

## Required Inputs
- `NICHE` in `.env` (or pass `--niche`)
- `ANTHROPIC_API_KEY` in `.env`
- `APPROVAL_EMAIL` in `.env`
- `GOOGLE_SHEET_ID` in `.env` (optional, for sheet writing)
- OAuth `token.json` (for sheet + email)

## Usage Examples
```bash
# Generate viral ideas for a specific topic
python3 agents/viral_idea_agent.py --topic "morning routines"

# Generate for full niche, with YouTube scraping
python3 agents/viral_idea_agent.py --niche "Self Development" --count 10

# Use existing scraped data (saves API quota)
python3 agents/viral_idea_agent.py --scraped-file .tmp/scraped_videos.json

# Integrate with the approval pipeline (sets state → approval_poller picks up)
python3 agents/viral_idea_agent.py --topic "discipline habits" --integrate-pipeline

# Dry run (no email, no Claude call — just validates setup)
python3 agents/viral_idea_agent.py --dry-run
```

## Output
- `.tmp/viral_ideas.json` — ideas with all fields including `content_format` and `viral_reason`
- Google Sheet "Ideas" tab updated
- Email sent to `APPROVAL_EMAIL`

## Output Schema
Each idea:
```json
{
  "id": 1,
  "title": "scroll-stopping title",
  "hook": "first 10-second hook",
  "angle": "unique angle vs existing content",
  "target_emotion": "curiosity|fear|surprise|aspiration|inspiration",
  "potential": "High|Medium|Low",
  "pexels_search_query": "concrete noun phrase",
  "content_format": "tutorial|breakdown|story|experiment|listicle",
  "viral_reason": "why viral potential right now"
}
```

## Differences vs Weekly idea_agent.py
| | `idea_agent.py` (weekly cron) | `viral_idea_agent.py` (on-demand) |
|---|---|---|
| Schedule | Automatic, every Sunday 10pm | Manual only |
| Prompt | General trend analysis | Viral researcher, 100k+ focus |
| Output file | `.tmp/ideas.json` | `.tmp/viral_ideas.json` |
| Pipeline state | Resets state, sets to `awaiting_idea_approval` | Independent by default |
| Sheet tab | Overwrites "Ideas" tab | Overwrites "Ideas" tab |

Note: The weekly `idea_agent.py` was upgraded to use `generate_viral_ideas.py` as of this update,
so the weekly cron automatically benefits from the viral researcher prompt.

## Edge Cases
- **No YouTube API quota**: Pass `--scraped-file .tmp/scraped_videos.json` to reuse existing data
- **Topic too niche**: Claude will still generate ideas but with lower trending context; quality depends on prompt
- **Sheet write fails**: Ideas still saved locally to `.tmp/viral_ideas.json`
- **Email fails**: Ideas still saved locally; check Gmail OAuth token

## Integration with Pipeline
To feed viral ideas into the production pipeline:
```bash
# Option A: Use --integrate-pipeline flag
python3 agents/viral_idea_agent.py --topic "morning routine" --integrate-pipeline
# Then reply to the email with APPROVE: 1, 3 → approval_poller.py picks it up

# Option B: Manually copy the ideas to .tmp/ideas.json
cp .tmp/viral_ideas.json .tmp/ideas.json
python3 tools/manage_state.py --set-phase awaiting_idea_approval
```
