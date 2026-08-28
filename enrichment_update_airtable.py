"""
enrichment_update_airtable.py — Write Twitter/X and GitHub URLs back to Airtable.

Downloads the enrichment S3 CSVs, matches profiles by name, and upserts
twitterUrl / githubUrl into existing Airtable records.

Usage:
    python3 enrichment_update_airtable.py [--dry-run]
"""

import argparse
import csv
import io
import os
import sys
import time
import unicodedata
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
PAT   = os.environ.get("AIRTABLE_PAT", "")
BASE  = os.environ.get("AIRTABLE_BASE_ID", "")
TABLE = os.environ.get("AIRTABLE_TABLE_ID", "")

if not PAT or not BASE or not TABLE:
    sys.exit("AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID must be set in .env")

AT_HEADERS = {"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"}

TWITTER_S3 = "https://phantombuster.s3.amazonaws.com/VLyWCsB92xw/HvQuPPZMKdKCrKiiq8IaXw/enrichment-twitter-urls.csv"
GITHUB_S3  = "https://phantombuster.s3.amazonaws.com/VLyWCsB92xw/wVbi9D6ZX0w3dfqlDVIk7Q/enrichment-github-search.csv"


# ── Helpers ───────────────────────────────────────────────────────────────────
def github_search_terms(query: str) -> str:
    """GitHub User Search's 'query' column is the search URL itself
    (e.g. 'https://github.com/search?q=Ronan+Sangouard+Nabla&type=users') —
    pull the actual 'Name Company' text back out of the q= param."""
    parsed = urlparse(query)
    q = parse_qs(parsed.query).get("q", [""])[0]
    return q or query


def norm(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def download_csv(url: str) -> list[dict]:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def fetch_airtable_records() -> dict:
    """Returns {norm(name): record_id} + {norm(name + company): record_id}"""
    lookup = {}
    params = {"fields[]": ["fullName", "name", "companyName"], "pageSize": 100}
    while True:
        r = requests.get(
            f"https://api.airtable.com/v0/{BASE}/{TABLE}",
            headers=AT_HEADERS, params=params, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        for rec in data.get("records", []):
            rid = rec["id"]
            f   = rec.get("fields", {})
            name = f.get("fullName") or f.get("name") or ""
            company = f.get("companyName") or ""
            if isinstance(company, dict):
                company = company.get("name", "")
            if name:
                lookup[norm(name)] = rid
                if company:
                    lookup[norm(f"{name} {company}")] = rid
        offset = data.get("offset")
        if not offset:
            break
        params["offset"] = offset
        time.sleep(0.2)
    return lookup


def ensure_fields():
    r = requests.get(
        f"https://api.airtable.com/v0/meta/bases/{BASE}/tables",
        headers=AT_HEADERS,
    )
    r.raise_for_status()
    existing = set()
    for t in r.json()["tables"]:
        if t["id"] == TABLE:
            existing = {f["name"] for f in t["fields"]}
            break
    for fname in ("twitterUrl", "githubUrl"):
        if fname not in existing:
            print(f"  Creating Airtable field: {fname}")
            requests.post(
                f"https://api.airtable.com/v0/meta/bases/{BASE}/tables/{TABLE}/fields",
                headers=AT_HEADERS, json={"name": fname, "type": "url"},
            )
            time.sleep(0.3)


def update_record(record_id: str, fields: dict) -> bool:
    r = requests.patch(
        f"https://api.airtable.com/v0/{BASE}/{TABLE}/{record_id}",
        headers=AT_HEADERS,
        json={"fields": fields},
    )
    return r.status_code == 200


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show matches without updating Airtable")
    args = parser.parse_args()

    print("Downloading enrichment CSVs from S3…")
    twitter_rows = download_csv(TWITTER_S3)
    github_rows  = download_csv(GITHUB_S3)
    print(f"  Twitter: {len(twitter_rows)} rows | GitHub: {len(github_rows)} rows")

    print("Fetching Airtable records…")
    lookup = fetch_airtable_records()
    print(f"  {len(lookup)} lookup keys built")

    if not args.dry_run:
        ensure_fields()

    tw_matched = tw_updated = tw_skipped = 0
    gh_matched = gh_updated = gh_skipped = 0

    # ── Twitter ───────────────────────────────────────────────────────────────
    print("\nProcessing Twitter/X…")
    for row in twitter_rows:
        url   = (row.get("twitterUrl") or "").strip()
        query = (row.get("query") or "").strip()
        if not url:
            continue  # no result found for this person

        key = norm(query)
        rid = lookup.get(key)
        if not rid:
            tw_skipped += 1
            continue

        tw_matched += 1
        if args.dry_run:
            print(f"  [dry-run] {query} → {url}")
            continue

        if update_record(rid, {"twitterUrl": url}):
            tw_updated += 1
        time.sleep(0.1)

    # ── GitHub ────────────────────────────────────────────────────────────────
    print("Processing GitHub…")
    for row in github_rows:
        url   = (row.get("profileUrl") or "").strip()
        query = (row.get("query") or "").strip()
        if not url:
            continue

        # query is the search URL itself — pull "Name Company" out of the q= param
        terms = github_search_terms(query)

        # Try exact match first ("Name Company"), then name-only (first 2-3 words)
        rid = lookup.get(norm(terms))
        if not rid:
            words = terms.split()
            for n in (3, 2):
                if len(words) >= n:
                    rid = lookup.get(norm(" ".join(words[:n])))
                    if rid:
                        break

        if not rid:
            gh_skipped += 1
            continue

        gh_matched += 1
        if args.dry_run:
            print(f"  [dry-run] {query} → {url}")
            continue

        if update_record(rid, {"githubUrl": url}):
            gh_updated += 1
        time.sleep(0.1)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'[dry-run] ' if args.dry_run else ''}Done.")
    print(f"  Twitter: {tw_matched} matched → {tw_updated} updated | {tw_skipped} no match")
    print(f"  GitHub:  {gh_matched} matched → {gh_updated} updated | {gh_skipped} no match")


if __name__ == "__main__":
    main()
