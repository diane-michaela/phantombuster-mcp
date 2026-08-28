# PhantomBuster API (new: MCP) + LinkedIn Profile Ranker

A Python toolkit that connects PhantomBuster to Claude (Anthropic) to control LinkedIn automation agents in plain English and classify scraped profiles by role and seniority — all from your terminal.

UPDATE : Please note that PB MCP was released after this project was created. You now have the option to connect with the MCP.

---

## Sourcing pipeline

```
1. LinkedIn Company Employees Export (PB, daily 04:45)
   → phantomBuster

2. filter_and_prepare_enricher.py
   → keeps FR / ES / PT profiles only (~45% of total)

3. LinkedIn Profile Scraper (PB, 200/day)
   → phantomBuster
   → enriches filtered profiles (title, skills, headline…)

4. rank_profiles.py  ← Claude Haiku Batch API, role rank 1–13
   → output: ranked_profiles.csv

5. push_to_airtable.py
   → pushes ranked CSV into Airtable, adds country + role label
   → usage: python push_to_airtable.py 

6. Github/ X user search export — (Github only for Tech shortlisted profiles)
    → non-LinkedIn enrichment (Twitter/X · GitHub) — running in parallel

TBC:
      Gem (gem.com) — shortlisted candidates only
         → bulk CSV import of LinkedIn URLs → personal emails
```

**Wave status (Aug 2026):**

| Wave | Companies | Profiles | Status |
|---|---|---|---|
| Wave 1 (`target_companies_wave1.csv`) | 23/28 exported (5 missing — URL slug mismatch) | 2,187 ranked | ✅ Done — in Airtable |
| Wave 2 (`target_companies_wave2.csv`) | 45/45 ✅ | 7,452 of 9,084 targeted scraped, ranked + in Airtable | ⏸ Paused 2026-07-27 mid-queue (~1,631 never scraped) to prioritize Wave 4 — resume via `wave/wave2_pause_state.json` |
| Wave 3 (`target_companies_wave3.csv`) | 28/28 ✅ (done Jun 19) | — | Filter + enrich queued — waiting on Wave 2/4 |
| Wave 4 (`target_companies_wave4.csv`) | 6 GTM-focused (Gong, Modjo, Ringover, Livestorm, Pennylane, Agicap) | 1,286 newly ranked + pushed 2026-08-27 (54 pushed earlier) | ▶ Actively scraping (~80/day, shared LinkedIn account cap) |

⚠️ `pb_daily_dashboard.py`'s "Wave 2" progress line reads the Profile Scraper phantom's persistent S3 store, which keeps accumulating whatever wave that phantom is *currently* pointed at — since Jul 27 that's Wave 4. Don't trust that line's label; check which spreadsheet the phantom is actually pointed at first.

**Non-LinkedIn enrichment (Twitter/X · GitHub):**

Targets ranks 1–7 only (technical profiles: AI/ML, Eng, Product, Design) — ~1,146 profiles. Sales, CS, HR, Finance excluded (no GitHub/Twitter signal). Link-only enrichment (no profile scraping) — just finds the handle/username and writes it onto the matching Airtable row.

| Phantom | Status |
|---|---|
| Twitter/X URL Finder | ✅ 1,157 handles found |
| Twitter/X Profile Scraper | Only 120/1,157 profiles scraped — not needed for link-only enrichment |
| GitHub User Search | 413/1,146 usernames found (36%), still running daily |
| GitHub Profile Scraper | Never launched — not needed for link-only enrichment |
| `enrichment_update_airtable.py` | Writes `twitterUrl`/`githubUrl` onto existing Airtable rows by name match. As of 2026-08-27: 673 Twitter + 12 GitHub written (218 Twitter / 6 GitHub handles found had no matching Airtable row). GitHub's `query` column is a search URL, not a plain name — the matcher parses the `q=` param back out before matching. |

---

## What it does

### 1. PhantomBuster API wrapper (`phantombuster_api.py`)
Eight ready-to-use functions to control any PhantomBuster agent without touching the UI:

| Function | What it does |
|---|---|
| `list_phantoms()` | List all agents in your account |
| `get_phantom(agent_id)` | Fetch metadata for one agent |
| `launch_phantom(agent_id, args=None)` | Launch an agent (optionally override its argument) |
| `stop_phantom(agent_id)` | Abort a running agent |
| `fetch_output(agent_id)` | Get the latest result object from an agent |
| `save_phantom_argument(agent_id, args)` | Persist a new default argument for an agent |
| `get_phantom_status(agent_id)` | Return current launch status + last end message |
| `delete_phantom_output(agent_id)` | Delete all stored output for an agent |

### 2. Slack Daily Dashboard (`pb_daily_dashboard.py`)
Posts a pipeline status report every weekday morning to a Slack channel via incoming webhook.

**What it shows:**
- Which phantoms are currently running
- Company Employees Export progress per wave
- LinkedIn Profile Scraper (enricher) progress + ETA
- Twitter/X and GitHub enrichment phantom progress + ETA
- Up Next action items with dates
- Vigilance alerts (stale LinkedIn cookie, missed runs, errors)

**Setup:**
1. Create a Slack incoming webhook for your channel ([api.slack.com/apps](https://api.slack.com/apps) → Incoming Webhooks → Add to Workspace)
2. Add `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...` to your `.env`
3. Add a crontab entry to run it automatically:

```bash
# 9:03 AM Paris (7:03 AM UTC), Mon–Fri
3 7 * * 1-5 cd /path/to/phantombuster-api && python3 pb_daily_dashboard.py >> /tmp/pb_dashboard.log 2>&1
```

Or run manually at any time:
```bash
source .env && python3 pb_daily_dashboard.py
```

---

### 3. LinkedIn Profile Ranker (`rank_profiles.py`)
After a PhantomBuster LinkedIn scrape completes, this script:
- Fetches the result CSV automatically from PhantomBuster
- Sends every profile to **Claude Haiku via the Batch API** (async, 50% cheaper than standard)
- Classifies each person into a **role rank (1–13)** and a **seniority tag (B = founder/leader)**
- Outputs `ranked_profiles.csv` with all original columns + `rank` + `seniority_tag`

---

## Role rank reference

| Rank | Role |
|---|---|
| 1 | AI / ML / Data Science / LLM / NLP |
| 2 | Frontend / Mobile / Fullstack |
| 3 | Backend |
| 4 | DevOps / SRE / Infrastructure / Cloud |
| 5 | Other Engineering (QA, Security, Embedded…) |
| 6 | Product |
| 7 | Design |
| 8 | Marketing / Growth |
| 9 | Revenue / BizDev / Partnerships |
| 10 | Sales / AE / BDR / SDR |
| 11 | Customer Success / Enablement |
| 12 | Other / HR / Finance / Unknown |
| 13 | Investor / VC / Advisor |

**Seniority tag `B`** = leadership / decision-maker scope. Assigned by Claude Haiku based on job title.

Includes: Founder, Co-founder, CEO, CTO, COO, CFO, CPO, CMO, CRO, President, Chairman, VP, Vice-President, Director, Head of, Managing Director, General Partner, Managing Partner, Engineering Manager, Tech Lead, Lead Engineer, Chief of Staff, Operating Partner, QA Manager.

Empty = individual contributor (Senior X, Staff X, Principal X without management scope, intern, associate, etc.).

---

## Setup

### Prerequisites
- Python 3.12
- [uv](https://github.com/astral-sh/uv) (fast package manager)
- A [PhantomBuster](https://phantombuster.com) account + API key
- An [Anthropic](https://console.anthropic.com) API key (for the ranker only)

### Install

```bash
git clone https://github.com/diane-michaela/phantombuster-api.git
cd phantombuster-api

# Create venv + install dependencies
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

### Configure

Create a `.env` file in the project root (never committed — already in `.gitignore`):

```
PHANTOMBUSTER_API_KEY=your_phantombuster_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   # optional, for pb_daily_dashboard.py
```

---

## Usage

### Control PhantomBuster agents

```python
source .env && python -c "
import phantombuster_api as pb
import json

# List all your agents
agents = pb.list_phantoms()
for a in agents:
    print(a['id'], a['name'])
"
```

Or use it interactively with Claude Code — just open this folder in Claude Code and ask in plain English:

> "List all my phantoms and tell me which ones ran recently"
> "Launch the agent with ID 12345 with this new search URL"
> "What's the status of my targeted companies scraper?"

### Rank scraped profiles

```bash
# Fetch PhantomBuster output and classify all profiles:
source .env && python rank_profiles.py --phantom-id YOUR_AGENT_ID

# Classify a local CSV you already have:
source .env && python rank_profiles.py --input result.csv

# Resume an interrupted batch:
source .env && python rank_profiles.py --batch-id YOUR_BATCH_ID
```

Output: `ranked_profiles.csv` — all original columns plus:
- `rank` — integer 1–13
- `seniority_tag` — `B` for founders/leaders, empty for ICs

---

## Auto Connect filter logic

Run once all waves are ranked (~Oct 2026). Produces a CSV ready to feed into the LinkedIn Auto Connect phantom.

```bash
python build_autoconnect_segment.py --input ranked_profiles.csv --wave 2
```

**Filters applied:**
- Country: **France only**
- Connection degree: **2nd only** (1st = already connected, 3rd = can't reach directly)
- Excludes profiles where `autoconnect_sent = true` in Airtable — no one is contacted twice
- Ranks:

| Rank | Role | Included |
|---|---|---|
| 1 | AI / ML / Data / LLM | ✅ all |
| 2 | Frontend / Mobile / Fullstack | ✅ all |
| 3 | Backend | ✅ all |
| 4 | DevOps / SRE / Cloud | ✅ all |
| 5 | Other Engineering | ✅ all |
| 6 | Product | ✅ all |
| 7 | Design | ✅ all |
| 8 | Marketing / Growth | ✅ all |
| 9 | Revenue / BizDev / GTM | ✅ only if title contains "GTM" or "Growth Engineer" |
| 10 | Sales / AE / BDR / SDR | ❌ |
| 11 | Customer Success / Enablement | ✅ all |
| 12 | Other / HR / Finance / Unknown | ❌ |
| 13 | Investor / VC / Advisor | ❌ |

Output sorted: seniority B first, then rank ascending. LinkedIn limit: ~20 connections/day.

**After running the phantom:** bulk-check `autoconnect_sent` in Airtable for the segment profiles. Next run excludes them automatically.

**Timing:** do not run while Profile Scraper (enricher) is active — both visit individual profiles and LinkedIn flags the overlap. Wait until the current wave's enricher finishes before starting Auto Connect.

**Why Auto Connect compounds results:** accepted connections become 1st degree → their colleagues at the same company become 2nd degree → next Employees Export run on those companies surfaces profiles previously hidden at 3rd degree.

---

## Project structure

```
phantombuster-api/
├── phantombuster_api.py              # PhantomBuster API wrapper (8 functions)
├── pb_daily_dashboard.py             # Slack daily pipeline status dashboard (Mon–Fri 9 AM Paris)
├── rank_profiles.py                  # LinkedIn profile classifier (Claude Batch API)
├── filter_and_prepare_enricher.py    # Filter FR/ES/PT → prep enricher input
├── push_to_airtable.py               # Push ranked CSV → Airtable (country, connectionDegree, autoconnect_sent)
├── enrichment_update_airtable.py     # Write Twitter/GitHub URLs onto existing Airtable rows by name match
├── build_autoconnect_segment.py      # Build Auto Connect CSV (FR, 2nd degree, target ranks, dedup via Airtable)
├── target_companies_wave1.csv        # Wave 1 companies (28, done)
├── target_companies_wave2.csv        # Wave 2 companies (45, enricher running)
├── target_companies_wave3.csv        # Wave 3 companies (29, export started Jun 16)
├── target_companies_wave4.csv        # Wave 4 companies (6 GTM-focused, export launched Jun 24)
├── requirements.txt                  # httpx, anthropic
├── CLAUDE.md                         # Instructions for Claude Code
├── .env                              # API keys — NOT committed
└── .gitignore                        # Excludes .env, .venv, __pycache__
```

---

## Security

- API keys live in `.env` which is excluded from git via `.gitignore`
- Keys are never hard-coded or logged
- The ranker script reads keys from environment variables only

---

## Requirements

- `httpx` — HTTP client for PhantomBuster API calls
- `anthropic` — Claude SDK for profile classification

---

## API notes

Most PhantomBuster endpoints are on **v1**: `https://api.phantombuster.com/api/v1`
The launch endpoint is the exception: `POST https://api.phantombuster.com/api/v2/agents/launch`

Auth header on all calls: `X-Phantombuster-Key: <key>`


