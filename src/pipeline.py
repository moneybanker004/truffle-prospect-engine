"""
Ties together enrichment (Google Places + Greenhouse/Lever + NewsAPI) and
scoring for one or all seed prospects, and persists results to SQLite.
"""

import json

from src import db
from src.config import GOOGLE_PLACES_API_KEY, NEWSAPI_KEY, SEED_PROSPECTS
from src.enrichment.google_places import enrich_group
from src.enrichment.jobs_search import enrich_hiring_signal
from src.enrichment.news_search import enrich_expansion_signal
from src.scoring import detect_vendor_mention, score_prospect


def enrich_and_score_prospect(seed: dict, verbose: bool = False) -> dict:
    prospect_id = db.upsert_prospect(
        name=seed["name"],
        short_name=seed["short_name"],
        notes=seed["notes"],
        search_query=seed["search_query"],
    )

    if verbose:
        print(f"[{seed['short_name']}] Querying Google Places...")
    places_data = enrich_group(
        seed["search_query"], GOOGLE_PLACES_API_KEY, location_queries=seed.get("location_queries")
    )

    if verbose:
        print(f"[{seed['short_name']}] Checking Greenhouse/Lever job boards...")
    hiring_data = enrich_hiring_signal(seed["name"])

    # A live Greenhouse/Lever board always wins. But if this run found nothing
    # (the common case for hospitality groups) and the user had previously
    # entered a manual override, keep the manual value instead of clobbering
    # it back to "unavailable" on every refresh.
    if hiring_data["hiring_source"] == "unavailable":
        existing = db.get_prospect_detail(prospect_id)
        if existing and existing.get("hiring_source") == "manual_csv":
            titles = existing.get("hiring_titles")
            hiring_data = {
                "hiring_signal_found": existing.get("hiring_signal_found"),
                "hiring_titles": json.loads(titles) if isinstance(titles, str) and titles else [],
                "hiring_source": "manual_csv",
                "job_texts": [],
            }

    if verbose:
        print(f"[{seed['short_name']}] Querying NewsAPI for expansion signal...")
    news_data = enrich_expansion_signal(seed["name"], NEWSAPI_KEY)

    # Vendor greenfield check scans whatever text we actually have — job
    # postings and the top press headline. If neither source was available,
    # vendor presence is honestly unknown, not assumed greenfield.
    scan_texts = list(hiring_data.pop("job_texts", []))
    if news_data.get("expansion_headline"):
        scan_texts.append(news_data["expansion_headline"])

    if hiring_data["hiring_source"] == "unavailable" and news_data["expansion_source"] == "unavailable":
        vendor_named, vendor_name, vendor_source = None, None, "unavailable"
    else:
        found, name = detect_vendor_mention(scan_texts)
        vendor_named = 1 if found else 0
        vendor_name = name
        sources = [s for s in (hiring_data["hiring_source"], news_data["expansion_source"]) if s != "unavailable"]
        vendor_source = "+".join(sources)

    enrichment = {
        **places_data,
        **hiring_data,
        **news_data,
        "vendor_named": vendor_named,
        "vendor_name": vendor_name,
        "vendor_source": vendor_source,
    }
    db.save_enrichment(prospect_id, enrichment)

    score = score_prospect(enrichment)
    db.save_score(prospect_id, score)
    db.log_snapshot(prospect_id, enrichment.get("location_count"), score["total_score"])

    if verbose:
        print(f"[{seed['short_name']}] Score: {score['total_score']}/100 — {score['why_now']}")

    return {"prospect_id": prospect_id, "enrichment": enrichment, "score": score}


def run_pipeline(seeds: list[dict] = None, verbose: bool = True) -> list[dict]:
    db.init_db()
    seeds = seeds if seeds is not None else SEED_PROSPECTS
    return [enrich_and_score_prospect(seed, verbose=verbose) for seed in seeds]


if __name__ == "__main__":
    run_pipeline()
