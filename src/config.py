"""
Scoring weights and seed prospect list for the Truffle Prospect Engine.

All weights are adjustable constants — tune them here, nothing else needs to change.
"""

import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prospects.db")

# ---------------------------------------------------------------------------
# Scoring weights (must sum to 100)
# ---------------------------------------------------------------------------
WEIGHT_LOCATION_COUNT = 25
WEIGHT_ACTIVE_HIRING = 25
WEIGHT_EXPANSION_SIGNAL = 25
WEIGHT_NEGATIVE_REVIEWS = 15
WEIGHT_GREENFIELD_VENDOR = 10

assert (
    WEIGHT_LOCATION_COUNT
    + WEIGHT_ACTIVE_HIRING
    + WEIGHT_EXPANSION_SIGNAL
    + WEIGHT_NEGATIVE_REVIEWS
    + WEIGHT_GREENFIELD_VENDOR
    == 100
), "Scoring weights must sum to 100"

# Location count → points out of WEIGHT_LOCATION_COUNT.
# Sweet spot is 4-10 locations: big enough to have real ops complexity,
# small enough that they likely don't already have enterprise tooling.
LOCATION_COUNT_BANDS = [
    # (min_locations, max_locations_inclusive, points_out_of_25)
    (0, 1, 5),
    (2, 3, 15),
    (4, 10, 25),
    (11, 999, 10),
]

# Recency decay for the expansion-signal score. A press hit "this month"
# is worth full points; a hit from 6 months ago is worth close to none.
EXPANSION_SIGNAL_FULL_POINTS_WITHIN_DAYS = 30
EXPANSION_SIGNAL_ZERO_POINTS_AFTER_DAYS = 180

# Keywords used to flag operational-pain review content (case-insensitive substring match).
NEGATIVE_REVIEW_KEYWORDS = [
    "out of stock", "sold out", "ran out", "no longer available",
    "long wait", "long line", "slow service", "took forever", "waited",
    "wrong order", "missing item", "order was wrong", "incorrect order",
    "understaffed", "short staffed", "inconsistent", "hit or miss",
]

# Job-title keywords that indicate active back-of-house / multi-unit ops hiring.
OPS_HIRING_KEYWORDS = [
    "kitchen manager", "inventory", "purchasing manager", "purchasing coordinator",
    "multi-unit", "multi unit", "operations manager", "director of operations",
    "back of house", "boh manager", "supply chain",
]

# Named BOH/inventory/POS vendors to scan for in job postings and press text.
# A hit means the group is NOT greenfield for that category (they already have a vendor).
KNOWN_VENDOR_KEYWORDS = [
    "toast pos", "toasttab", "square pos", "marginedge", "restaurant365", "r365",
    "compeat", "craftable", "apicbase", "supy", "orderly", "meez", "yellow dog",
    "ctuit", "sculpture hospitality", "partender", "bevspot", "optimum control",
    "truffle",  # mentioned by name = already a Truffle customer/lead, not greenfield
]

# ---------------------------------------------------------------------------
# Seed list — 10 real NYC multi-unit restaurant groups (user-researched).
#
# `search_query` is the fallback Google Places Text Search query (group name).
# `location_queries` is a list of per-concept queries used instead, when
# available — Places Text Search returns the single best match for a compound
# query, so a group with several differently-branded concepts (e.g. Major
# Food Group's Carbone/Sadelle's/Torrisi/Parm) needs one query per concept to
# get an accurate location count, not one query on the umbrella group name.
#
# Concept names below are exactly the ones the user supplied in their brief —
# nothing invented. Groups the user described without a concept breakdown
# (USHG, NoHo Hospitality, HAND, City Roots, Happy Cooking) fall back to a
# single group-name query, which likely UNDERcounts their true location count.
# This is a documented limitation, not a hidden one — see README.
# ---------------------------------------------------------------------------
SEED_PROSPECTS = [
    {
        "name": "Union Square Hospitality Group",
        "short_name": "USHG",
        "search_query": "Union Square Hospitality Group restaurant New York",
        # Verified via ushg.com/restaurants (fetched live) — NYC venues only,
        # excludes Union Square Tokyo and Pine Hall (Detroit).
        "location_queries": [
            "The View New York", "Ci Siamo New York", "Daily Provisions New York",
            "Gramercy Tavern New York", "Marta New York", "Manhatta New York",
            "Porchlight New York", "Union Square Cafe New York", "The Modern New York",
            "Tacocina New York", "Maialino New York",
        ],
        "notes": "Danny Meyer's group. Large, mature, likely already has enterprise tooling — control case.",
    },
    {
        "name": "Major Food Group",
        "short_name": "MFG",
        "search_query": "Major Food Group Carbone New York",
        "location_queries": ["Carbone New York", "Sadelle's New York", "Torrisi New York", "Parm New York"],
        "notes": "11 NYC properties (Carbone, Sadelle's, Torrisi, Parm). Large scale, complex multi-concept ops.",
    },
    {
        "name": "NoHo Hospitality Group",
        "short_name": "NoHo Hospitality",
        "search_query": "NoHo Hospitality Group Andrew Carmellini New York",
        # Verified via nhgnyc.com/locations (fetched live) — NYC venues only.
        # Excludes San Morello, Evening Bar, The Brakeman, Penny Red's, Gilly's
        # Clubhouse, Saksey's, Bar Torino — all Shinola Hotel Detroit / out-of-market.
        "location_queries": [
            "Locanda Verde New York", "The Dutch New York", "Lafayette New York",
            "Joe's Pub New York", "Bar Primi New York", "Westlight New York",
            "Leuca New York", "Mister Dips New York", "Carne Mare New York",
            "Café Carmellini New York", "The Portrait Bar New York",
        ],
        "notes": "Andrew Carmellini's group, 10+ years old, multiple concepts across hotels/nightlife/restaurants.",
    },
    {
        "name": "Golden Age Hospitality",
        "short_name": "Golden Age",
        "search_query": "Golden Age Hospitality Jon Neidich New York",
        "location_queries": ["Monsieur New York", "Deux Chats New York"],
        "notes": "Jon Neidich, cocktail-bar/nightlife-leaning, active recent expansion (Monsieur, Deux Chats).",
    },
    {
        "name": "Unapologetic Foods",
        "short_name": "Unapologetic Foods",
        "search_query": "Unapologetic Foods Semma Dhamaka New York",
        "location_queries": [
            "Semma New York", "Dhamaka New York", "Adda New York",
            "Masalawala & Sons New York", "Rowdy Rooster New York",
            "Naks New York", "Biryani Bol New York",
        ],
        "notes": "Roni Mazumdar & Chintan Pandya. 7+ concepts, fast growth since 2017, Michelin star, heavy press.",
    },
    {
        "name": "The Group (Boucherie)",
        "short_name": "Boucherie",
        "search_query": "Boucherie restaurant New York",
        "location_queries": [
            "Omakase Room New York", "Olio e Più New York", "La Grande Boucherie New York",
            "Boucherie West Village New York", "Boucherie Union Square New York", "Petite Boucherie New York",
        ],
        "notes": "Omakase Room, Olio e Più, La Grande Boucherie, Boucherie West Village/Union Square. Replication pattern.",
    },
    {
        "name": "New York City Restaurant Group",
        "short_name": "NYCRG",
        "search_query": "Tony's Di Napoli New York City Restaurant Group",
        "location_queries": ["Tony's Di Napoli New York", "Viva Cucina New York", "Il Bastardo New York"],
        "notes": "8 restaurants (Tony's Di Napoli, Viva Cucina, Il Bastardo). Mid-size sweet-spot candidate.",
    },
    {
        "name": "HAND Hospitality",
        "short_name": "HAND Hospitality",
        "search_query": "HAND Hospitality restaurant New York",
        # Verified via handhospitality.com/brands (fetched live) — full 20-brand
        # portfolio. The site doesn't tag each brand's city (HAND also operates
        # in LA), so all 20 are queried; any without a real NYC location simply
        # won't return a match and won't be counted — no manual city filtering
        # was reliable enough to do by hand here, so the live API is the filter.
        "location_queries": [
            "Take31 New York", "Cho Dang Gol New York", "Her Name Is Han New York",
            "Izakaya Mew New York", "Nonono New York", "Atoboy New York", "Jua New York",
            "Lysée New York", "Okdongsik New York", "George Bang Bang New York",
            "Seoul Salon New York", "Moono New York", "Hojokban New York",
            "Okonomi YUJI Ramen New York", "AriAri New York", "Joo OK New York",
            "Samwoojung New York", "everydaily New York", "ODRE New York", "HORI New York",
        ],
        "notes": "Regional-cuisine-focused group, multiple locations.",
    },
    {
        "name": "City Roots Hospitality Group",
        "short_name": "City Roots",
        "search_query": "City Roots Hospitality plant-based New York",
        # Verified via cityrootsnyc.com (fetched live) — all 7 NYC concepts.
        "location_queries": [
            "Beyond Sushi New York", "Willow New York", "Coletta New York",
            "Anixi New York", "Sentir New York", "Le Basque New York", "Reverie Brooklyn",
        ],
        "notes": "Plant-based, multiple NYC + event locations.",
    },
    {
        "name": "Happy Cooking Hospitality",
        "short_name": "Happy Cooking",
        "search_query": "Happy Cooking Hospitality New York restaurant",
        "location_queries": None,
        "notes": "Multiple Manhattan locations.",
    },
]
