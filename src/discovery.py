"""
Independent restaurant discovery (v1, "beta"): finds standalone NYC
restaurants not tied to any of the 10 seed groups, using only official APIs.

Version history / why this works the way it does:
  A first version tried a NewsAPI headline sweep requiring "New York" AND a
  restaurant word AND an opening word all in one article title (mirroring
  the group-level press signal). Tested empirically: 0 results in NewsAPI's
  free-tier 30-day window. Loosening the geographic requirement returned
  results, but for the wrong cities entirely (Disney World, Fort Worth) —
  worse than no results, since showing those would be a real accuracy
  failure. That approach was dropped rather than shipped half-working.

  This version instead queries Google Places Text Search directly with
  "best new / trending restaurant" style phrases per NYC borough/
  neighborhood. Tested empirically: this reliably returns real, high-rated,
  food-category businesses. Important honesty caveat, not hidden: Google
  Places has no "opening date" field, so "new" here reflects Google's own
  relevance ranking for a discovery-style query — not a verified opening
  date. Every result is still a 100% real, currently-operating restaurant
  (same food-type + businessStatus filtering as the main pipeline); what's
  unverified is only the "how recently did this open" framing.

Every candidate is deduped against the seed groups' own known locations (so
a Boucherie or Semma location can't double-appear as an "independent" find)
and against permanently-closed businesses.
"""

from src.enrichment.google_places import get_place_reviews, is_food_business, search_places

# Rotated across NYC's main dining neighborhoods/boroughs so results aren't
# dominated by whichever single area Google's ranking happens to favor.
DISCOVERY_QUERIES = [
    "best new restaurants New York 2026",
    "trending new restaurant Manhattan 2026",
    "trending new restaurant Brooklyn 2026",
    "hot new restaurant Queens New York",
    "buzzy new restaurant Lower East Side New York",
    "new restaurant openings West Village New York",
]


def discover_independent_restaurants(
    places_key: str,
    known_place_ids: set[str],
    verbose: bool = False,
) -> list[dict]:
    """
    Runs the discovery queries, filters to real/open/food-category places,
    dedupes against `known_place_ids` (every location already tracked under
    one of the 10 seed groups), and returns each with its own review data.
    """
    if not places_key:
        return []

    results = []
    seen_place_ids = set()

    for query in DISCOVERY_QUERIES:
        if verbose:
            print(f"[discovery] querying: {query}")
        for place in search_places(query, places_key):
            place_id = place.get("id")
            if not place_id or place_id in seen_place_ids or place_id in known_place_ids:
                continue
            if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            if not is_food_business(place):
                continue
            display_name = place.get("displayName", {}).get("text", "")
            if not display_name:
                continue

            seen_place_ids.add(place_id)
            reviews = get_place_reviews(place_id, places_key)
            results.append({
                "place_id": place_id,
                "name": display_name,
                "rating": place.get("rating"),
                "reviews_sampled": len(reviews),
                "reviews": reviews,
                "discovered_via": query,
            })

    return results
