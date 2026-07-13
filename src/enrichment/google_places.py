"""
Google Places API (New) enrichment: location count, ratings, and review text
for a restaurant group, found via Text Search on the group/brand name.

Two live endpoints, both under the Places API (New) free-tier usage:
  - Text Search   (places:searchText)   -> list of matching locations
  - Place Details (places/{id})         -> up to 5 most recent reviews per location

Nothing here is fabricated: if the API key is missing or a call fails, every
field is returned as None with source="unavailable" rather than a guessed value.

Known limitations (real, not hidden):
  - Text Search matches on relevance, not an authoritative franchise registry,
    so location_count is a heuristic count of matching Places results (max 20
    per query in this implementation), not a verified location total.
  - Google only exposes up to 5 reviews per place via the Details API, so
    review-sentiment signal is based on a small, Google-selected sample —
    not the full review history.
"""

import re

import requests

from src.config import NEGATIVE_REVIEW_KEYWORDS

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.rating,places.userRatingCount,"
    "places.types,places.businessStatus"
)
DETAILS_FIELD_MASK = "id,displayName,reviews"

# A review's own star rating gates whether a keyword hit counts as a real
# complaint. Without this, common words like "waited" match constantly in
# glowing reviews too ("I waited all winter for this dish", 5 stars) — tested
# and confirmed this was producing false positives before the rating gate.
NEGATIVE_REVIEW_MAX_RATING = 3

# Substrings that show up in Google's food/drink place types (e.g.
# "french_restaurant", "wine_bar", "coffee_shop", "night_club"). A generic
# single-word brand name like "Lafayette" will happily text-match a French
# department store or a clothing label — requiring an actual food/drink type
# on the result caught that class of false positive in testing.
FOOD_PLACE_TYPE_KEYWORDS = [
    "restaurant", "food", "bar", "cafe", "bakery", "meal_takeaway",
    "meal_delivery", "night_club", "dessert", "coffee", "diner", "bistro",
    "pub", "catering", "brunch", "steak_house",
]


def _empty_result(reason: str = "unavailable") -> dict:
    return {
        "location_count": None,
        "location_count_source": reason,
        "location_names": [],
        "avg_rating": None,
        "reviews_sampled": None,
        "negative_review_hits": None,
        "negative_review_examples": [],
        "review_source": reason,
        "locations": [],
    }


def negative_hits_for_reviews(reviews: list[dict]) -> list[str]:
    """Keyword hit AND the reviewer's own rating ≤3 — see NEGATIVE_REVIEW_MAX_RATING."""
    hits = []
    for review in reviews:
        review_text = review["text"]
        review_rating = review.get("rating")
        lowered = review_text.lower()
        keyword_hit = any(keyword in lowered for keyword in NEGATIVE_REVIEW_KEYWORDS)
        rating_confirms_complaint = review_rating is not None and review_rating <= NEGATIVE_REVIEW_MAX_RATING
        if keyword_hit and rating_confirms_complaint:
            hits.append(review_text[:200])
    return hits


def _brand_name(query: str) -> str:
    """Strip a trailing '... New York' off a per-concept query to get the bare brand name."""
    return re.sub(r"\s*new york\s*$", "", query, flags=re.IGNORECASE).strip()


def _is_relevant_match(display_name: str, brand_name: str) -> bool:
    """
    True if `display_name` is plausibly a location of `brand_name`.

    Text Search ranks by relevance, not exact match — for a made-up-sounding
    or ambiguous query (e.g. "Biryani Bol New York") it will happily return
    loosely related places ("Royal Biryani Pakistani Halal Food") rather than
    zero results. Requiring the brand name as a substring of the result (or
    vice versa) keeps only places that are actually plausibly that concept.
    """
    dn = display_name.lower().strip()
    bn = brand_name.lower().strip()
    if not dn or not bn:
        return False
    return bn in dn or dn in bn


def is_food_business(place: dict) -> bool:
    """True if Google categorizes this place as a restaurant/bar/cafe/etc.

    Filters out same-name non-food businesses a loose text match can pull in
    (a department store, a clothing brand, an unrelated town) — see module
    docstring / FOOD_PLACE_TYPE_KEYWORDS.
    """
    types = [t.lower() for t in place.get("types", [])]
    return any(any(kw in t for kw in FOOD_PLACE_TYPE_KEYWORDS) for t in types)


def search_places(query: str, api_key: str) -> list[dict]:
    """Text-search for places matching `query`. Returns [] on any failure."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": SEARCH_FIELD_MASK,
    }
    body = {"textQuery": query, "maxResultCount": 20}
    try:
        resp = requests.post(SEARCH_URL, json=body, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("places", [])
    except requests.RequestException:
        return []


def get_place_reviews(place_id: str, api_key: str) -> list[dict]:
    """Fetch up to 5 reviews (text + the reviewer's own star rating) for a place."""
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": DETAILS_FIELD_MASK,
    }
    try:
        resp = requests.get(DETAILS_URL.format(place_id=place_id), headers=headers, timeout=15)
        resp.raise_for_status()
        reviews = resp.json().get("reviews", [])
        return [
            {"text": r.get("text", {}).get("text", ""), "rating": r.get("rating")}
            for r in reviews if r.get("text")
        ]
    except requests.RequestException:
        return []


def enrich_group(search_query: str, api_key: str, location_queries: list[str] | None = None) -> dict:
    """
    Full Google Places enrichment for one restaurant group.
    Returns location count/names, average rating, and negative-review keyword hits.

    If `location_queries` is given (one query per known concept/brand name),
    each is searched separately and results are deduped by place id — this
    avoids undercounting groups whose concepts don't share a brand name
    (Text Search returns the single best match for a compound query, not an
    enumeration of every property tied to a group). Falls back to a single
    `search_query` on the group name when no concept list is available.
    """
    if not api_key:
        return _empty_result("unavailable")

    using_concepts = bool(location_queries)
    queries = location_queries if using_concepts else [search_query]

    places_by_id = {}
    for query in queries:
        brand = _brand_name(query)
        for place in search_places(query, api_key):
            place_id = place.get("id")
            if not place_id or place_id in places_by_id:
                continue
            if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            if not is_food_business(place):
                continue
            display_name = place.get("displayName", {}).get("text", "")
            # Only filter by name relevance in per-concept mode. The single
            # group-name fallback already returns just the top match(es), and
            # filtering against the full compound group name would wrongly
            # exclude a correctly-matched result whose name differs from it.
            if using_concepts and not _is_relevant_match(display_name, brand):
                continue
            places_by_id[place_id] = place

    places = list(places_by_id.values())
    if not places:
        return _empty_result("unavailable")

    location_names = []
    ratings = []
    all_reviews = []
    locations = []  # per-place breakdown, for individual-location scoring

    for place in places:
        display_name = place.get("displayName", {}).get("text", "")
        if display_name:
            location_names.append(display_name)
        if place.get("rating") is not None:
            ratings.append(place["rating"])

        place_id = place.get("id")
        place_reviews = get_place_reviews(place_id, api_key) if place_id else []
        all_reviews.extend(place_reviews)
        place_negative_hits = negative_hits_for_reviews(place_reviews)

        locations.append({
            "place_id": place_id,
            "name": display_name,
            "rating": place.get("rating"),
            "reviews_sampled": len(place_reviews),
            "negative_review_hits": len(place_negative_hits),
            "negative_review_examples": place_negative_hits[:3],
        })

    negative_hits = negative_hits_for_reviews(all_reviews)

    # Fallback (no concept list) mode often just returns the group's own
    # corporate Google Business Profile entry rather than a real restaurant
    # location — tag it distinctly so the UI never presents it as a verified
    # count. See docstring: this is the documented undercount limitation.
    location_source = "google_places_api" if using_concepts else "google_places_api_estimate"

    return {
        "location_count": len(places),
        "location_count_source": location_source,
        "location_names": location_names,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "reviews_sampled": len(all_reviews),
        "negative_review_hits": len(negative_hits),
        "negative_review_examples": negative_hits[:5],
        "review_source": "google_places_api" if all_reviews else "unavailable",
        "locations": locations,
    }
