"""
rank_profiles.py — Classify PhantomBuster LinkedIn profiles by role rank and seniority.

Usage:
    # Fetch output from PhantomBuster then classify:
    python rank_profiles.py --phantom-id $PB_WAVE1_ENRICHER_ID

    # Classify an existing local CSV:
    python rank_profiles.py --input result.csv

    # Append new profiles to an existing ranked_profiles.csv (skip already-ranked):
    python rank_profiles.py --phantom-id $PB_WAVE1_ENRICHER_ID --append
    python rank_profiles.py --input new_batch.csv --append

    # Resume a batch already submitted (if script was interrupted):
    python rank_profiles.py --batch-id <batch_id>

Output:
    ranked_profiles.csv — all original columns + rank (1-13) + seniority_tag (B or "")

Prerequisites:
    ANTHROPIC_API_KEY  — set in .env
    PHANTOMBUSTER_API_KEY — already in .env (only needed with --phantom-id)
"""

import argparse
import csv
import httpx
import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

load_env()

ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
PHANTOM_KEY    = os.environ.get("PHANTOMBUSTER_API_KEY", "")
PHANTOM_BASE   = "https://api.phantombuster.com/api/v2"
OUTPUT_PATH    = Path(__file__).parent / "ranked_profiles.csv"
BATCH_ID_FILE  = Path(__file__).parent / ".last_batch_id"


# ---------------------------------------------------------------------------
# Classification prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a lead classification assistant. Given a LinkedIn job title and headline, classify the profile into TWO fields and respond ONLY with a JSON object — no explanation, no markdown.

Classify strictly on the job title/headline text provided. Never infer or use health, religion, ethnicity, sexual orientation, political affiliation, age, or other protected/sensitive personal attributes, even if a name or headline seems to suggest one.

---

FIELD 1 — RANK (number 1 to 13)

Use the title/headline to assign a rank based on the primary role:

1 = ML / AI / Data Science / LLM / NLP / AI Engineer / MLOps / ML Researcher / AI Researcher / Data Scientist / LLM R&D / AI Architect / AI Research Engineer
2 = Frontend / Front-end / UI Developer / Mobile / iOS / Android / Fullstack / Full-stack (fullstack counts as frontend)
3 = Backend / Back-end / API / Server-side / Node / Python / Java / Go / Ruby — not fullstack
4 = DevOps / SRE / Infrastructure / Platform / Cloud / Kubernetes / GitOps / Site Reliability / CI-CD / Ingénieur cloud / Platform Ops
5 = QA / Test / Security Engineer / Embedded / Firmware / Support Engineer / Solution Architect / Solutions Architect / Forward Deployed Engineer / Member of Engineering (when no stack specified) / Other engineer not in 1-4
6 = Product Manager / PM / Product Owner / Product Operations / Product Engineer / Staff Product Manager / Product designer (with "product" in title) / Product marketer / Product marketing
7 = Designer / UX / UI Designer / Brand Designer / Creative Lead / Graphic — without "product" in title
8 = Marketing / Content / Brand / SEO / Demand Gen / Growth Marketing / Field Marketing / Communications / CMO / External Communications
9 = Revenue / RevOps / Sales Ops / Revenue Operations / CRO (if not C-suite founder) / GTM / GTM Engineer / GTM Lead / Partnerships / Business Development / Commercial / Expansion / Sales Enablement / Key Account Manager / Partner Sales
10 = Sales / Account Executive / AE / BDR / SDR / Sales Engineer / Enterprise AE / Inside Sales
11 = Customer Success / CSM / Customer Care / Customer Support / Implementation / Onboarding / CX / Chief Customer Officer / Customer Chief Officer
12 = Office Manager / EA / PA / HR / People Operations / Recruiter / Talent / Finance / Legal / CFO (non-founder) / COO (non-founder) / Operations Manager / Project Manager / Innovation Coordinator / Formateur / Consultant / Trainer / Academic / PhD Student / Researcher (non-AI) / Postdoc / Professor / Doctor / Deputy Director / Board Member / Auteur / Assessment Specialist / Unknown / unclear
13 = Investor / VC / Venture Capital / Partner (VC firm) / General Partner / Limited Partner / Angel Investor / Seed Investor / Operating Partner / Advisor / Board Member (investor context)

RANKING RULES:
- If the title contains multiple roles (e.g. "CTO & Co-Founder"), rank by the TECHNICAL role (CTO → use their likely stack, default rank 5 if unclear)
- "Founding Engineer" → rank by stack if specified, else rank 5
- "Founding Account Executive" → rank 10
- "Founding Product Designer" → rank 6
- "Chief of Staff" → rank 12
- "Head of Research" → rank 1 if AI/ML context, else rank 12
- "Data Analyst" / "Data Engineer" / "BI Analyst" → rank 5
- "Solution Architect" / "Forward Deployed Engineer" → rank 5
- "GTM Engineer" / "GTM Lead" → rank 9
- "Member of Engineering" (no stack) → rank 5
- If title is completely unclear, investor-sounding, or missing → rank 13 if investor, rank 12 if unknown

---

FIELD 2 — SENIORITY TAG (B or empty string)

Output "B" if the title contains ANY of these keywords (case insensitive):
Founder, Co-founder, Cofounder, Founding (partner/member/team), CEO, CTO, COO, CFO, CPO, CBO, CRO, CRIO, CCO, CMO, President, Chairman, Director, Head of, VP, Vice-President, Vice President, Managing Director, General Partner, Managing Partner, Partner (in VC/investment context), C-suite, Principal (senior), Lead (only if "Tech Lead" or "Lead Engineer" or "Lead Backend" etc), Manager (EXCEPT Product Manager and Project Manager)

Output "" (empty) for all individual contributors, interns, associates, juniors, seniors without management scope.

SPECIAL CASES:
- "Founding Engineer" / "Founding Team" / "Founding Member" → B
- "Chief of Staff" → B
- "Operating Partner" → B
- "Engineering Manager" → B
- "QA Manager" → B
- "Senior X" alone → empty
- "Product Manager" → empty
- "Project Manager" → empty
- "Account Manager" / "Key Account Manager" → empty

---

Respond with ONLY this JSON, nothing else:
{"rank": <integer 1-13>, "seniority": "<B or empty string>"}"""


def make_user_message(row: dict) -> str:
    # Support both PhantomBuster column naming conventions
    title = (
        row.get("linkedinJobTitle") or
        row.get("job") or
        row.get("currentJobTitle") or ""
    ).strip()
    headline = (
        row.get("linkedinHeadline") or
        row.get("headline") or ""
    ).strip()
    if not title and not headline:
        return "Job Title: (unknown)\nHeadline: (unknown)"
    parts = []
    if title:
        parts.append(f"Job Title: {title}")
    if headline and headline != title:
        parts.append(f"Headline: {headline}")
    return "\n".join(parts)


def profile_url(row: dict) -> str:
    """Return a normalized, stable dedup key for a profile row.

    Different PhantomBuster runs populate different URL columns in different
    formats (with/without www., URL-encoded accents, trailing slash), so raw
    string comparison silently fails to dedup the same person across runs.
    """
    import urllib.parse

    raw = (
        row.get("profileUrl_from_input") or
        row.get("profileUrl") or
        row.get("linkedinProfileUrl") or ""
    ).strip()
    if not raw:
        return ""
    url = urllib.parse.unquote(raw).lower().strip()
    url = re.sub(r"^https?://(www\.)?linkedin\.com/in/", "", url)
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# PhantomBuster helpers
# ---------------------------------------------------------------------------

def fetch_phantom_output(phantom_id: str) -> list[dict]:
    """Download the latest result CSV from PhantomBuster and return rows."""
    if not PHANTOM_KEY:
        sys.exit("PHANTOMBUSTER_API_KEY not set in .env")

    headers = {"X-Phantombuster-Key": PHANTOM_KEY}
    r = httpx.get(
        f"{PHANTOM_BASE}/agents/fetch-output",
        headers=headers,
        params={"id": phantom_id},
    )
    r.raise_for_status()
    data = r.json()

    output_text = data.get("output", "")
    csv_url = None
    for line in output_text.splitlines():
        if "result.csv" in line and "s3.amazonaws.com" in line:
            for token in line.split():
                if token.startswith("https://") and token.endswith(".csv"):
                    csv_url = token
                    break
        if csv_url:
            break

    # Fall back: construct direct S3 URL from phantom metadata
    if not csv_url:
        agent_r = httpx.get(
            f"{PHANTOM_BASE}/agents/fetch",
            headers=headers,
            params={"id": phantom_id},
        )
        agent_r.raise_for_status()
        agent = agent_r.json()
        org_folder = agent.get("orgS3Folder")
        s3_folder = agent.get("s3Folder")
        if org_folder and s3_folder:
            csv_url = f"https://phantombuster.s3.amazonaws.com/{org_folder}/{s3_folder}/result.csv"
            print(f"Using direct S3 URL: {csv_url}")
        else:
            sys.exit("Could not find result.csv URL in PhantomBuster output. Is the phantom finished?")

    print(f"Downloading CSV from PhantomBuster…")
    resp = httpx.get(csv_url, follow_redirects=True)
    resp.raise_for_status()

    local_path = Path(__file__).parent / "pb_result.csv"
    local_path.write_bytes(resp.content)
    print(f"Saved {len(resp.content):,} bytes → {local_path}")
    return load_csv(local_path)


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

def build_requests(profiles: list[dict]) -> list[Request]:
    requests = []
    for i, row in enumerate(profiles):
        requests.append(Request(
            custom_id=str(i),
            params=MessageCreateParamsNonStreaming(
                model="claude-haiku-4-5",
                max_tokens=50,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": make_user_message(row)}],
            ),
        ))
    return requests


def submit_batch(client: anthropic.Anthropic, requests: list[Request]) -> str:
    print(f"Submitting batch of {len(requests):,} requests…")
    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id
    BATCH_ID_FILE.write_text(batch_id)
    print(f"Batch submitted: {batch_id}")
    print(f"(ID saved to {BATCH_ID_FILE} in case you need to resume)")
    return batch_id


def wait_for_batch(client: anthropic.Anthropic, batch_id: str) -> None:
    print("Waiting for batch to complete (checking every 60 s)…")
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"  Status: {batch.processing_status} | "
            f"processing={counts.processing} succeeded={counts.succeeded} "
            f"errored={counts.errored}"
        )
        if batch.processing_status == "ended":
            break
        time.sleep(60)
    print("Batch complete.")


def parse_classification(raw: str) -> dict:
    """Extract rank/seniority from raw text, tolerating markdown fences."""
    import re
    text = raw.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    # Extract first {...} block if surrounded by prose
    m = re.search(r"\{[^}]+\}", text)
    if m:
        text = m.group(0)
    try:
        parsed = json.loads(text)
        rank = int(parsed.get("rank", 12))
        seniority = str(parsed.get("seniority", "")).strip()
        if rank < 1 or rank > 13:
            rank = 12
        if seniority not in ("B", ""):
            seniority = ""
        return {"rank": rank, "seniority": seniority}
    except (json.JSONDecodeError, ValueError):
        return {"rank": 12, "seniority": ""}


def collect_results(client: anthropic.Anthropic, batch_id: str) -> dict[str, dict]:
    """Return mapping custom_id → {"rank": int, "seniority": str}."""
    results = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            msg = result.result.message
            raw = next((b.text for b in msg.content if b.type == "text"), "")
            results[result.custom_id] = parse_classification(raw)
        else:
            results[result.custom_id] = {"rank": 12, "seniority": ""}
    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def load_existing_ranked() -> tuple[list[dict], set[str]]:
    """Load existing ranked_profiles.csv; return (rows, set of already-ranked profile URLs)."""
    if not OUTPUT_PATH.exists():
        return [], set()
    with open(OUTPUT_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    seen = {profile_url(r) for r in rows if profile_url(r)}
    print(f"Existing ranked_profiles.csv: {len(rows):,} profiles already ranked.")
    return rows, seen


def write_output(profiles: list[dict], results: dict[str, dict], append: bool = False) -> None:
    existing_rows: list[dict] = []
    if append:
        existing_rows, _ = load_existing_ranked()

    original_fields = list(profiles[0].keys()) if profiles else []
    extra = [f for f in ["rank", "seniority_tag"] if f not in original_fields]

    # Merge existing fieldnames if appending
    if existing_rows:
        all_fields = list(existing_rows[0].keys())
        for f in original_fields + extra:
            if f not in all_fields:
                all_fields.append(f)
        fieldnames = all_fields
    else:
        fieldnames = original_fields + extra

    new_rows = []
    for i, row in enumerate(profiles):
        classification = results.get(str(i), {"rank": 12, "seniority": ""})
        new_rows.append({**row, "rank": classification["rank"], "seniority_tag": classification["seniority"]})

    all_rows = existing_rows + new_rows

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nOutput written to: {OUTPUT_PATH}")

    # Quick summary
    from collections import Counter
    rank_counts = Counter(results[k]["rank"] for k in results)
    seniority_b = sum(1 for k in results if results[k]["seniority"] == "B")
    print(f"New profiles classified: {len(profiles):,}")
    print(f"Total in ranked_profiles.csv: {len(all_rows):,}")
    print(f"Profiles with seniority B (founders/leaders): {seniority_b:,}")
    print("Rank breakdown:")
    rank_labels = {
        1: "AI/ML/Data Science", 2: "Frontend/Mobile", 3: "Backend",
        4: "DevOps/Infra", 5: "Other Engineering", 6: "Product",
        7: "Design", 8: "Marketing", 9: "Revenue/BizDev",
        10: "Sales", 11: "Customer Success", 12: "Other/Unknown", 13: "Investor",
    }
    for rank in sorted(rank_counts):
        label = rank_labels.get(rank, f"Rank {rank}")
        print(f"  [{rank:2d}] {label}: {rank_counts[rank]:,}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Rank LinkedIn profiles from PhantomBuster output")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phantom-id", help="PhantomBuster agent ID to fetch output from")
    group.add_argument("--input", help="Path to a local CSV file")
    group.add_argument("--batch-id", help="Resume an already-submitted batch by ID")
    parser.add_argument("--append", action="store_true",
                        help="Append new profiles to existing ranked_profiles.csv (skip already-ranked)")
    args = parser.parse_args()

    if not ANTHROPIC_KEY:
        sys.exit("ANTHROPIC_API_KEY not set. Add it to .env: ANTHROPIC_API_KEY=sk-ant-…")

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # --- Resume path ---
    if args.batch_id:
        batch_id = args.batch_id
        # We need the original profiles to write the output — look for pb_result.csv or last csv
        csv_candidates = [
            Path(__file__).parent / "pb_result.csv",
            Path(__file__).parent / "result.csv",
        ]
        profiles = None
        for p in csv_candidates:
            if p.exists():
                profiles = load_csv(p)
                print(f"Loaded {len(profiles):,} profiles from {p}")
                break
        if profiles is None:
            sys.exit("Could not find local CSV. Place pb_result.csv in the project folder.")
        wait_for_batch(client, batch_id)
        results = collect_results(client, batch_id)
        write_output(profiles, results)
        return

    # --- Fetch or load profiles ---
    if args.phantom_id:
        profiles = fetch_phantom_output(args.phantom_id)
    else:
        profiles = load_csv(Path(args.input))

    print(f"Loaded {len(profiles):,} profiles.")

    # --- Dedup if appending ---
    if args.append:
        _, already_ranked = load_existing_ranked()
        before = len(profiles)
        profiles = [r for r in profiles if profile_url(r) not in already_ranked]
        skipped = before - len(profiles)
        if skipped:
            print(f"Skipping {skipped:,} already-ranked profiles → {len(profiles):,} new to classify.")
        if not profiles:
            print("Nothing new to classify. ranked_profiles.csv is up to date.")
            return

    # --- Estimate cost ---
    n = len(profiles)
    est_input_tokens  = n * 120   # ~120 tokens per request (system cached after first)
    est_output_tokens = n * 15
    # Haiku 4.5 batch: $0.50/1M input, $2.50/1M output (50% batch discount)
    est_cost = (est_input_tokens / 1_000_000 * 0.50) + (est_output_tokens / 1_000_000 * 2.50)
    print(f"Estimated cost: ${est_cost:.3f} (Haiku 4.5 batch pricing)")

    # --- Build and submit batch ---
    requests = build_requests(profiles)
    batch_id = submit_batch(client, requests)

    # --- Wait and collect ---
    wait_for_batch(client, batch_id)
    results = collect_results(client, batch_id)

    # --- Write output ---
    write_output(profiles, results, append=args.append)


if __name__ == "__main__":
    main()
