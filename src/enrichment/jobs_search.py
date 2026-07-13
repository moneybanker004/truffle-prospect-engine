"""
Active hiring signal via Greenhouse and Lever's PUBLIC job board APIs.

Both are free, require no API key, and are explicitly meant to be queried by
third parties (that's what they're for — public job boards). No LinkedIn or
Indeed scraping is done anywhere in this project, per instructions: Indeed
does not offer a usable public/no-auth job search API for this use case, and
LinkedIn's ToS prohibits scraping outright, so those two sources are skipped
entirely rather than faked.

Reality check: most small/mid-size hospitality groups do not run their
careers page on Greenhouse or Lever (they're more common at tech companies).
We try a handful of reasonable slug guesses derived from the company name;
if none resolve to a real board, the signal is honestly reported as
source="unavailable" rather than guessed.
"""

import re

import requests

from src.config import OPS_HIRING_KEYWORDS

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{company}?mode=json"


def _slug_candidates(name: str) -> list[str]:
    base = name.lower()
    base = re.sub(r"\b(hospitality group|hospitality|group|the)\b", "", base)
    base = base.strip()
    no_space = re.sub(r"[^a-z0-9]", "", base)
    hyphenated = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    candidates = {no_space, hyphenated}
    return [c for c in candidates if c]


def _check_greenhouse(slug: str) -> list[dict] | None:
    try:
        resp = requests.get(GREENHOUSE_URL.format(token=slug), timeout=10)
        if resp.status_code == 200:
            jobs = resp.json().get("jobs", [])
            if jobs:
                return jobs
    except requests.RequestException:
        pass
    return None


def _check_lever(slug: str) -> list[dict] | None:
    try:
        resp = requests.get(LEVER_URL.format(company=slug), timeout=10)
        if resp.status_code == 200:
            jobs = resp.json()
            if isinstance(jobs, list) and jobs:
                return jobs
    except requests.RequestException:
        pass
    return None


def enrich_hiring_signal(company_name: str) -> dict:
    """
    Looks for open ops/kitchen-manager-type postings on Greenhouse or Lever.
    Returns hiring_signal_found / hiring_titles / hiring_source ("greenhouse_api",
    "lever_api", or "unavailable" if no board could be found under any of the
    tried slugs).
    """
    for slug in _slug_candidates(company_name):
        jobs = _check_greenhouse(slug)
        source = "greenhouse_api"
        if jobs is None:
            jobs = _check_lever(slug)
            source = "lever_api"
        if jobs is not None:
            titles = [j.get("title", "") for j in jobs]
            matched = [
                t for t in titles
                if any(kw in t.lower() for kw in OPS_HIRING_KEYWORDS)
            ]
            # Lever includes description text inline; Greenhouse's list endpoint
            # only gives titles, so job_texts is best-effort (used for the
            # vendor-mention scan, not stored/shown as a signal on its own).
            job_texts = titles + [
                j.get("descriptionPlain", "") for j in jobs if j.get("descriptionPlain")
            ]
            return {
                "hiring_signal_found": 1 if matched else 0,
                "hiring_titles": matched,
                "hiring_source": source,
                "job_texts": job_texts,
            }

    return {
        "hiring_signal_found": None,
        "hiring_titles": [],
        "hiring_source": "unavailable",
        "job_texts": [],
    }
