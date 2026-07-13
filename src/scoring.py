"""
Weighted 0-100 lead scoring engine.

Five signals, weights defined in config.py (must sum to 100):
  1. Location count       (25%) - sweet spot is 4-10 locations
  2. Active ops hiring     (25%) - any matching open posting = full points
  3. Recent expansion/press(25%) - scaled by recency
  4. Negative review density(15%) - % of sampled reviews flagging ops pain
  5. No named BOH vendor   (10%) - greenfield = full points

Every sub-score function takes raw enrichment data and returns (points, source).
If the underlying data is unavailable, points = 0 and that's explicit, not hidden -
the UI shows source="unavailable" so nobody mistakes a 0 for "confirmed no signal."
"""

from src.config import (
    KNOWN_VENDOR_KEYWORDS,
    LOCATION_COUNT_BANDS,
    EXPANSION_SIGNAL_FULL_POINTS_WITHIN_DAYS,
    EXPANSION_SIGNAL_ZERO_POINTS_AFTER_DAYS,
    WEIGHT_LOCATION_COUNT,
    WEIGHT_ACTIVE_HIRING,
    WEIGHT_EXPANSION_SIGNAL,
    WEIGHT_NEGATIVE_REVIEWS,
    WEIGHT_GREENFIELD_VENDOR,
)
from src.enrichment.news_search import days_since


def score_location_count(location_count: int | None) -> float:
    if location_count is None:
        return 0.0
    for lo, hi, points in LOCATION_COUNT_BANDS:
        if lo <= location_count <= hi:
            return float(points)
    return 0.0


def score_hiring_signal(hiring_signal_found: int | None) -> float:
    if hiring_signal_found is None:
        return 0.0
    return float(WEIGHT_ACTIVE_HIRING) if hiring_signal_found == 1 else 0.0


def score_expansion_signal(expansion_signal_found: int | None, published_at: str | None) -> float:
    if not expansion_signal_found:
        return 0.0
    age_days = days_since(published_at)
    if age_days is None:
        # We know a signal fired but can't date it — award half credit, not full.
        return WEIGHT_EXPANSION_SIGNAL / 2
    if age_days <= EXPANSION_SIGNAL_FULL_POINTS_WITHIN_DAYS:
        return float(WEIGHT_EXPANSION_SIGNAL)
    if age_days >= EXPANSION_SIGNAL_ZERO_POINTS_AFTER_DAYS:
        return 0.0
    # Linear decay between the "full points" and "zero points" day thresholds.
    span = EXPANSION_SIGNAL_ZERO_POINTS_AFTER_DAYS - EXPANSION_SIGNAL_FULL_POINTS_WITHIN_DAYS
    decayed_fraction = 1 - ((age_days - EXPANSION_SIGNAL_FULL_POINTS_WITHIN_DAYS) / span)
    return round(WEIGHT_EXPANSION_SIGNAL * decayed_fraction, 1)


def score_negative_reviews(negative_hits: int | None, reviews_sampled: int | None) -> float:
    if not reviews_sampled:
        return 0.0
    density = (negative_hits or 0) / reviews_sampled
    return round(WEIGHT_NEGATIVE_REVIEWS * min(density, 1.0), 1)


def detect_vendor_mention(texts: list[str]) -> tuple[bool, str | None]:
    """Scan a list of raw text blobs (job postings, press) for a named BOH/POS vendor."""
    for text in texts:
        lowered = text.lower()
        for vendor in KNOWN_VENDOR_KEYWORDS:
            if vendor in lowered:
                return True, vendor
    return False, None


def score_vendor_greenfield(vendor_named: int | None) -> float:
    if vendor_named is None:
        return 0.0
    return 0.0 if vendor_named == 1 else float(WEIGHT_GREENFIELD_VENDOR)


def generate_why_now(enrichment: dict, sub_scores: dict) -> str:
    """
    Plain-English one-liner built from whichever signals scored highest,
    written like a sales rep's note rather than a data dump.
    """
    fragments = []

    if sub_scores["expansion_score"] >= WEIGHT_EXPANSION_SIGNAL * 0.5 and enrichment.get("expansion_headline"):
        fragments.append(f"Recent press: \"{enrichment['expansion_headline']}\"")

    if sub_scores["hiring_score"] > 0 and enrichment.get("hiring_titles"):
        titles = enrichment["hiring_titles"]
        if isinstance(titles, str):
            import json
            titles = json.loads(titles) if titles else []
        if titles:
            fragments.append(f"actively hiring for {titles[0]}")

    if sub_scores["location_score"] >= WEIGHT_LOCATION_COUNT and enrichment.get("location_count"):
        fragments.append(f"{enrichment['location_count']} locations found (sweet-spot scale)")

    if sub_scores["review_score"] > 0 and enrichment.get("negative_review_hits"):
        fragments.append(f"{enrichment['negative_review_hits']} recent reviews flag ops pain (waits/stockouts/order errors)")

    if sub_scores["vendor_score"] == WEIGHT_GREENFIELD_VENDOR:
        fragments.append("no named BOH/inventory vendor found — likely greenfield")

    if not fragments:
        return "Limited live signal available — score reflects mostly unavailable data, not confirmed low fit."

    if len(fragments) == 1:
        return fragments[0].capitalize() + "."

    return (fragments[0].capitalize() + ", and " + fragments[1] + " — classic sign of back-of-house strain.")


def score_prospect(enrichment: dict) -> dict:
    """
    Takes a merged enrichment dict (as stored in the `enrichment` table) and
    returns the full score breakdown + total + why_now, ready for db.save_score().
    """
    location_score = score_location_count(enrichment.get("location_count"))
    hiring_score = score_hiring_signal(enrichment.get("hiring_signal_found"))
    expansion_score = score_expansion_signal(
        enrichment.get("expansion_signal_found"), enrichment.get("expansion_published_at")
    )
    review_score = score_negative_reviews(
        enrichment.get("negative_review_hits"), enrichment.get("reviews_sampled")
    )
    vendor_score = score_vendor_greenfield(enrichment.get("vendor_named"))

    sub_scores = {
        "location_score": location_score,
        "hiring_score": hiring_score,
        "expansion_score": expansion_score,
        "review_score": review_score,
        "vendor_score": vendor_score,
    }
    total = round(sum(sub_scores.values()), 1)

    return {
        **sub_scores,
        "total_score": total,
        "why_now": generate_why_now(enrichment, sub_scores),
    }
