# PhantomBuster API — Claude Code project

## Interpreter
`~/phantombuster-api/.venv/bin/python`

## Environment variable
`PHANTOMBUSTER_API_KEY` — ask me when you need it; never hard-code it.

## How to run a one-off call
```bash
# Key is stored in .env — load it automatically:
source ~/phantombuster-api/.env && ~/phantombuster-api/.venv/bin/python -c "
import phantombuster_api as pb
print(pb.list_phantoms())
"
```

## Available functions in `phantombuster_api.py`

| Function | What it does |
|---|---|
| `list_phantoms()` | List all agents in the account |
| `get_phantom(agent_id)` | Fetch metadata for one agent |
| `launch_phantom(agent_id, args=None)` | Launch an agent (optionally override its argument) |
| `stop_phantom(agent_id)` | Abort a running agent |
| `fetch_output(agent_id)` | Get the latest result object from an agent |
| `save_phantom_argument(agent_id, args)` | Persist a new default argument for an agent |
| `get_phantom_status(agent_id)` | Return current launch status + last end message |
| `delete_phantom_output(agent_id)` | Delete all stored output for an agent |

## Profile ranking (run after PhantomBuster finishes)

```bash
# Fetch PhantomBuster output + classify all profiles in one go:
source ~/phantombuster-api/.env && \
  ~/phantombuster-api/.venv/bin/python rank_profiles.py --phantom-id $PB_WAVE1_ENRICHER_ID

# Or classify a local CSV you already have:
source ~/phantombuster-api/.env && \
  ~/phantombuster-api/.venv/bin/python rank_profiles.py --input pb_result.csv

# If the script was interrupted, resume with the saved batch ID:
source ~/phantombuster-api/.env && \
  ~/phantombuster-api/.venv/bin/python rank_profiles.py --batch-id <batch_id>
```

Output: `ranked_profiles.csv` — all original columns + `rank` (1–13) + `seniority_tag` (B or empty)

Requires `ANTHROPIC_API_KEY` in `.env` in addition to `PHANTOMBUSTER_API_KEY`.

---

## API base
**v1** for status / listing / output: `https://api.phantombuster.com/api/v1`
**v2** for launch only: `POST https://api.phantombuster.com/api/v2/agents/launch`

Auth header: `X-Phantombuster-Key: <key>`

Key endpoints:
- `GET /api/v1/user` — list all agents with status (`runningContainers`, `lastEndStatus`)
- `GET /api/v1/agent/{id}/output` — raw output log for one agent
- `GET /api/v1/agent/{id}/containers` — run history
- `POST /api/v2/agents/launch` — launch an agent (v2 only!)

---

## Where the API key lives
`PB_API_KEY` in `~/Desktop/Python/talent_radar/.env`
`PHANTOMBUSTER_API_KEY` in `~/phantombuster-api/.env` (for scripts in this repo)
Both contain the same key — never hard-code it.

---

## Sourcing pipeline (as of June 2026)

```
1. LinkedIn Company Employees Export (PB)
   → auto-runs daily at 04:45, fileMgmt: mix (deduplication)
   → phantom ID: $PB_EMPLOYEES_EXPORT_ID (set in .env)

2. filter_and_prepare_enricher.py
   → keeps only France / Spain / Portugal profiles (~45% of total)
   → saves wave2_enricher_input.csv → upload to Google Sheets manually

3. LinkedIn Profile Scraper (PB) — profile enrichment
   → phantom ID: $PB_PROFILE_ENRICHER_ID (set in .env)
   → pointed at filtered Google Sheet, 200 profiles/day

4. rank_profiles.py — Claude Haiku Batch API
   → classifies each profile: role rank 1–13 + seniority tag B
   → output: ranked_profiles.csv → review & shortlist

5. push_to_airtable.py — push ranked CSV into Airtable
   → auto-creates missing fields, detects country (FR/SP/PT), adds wave label
   → python push_to_airtable.py --input ranked_profiles.csv --wave 2
   → Airtable base/table: $AIRTABLE_BASE_ID / $AIRTABLE_TABLE_ID (set in .env)
   → requires AIRTABLE_PAT in .env

6. Gem (gem.com) — personal email enrichment
   → bulk CSV import of shortlisted LinkedIn profile URLs only
   → NOT a PhantomBuster phantom — run on final candidates, not all profiles
```

## Wave status (as of 2026-07-27)

| Wave | Companies | Profiles | Status |
|---|---|---|---|
| Wave 1 | 28 (sales automation, FR/ES/PT) | 2,187 ranked | Done — in Airtable |
| Wave 2 | 45 (broader B2B SaaS) | 18,700 exported, 9,084 FR/ES/PT | **Paused 2026-07-27** to run Wave 4 — see `wave/notes.md` to resume |
| Wave 3 | 29 (Bordeaux tech + AI) | — | Employees Export started Jun 16 |
| Wave 4 | 6 (Gong, Modjo, Ringover, Livestorm, Pennylane, Agicap) | 24,227 exported (cumulative store), 1,499 FR/ES/PT unique to these 6 | Enricher running 2026-07-27, ~8 days |

Note: the Employees Export phantom's result store is cumulative/shared across
all waves ("mix" file management) — always filter its raw output down to the
current wave's company URLs before feeding the Profile Scraper. See
`wave/notes.md` for details.

## ⚠️ DO NOT RUN
**LinkedIn Auto Connect** — uses connection quota and was crash-looping (3-second runs).
Never trigger this phantom unless explicitly instructed.
