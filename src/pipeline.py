"""
Ties together enrichment (Google Places + Greenhouse/Lever + NewsAPI) and
scoring for one or all seed prospects, and persists results to SQLite.
"""

import json
import sys

from src import db
from src.config import GOOGLE_PLACES_API_KEY, NEWSAPI_KEY, SEED_PROSPECTS
from src.discovery import discover_independent_restaurants
from src.enrichment.google_places import enrich_group, negative_hits_for_reviews
from src.enrichment.jobs_search import enrich_hiring_signal
from src.enrichment.news_search import enrich_expansion_signal
from src.scoring import detect_vendor_mention, score_location_pain, score_prospect


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

    # Per-location breakdown isn't a column on the enrichment table (it's a
    # list of restaurants, not a scalar) — pull it out and persist separately.
    individual_locations = places_data.pop("locations", [])

    enrichment = {
        **places_data,
        **hiring_data,
        **news_data,
        "vendor_named": vendor_named,
        "vendor_name": vendor_name,
        "vendor_source": vendor_source,
    }
    db.save_enrichment(prospect_id, enrichment)

    for loc in individual_locations:
        if not loc.get("place_id"):
            continue
        db.save_location(prospect_id, {
            "place_id": loc["place_id"],
            "name": loc["name"],
            "rating": loc.get("rating"),
            "reviews_sampled": loc.get("reviews_sampled"),
            "negative_review_hits": loc.get("negative_review_hits"),
            "negative_review_examples": loc.get("negative_review_examples", []),
            "pain_score": score_location_pain(loc.get("negative_review_hits"), loc.get("reviews_sampled")),
            "source": "google_places_api",
        })

    score = score_prospect(enrichment)
    db.save_score(prospect_id, score)
    db.log_snapshot(prospect_id, enrichment.get("location_count"), score["total_score"])

    if verbose:
        print(f"[{seed['short_name']}] Score: {score['total_score']}/100 — {score['why_now']}")

    return {"prospect_id": prospect_id, "enrichment": enrichment, "score": score}


def run_discovery(verbose: bool = True) -> list[dict]:
    """
    Finds standalone NYC restaurants not tied to any of the 10 seed groups.
    See src/discovery.py for how ("best new / trending" Places queries,
    verified real/open/food-category, deduped against known group locations).
    """
    known_ids = db.get_known_group_place_ids()
    discovered = discover_independent_restaurants(GOOGLE_PLACES_API_KEY, known_ids, verbose=verbose)

    for place in discovered:
        negative_hits = negative_hits_for_reviews(place["reviews"])
        db.save_location(None, {
            "place_id": place["place_id"],
            "name": place["name"],
            "rating": place.get("rating"),
            "reviews_sampled": place.get("reviews_sampled"),
            "negative_review_hits": len(negative_hits),
            "negative_review_examples": negative_hits[:3],
            "pain_score": score_location_pain(len(negative_hits), place.get("reviews_sampled")),
            "discovered_via": place.get("discovered_via"),
            "source": "google_places_api",
        })
        if verbose:
            print(f"[discovery] {place['name']} — pain score computed, saved")

    return discovered


def run_pipeline(seeds: list[dict] = None, verbose: bool = True, discover: bool = True) -> list[dict]:
    db.init_db()
    seeds = seeds if seeds is not None else SEED_PROSPECTS
    results = [enrich_and_score_prospect(seed, verbose=verbose) for seed in seeds]
    if discover:
        run_discovery(verbose=verbose)
    return results


if __name__ == "__main__":
    # Restaurant names/reviews can contain characters outside Windows' default
    # console codepage (e.g. Vietnamese diacritics) — reconfigure so a verbose
    # print never crashes the run; the actual DB writes are unaffected either way.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run_pipeline()
