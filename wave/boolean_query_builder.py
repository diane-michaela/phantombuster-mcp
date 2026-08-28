"""
boolean_query_builder.py — Deterministic, platform-limit-validated boolean search
query generator for Google X-ray, GitHub user search, and LinkedIn people search.

Ported from carlopezzuto/boolean-query-generator (query-generator.js, MIT license,
https://github.com/carlopezzuto/boolean-query-generator) — see wiki/insights/
carlo-pezzuto-sourcing-tools.md in the Obsidian vault for the review that led to this.

Unlike an LLM-generated boolean string, every query this produces is validated against
the target platform's real limits (Google 32 words, GitHub 256 chars / 5 operators,
LinkedIn 6 operators unless hack mode) and automatically split across multiple queries
when the input list would exceed them.

Usage:
    python boolean_query_builder.py --platform linkedin --skills "Python,Go,Kubernetes" \
        --titles "Backend Engineer,Platform Engineer" --hack-mode

    python boolean_query_builder.py --platform google --skills "Python,Rust" \
        --titles "Engineer" --locations "France,Spain,Portugal" --companies "Stripe,Datadog"

    python boolean_query_builder.py --platform github --skills "Python,Go" \
        --locations "France,Germany" --min-followers 50

Can also be imported and called directly:
    from boolean_query_builder import generate_queries
    result = generate_queries("linkedin", {"skills": ["Python", "Go"], "titles": ["Backend Engineer"]})
"""

import argparse
import json
import re
from urllib.parse import quote, urlencode

# ---------------------------------------------------------------------------
# Platform configuration
# ---------------------------------------------------------------------------

PLATFORMS = {
    "google": {
        "display_name": "Google (X-ray)",
        "max_length": 32,
        "length_unit": "words",
        "max_operators": None,
        "base_url": "https://www.google.com/search?q=",
    },
    "github": {
        "display_name": "GitHub User Search",
        "max_length": 256,
        "length_unit": "characters",
        "max_operators": 5,
        "base_url": "https://github.com/search?type=users&q=",
    },
    "linkedin": {
        "display_name": "LinkedIn Search",
        "max_length": 1000,
        "length_unit": "characters",
        "max_operators": 6,          # vanilla mode
        "max_operators_hack": 999,   # hack mode (char limit only)
        "base_url": "https://www.linkedin.com/search/results/people/",
    },
}

_OPERATOR_RE = re.compile(r"\b(?:AND|OR|NOT)\b")
_QUOTED_RE = re.compile(r'"[^"]+"')
_SITE_OP_RE = re.compile(r"site:\S+")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def count_words(query: str) -> int:
    processed = _QUOTED_RE.sub("QUOTED", query)
    processed = _SITE_OP_RE.sub("SITEOP", processed)
    return len([w for w in processed.split() if w])


def needs_quotes(term: str) -> bool:
    return " " in term


def format_term(term: str) -> str:
    return f'"{term}"' if needs_quotes(term) else term


def format_intitle(term: str) -> str:
    return f'intitle:"{term}"' if needs_quotes(term) else f"intitle:{term}"


def batch_items(items: list, batch_size: int) -> list:
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def parse_list(value) -> list:
    if isinstance(value, list):
        return [v.strip() for v in value if v and str(v).strip()]
    if not isinstance(value, str):
        return []
    return [s.strip() for s in re.split(r"[,\n]", value) if s.strip()]


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_google(query: str) -> dict:
    word_count = count_words(query)
    if word_count > 32:
        return {"valid": False, "message": f"Exceeds 32 words ({word_count})"}
    return {"valid": True, "message": "OK"}


def validate_github(query: str) -> dict:
    if len(query) > 256:
        return {"valid": False, "message": f"Exceeds 256 characters ({len(query)})"}
    if "(" in query or ")" in query:
        return {"valid": False, "message": "Parentheses not supported"}
    operator_count = len(_OPERATOR_RE.findall(query))
    if operator_count > 5:
        return {"valid": False, "message": f"Exceeds 5 operators ({operator_count})"}
    return {"valid": True, "message": "OK"}


def validate_linkedin(query: str, hack_mode: bool = False) -> dict:
    if len(query) > 1000:
        return {"valid": False, "message": f"Exceeds 1000 characters ({len(query)})"}
    operator_count = len(_OPERATOR_RE.findall(query))
    max_ops = PLATFORMS["linkedin"]["max_operators_hack"] if hack_mode else PLATFORMS["linkedin"]["max_operators"]
    if operator_count > max_ops:
        return {"valid": False, "message": f"Exceeds {max_ops} operators ({operator_count})"}
    return {"valid": True, "message": "OK"}


def validate(platform: str, query: str, hack_mode: bool = False) -> dict:
    if platform == "google":
        return validate_google(query)
    if platform == "github":
        return validate_github(query)
    if platform == "linkedin":
        return validate_linkedin(query, hack_mode)
    return {"valid": False, "message": "Unknown platform"}


# ---------------------------------------------------------------------------
# Query record helpers
# ---------------------------------------------------------------------------

def _make_query(platform: str, label: str, purpose: str, query: str) -> dict:
    config = PLATFORMS[platform]
    validation = validate(platform, query)
    length = count_words(query) if config["length_unit"] == "words" else len(query)
    return {
        "platform": platform,
        "label": label,
        "purpose": purpose,
        "query": query,
        "length": length,
        "length_unit": config["length_unit"],
        "max_length": config["max_length"],
        "valid": validation["valid"],
        "validation_message": validation["message"],
        "url": config["base_url"] + quote(query),
    }


def _make_linkedin_query(label: str, purpose: str, query: str, hack_mode: bool, facets: dict) -> dict:
    config = PLATFORMS["linkedin"]
    validation = validate_linkedin(query, hack_mode)
    return {
        "platform": "linkedin",
        "label": label,
        "purpose": purpose,
        "query": query,
        "length": len(query),
        "length_unit": config["length_unit"],
        "max_length": config["max_length"],
        "valid": validation["valid"],
        "validation_message": validation["message"],
        "url": _build_linkedin_url(query, facets),
        "hack_mode": hack_mode,
        "facets": facets,
    }


def _build_linkedin_url(keywords: str, facets: dict) -> str:
    params = {}
    if keywords and keywords.strip():
        params["keywords"] = keywords
    for facet_key in ("geoUrn", "currentCompany", "pastCompany", "industry", "schoolFilter"):
        values = facets.get(facet_key) if facets else None
        if values:
            params[facet_key] = json.dumps(values)
    params["origin"] = "FACETED_SEARCH"
    return PLATFORMS["linkedin"]["base_url"] + "?" + urlencode(params)


# ---------------------------------------------------------------------------
# Google X-ray generator
# ---------------------------------------------------------------------------

def generate_google_queries(skills, titles, companies, locations, exclusions) -> list:
    queries = []
    exclude_str = " " + " ".join(f"-{e}" for e in exclusions) if exclusions else ""
    title_group = f" ({' OR '.join(format_intitle(t) for t in titles)})" if titles else ""
    site_filter = "site:linkedin.com/in"

    if skills:
        strict_skills = f"({' '.join(skills)})"
        if not locations and not companies:
            query = f"{site_filter} {strict_skills}{title_group}{exclude_str}".strip()
            queries.append(_make_query("google", "Strict Skills", "All skills required", query))
        else:
            for i, batch in enumerate(batch_items(locations, 3)):
                loc_group = f" ({' OR '.join(format_term(l) for l in batch)})"
                query = f"{site_filter} {strict_skills}{title_group}{loc_group}{exclude_str}".strip()
                queries.append(_make_query("google", f"Strict + Location {i + 1}",
                                            f"All skills, locations: {', '.join(batch)}", query))
            for i, batch in enumerate(batch_items(companies, 3)):
                comp_group = f" ({' OR '.join(format_term(c) for c in batch)})"
                query = f"{site_filter} {strict_skills}{title_group}{comp_group}{exclude_str}".strip()
                queries.append(_make_query("google", f"Strict + Company {i + 1}",
                                            f"All skills, companies: {', '.join(batch)}", query))

    if len(skills) > 1:
        broad_skills = f"({' OR '.join(skills)})"
        if not locations and not companies:
            query = f"{site_filter} {broad_skills}{title_group}{exclude_str}".strip()
            queries.append(_make_query("google", "Broad Skills", "Any skill matches", query))
        else:
            for i, batch in enumerate(batch_items(locations, 3)):
                loc_group = f" ({' OR '.join(format_term(l) for l in batch)})"
                query = f"{site_filter} {broad_skills}{title_group}{loc_group}{exclude_str}".strip()
                queries.append(_make_query("google", f"Broad + Location {i + 1}",
                                            f"Any skill, locations: {', '.join(batch)}", query))
            for i, batch in enumerate(batch_items(companies, 3)):
                comp_group = f" ({' OR '.join(format_term(c) for c in batch)})"
                query = f"{site_filter} {broad_skills}{title_group}{comp_group}{exclude_str}".strip()
                queries.append(_make_query("google", f"Broad + Company {i + 1}",
                                            f"Any skill, companies: {', '.join(batch)}", query))

    if len(skills) > 2:
        for skill in skills:
            query = f"{site_filter} {format_term(skill)}{title_group}{exclude_str}".strip()
            queries.append(_make_query("google", f"Single: {skill}", f"Focus on {skill}", query))

    return queries


# ---------------------------------------------------------------------------
# GitHub generator
# ---------------------------------------------------------------------------

def generate_github_queries(skills, locations, min_followers, keywords) -> list:
    queries = []
    base_prefix = "type:user"
    follower_filter = f" followers:>{min_followers}" if min_followers > 0 else ""
    keyword_str = " " + " ".join(format_term(k) for k in keywords) if keywords else ""

    if not skills and not locations:
        if keywords or min_followers > 0:
            query = f"{base_prefix}{keyword_str}{follower_filter} in:name in:login".strip()
            queries.append(_make_query("github", "Keyword Search", "Search by keywords", query))
        return queries

    if skills:
        for lang in skills:
            if not locations:
                query = f"{base_prefix} language:{lang.lower()}{follower_filter}{keyword_str}".strip()
                queries.append(_make_query("github", f"Language: {lang}", f"Developers using {lang}", query))
            else:
                for i, batch in enumerate(batch_items(locations, 4)):
                    loc_str = "".join(f" location:{format_term(l)}" for l in batch)
                    query = f"{base_prefix}{loc_str} language:{lang.lower()}{follower_filter}{keyword_str}".strip()
                    queries.append(_make_query("github", f"{lang} + Locations {i + 1}",
                                                f"{lang} devs in {', '.join(batch)}", query))
    elif locations:
        for i, batch in enumerate(batch_items(locations, 4)):
            loc_str = "".join(f" location:{format_term(l)}" for l in batch)
            query = f"{base_prefix}{loc_str}{follower_filter}{keyword_str} in:name in:login".strip()
            queries.append(_make_query("github", f"Locations {i + 1}", f"Users in {', '.join(batch)}", query))

    return queries


# ---------------------------------------------------------------------------
# LinkedIn generator
# ---------------------------------------------------------------------------

def _build_or_group(terms: list, hack_mode: bool) -> str:
    if not terms:
        return ""
    if len(terms) == 1:
        return format_term(terms[0])
    if hack_mode:
        first = format_term(terms[0])
        rest = " ".join(f"OR({format_term(t)})" for t in terms[1:])
        return f"({first} {rest})"
    return f"({' OR '.join(format_term(t) for t in terms)})"


def _build_exclusions(terms: list, hack_mode: bool) -> str:
    if not terms:
        return ""
    if hack_mode:
        return " " + " ".join(f"NOT({format_term(t)})" for t in terms)
    return " " + " ".join(f"NOT {format_term(t)}" for t in terms)


def _title_part(titles: list, hack_mode: bool) -> str:
    if not titles:
        return ""
    group = _build_or_group(titles, hack_mode)
    return f" AND({group})" if hack_mode else f" AND {group}"


def generate_linkedin_queries(skills, titles, exclusions, hack_mode=False, facets=None) -> list:
    facets = facets or {}
    queries = []
    exclude_str = _build_exclusions(exclusions, hack_mode)
    has_facets = any(facets.get(k) for k in ("geoUrn", "currentCompany", "pastCompany", "industry", "schoolFilter"))

    def facet_desc():
        labels = {
            "geoUrn": "location(s)", "currentCompany": "current company(s)",
            "pastCompany": "past company(s)", "industry": "industry(s)", "schoolFilter": "school(s)",
        }
        parts = [f"{len(facets[k])} {label}" for k, label in labels.items() if facets.get(k)]
        return f" + {', '.join(parts)}" if parts else ""

    desc = facet_desc()

    if skills:
        if hack_mode:
            first = format_term(skills[0])
            rest = " ".join(f"AND({format_term(s)})" for s in skills[1:])
            skill_part = f"{first} {rest}" if rest else first
        else:
            skill_part = " AND ".join(format_term(s) for s in skills)

        query = f"{skill_part}{_title_part(titles, hack_mode)}{exclude_str}".strip()
        queries.append(_make_linkedin_query(
            f"Strict Skills{desc}",
            "All skills required" + (" with LinkedIn filters" if has_facets else ""),
            query, hack_mode, facets))

    if len(skills) > 1:
        skill_part = _build_or_group(skills, hack_mode)
        query = f"{skill_part}{_title_part(titles, hack_mode)}{exclude_str}".strip()
        queries.append(_make_linkedin_query(
            f"Broad Skills{desc}",
            "Any skill matches" + (" with LinkedIn filters" if has_facets else ""),
            query, hack_mode, facets))

    if len(skills) > 2 and not has_facets:
        for skill in skills:
            query = f"{format_term(skill)}{_title_part(titles, hack_mode)}{exclude_str}".strip()
            queries.append(_make_linkedin_query(f"Single: {skill}", f"Focus on {skill}", query, hack_mode, facets))

    if not skills and has_facets:
        title_group = _build_or_group(titles, hack_mode) if titles else ""
        query = f"{title_group}{exclude_str}".strip() if title_group else ""
        queries.append(_make_linkedin_query(
            f"Filters Only{desc}", "Search with LinkedIn filters only", query, hack_mode, facets))

    return queries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_queries(platform: str, inputs: dict, hack_mode: bool = False, facets: dict = None) -> dict:
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform!r} (expected google, github, or linkedin)")

    skills = parse_list(inputs.get("skills", []))
    titles = parse_list(inputs.get("titles", []))
    companies = parse_list(inputs.get("companies", []))
    locations = parse_list(inputs.get("locations", []))
    exclusions = parse_list(inputs.get("exclusions", []))
    keywords = parse_list(inputs.get("keywords", []))
    min_followers = int(inputs.get("min_followers", 0) or 0)

    if platform == "google":
        queries = generate_google_queries(skills, titles, companies, locations, exclusions)
    elif platform == "github":
        queries = generate_github_queries(skills, locations, min_followers, keywords)
    else:
        queries = generate_linkedin_queries(skills, titles, exclusions, hack_mode, facets)

    return {
        "platform": platform,
        "platform_name": PLATFORMS[platform]["display_name"],
        "queries": queries,
        "total_queries": len(queries),
        "valid_queries": sum(1 for q in queries if q["valid"]),
    }


def main():
    parser = argparse.ArgumentParser(description="Deterministic, platform-limit-validated boolean query generator")
    parser.add_argument("--platform", required=True, choices=["google", "github", "linkedin"])
    parser.add_argument("--skills", default="", help="Comma-separated skills/languages")
    parser.add_argument("--titles", default="", help="Comma-separated job titles")
    parser.add_argument("--companies", default="", help="Comma-separated companies (Google only)")
    parser.add_argument("--locations", default="", help="Comma-separated locations")
    parser.add_argument("--exclusions", default="", help="Comma-separated terms to exclude")
    parser.add_argument("--keywords", default="", help="Comma-separated bio/name keywords (GitHub only)")
    parser.add_argument("--min-followers", type=int, default=0, help="Minimum GitHub followers")
    parser.add_argument("--hack-mode", action="store_true", help="LinkedIn: bypass the 6-operator limit")
    args = parser.parse_args()

    inputs = {
        "skills": args.skills, "titles": args.titles, "companies": args.companies,
        "locations": args.locations, "exclusions": args.exclusions, "keywords": args.keywords,
        "min_followers": args.min_followers,
    }
    result = generate_queries(args.platform, inputs, hack_mode=args.hack_mode)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
