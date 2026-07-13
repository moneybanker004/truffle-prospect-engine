# Truffle Prospect Engine

A scored, ranked list of NYC multi-unit restaurant groups likely to need
Truffle's back-of-house platform right now — built as a prospecting demo,
not production sales infrastructure.

Every number on the dashboard either came from a live API call or is
explicitly marked "data unavailable." Nothing is fabricated or guessed.

## Quick start

```bash
cd truffle-prospect-engine
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on Mac/Linux
pip install -r requirements.txt
cp .env.example .env          # then fill in your API key(s)
streamlit run app.py
```

Open the app, click **Run / refresh enrichment** to pull live data for the
10 seed companies (takes ~30-60 seconds), then explore the ranked table and
per-prospect detail view.

### Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (`.env` is gitignored — it will not be pushed).
2. On [share.streamlit.io](https://share.streamlit.io), point a new app at
   `truffle-prospect-engine/app.py`.
3. In the app's **Settings → Secrets**, paste the same keys as `.env`:
   ```toml
   GOOGLE_PLACES_API_KEY = "..."
   NEWSAPI_KEY = "..."
   ```
4. Deploy. The SQLite db is ephemeral on Community Cloud (resets on redeploy) —
   click **Run / refresh enrichment** once after each deploy.

## Data sources, what's live, and what isn't

| Signal | Source | Status | Free tier limit |
|---|---|---|---|
| Location count | Google Places API (New) — Text Search, filtered to food-category places only | ✅ Live, all 10 groups | $200/month credit; comfortably covers demo-scale usage |
| Average rating | Google Places API (New) — Text Search | ✅ Live | Same as above |
| Negative review density | Google Places API (New) — Place Details `reviews` field, gated to reviews the reviewer themself rated ≤3 stars | ✅ Live | Needs a Cloud Billing account linked (done) — the `reviews` field lives under Google's "Enterprise + Atmosphere" SKU |
| Active ops/kitchen-manager hiring | Greenhouse + Lever public job board APIs, with a manual-entry fallback in the UI | ✅ Live, but sparse (see below) | Free, no key required |
| Recent expansion/press signal | NewsAPI.org `/v2/everything`, `qInTitle` (company name must appear in the headline, not just the body) | ✅ Live | Free "Developer" tier: 100 requests/day, articles from roughly the last 30 days only, intended for dev/testing rather than a paid production product — appropriate for this demo |
| Greenfield / no named vendor | Scanned from job posting text + press headlines already fetched above | Live whenever the above two return anything | No separate API cost |

### Accuracy fixes made after the first pass

Three real problems surfaced during testing and got fixed rather than shipped:

1. **Generic brand names matched unrelated businesses.** Querying "Lafayette
   New York" for NoHo Hospitality Group returned a department store in France
   and a clothing label alongside the actual restaurant. Fixed by requiring
   Google's own place-category data (`types`) to include a food/drink category
   before counting a result — see `_is_food_business()` in
   `src/enrichment/google_places.py`. Also excludes permanently-closed
   locations (caught one: NYCRG's "Viva Cucina" is closed per Google).
2. **Keyword-matched reviews weren't actually complaints.** The negative-review
   scanner was flagging 5-star raves like *"I waited all winter for this
   dish"* just because they contained "waited." Fixed by requiring the
   reviewer's own star rating to be ≤3 before a keyword hit counts — see
   `NEGATIVE_REVIEW_MAX_RATING` in `google_places.py`.
3. **Press matches included irrelevant articles.** NewsAPI's body-text search
   surfaced a Miami hotel-renovation story that mentioned "Major Food Group"
   once, in passing — technically a real match, misleading as a "why now"
   trigger. Fixed by requiring the company name to appear in the article's
   own **headline** (`qInTitle`), not just somewhere in the body.

### Location concept lists — now verified for all 10 groups

Google Places Text Search returns the single *best-relevance* match for a
compound query, not an enumeration of every property tied to a group, so an
accurate count requires querying each known concept/brand name separately.
All 10 seed groups now have a verified concept list in `src/config.py`:

- 6 came directly from the original seed research (Major Food Group,
  Unapologetic Foods, Boucherie, NYCRG, Golden Age, and originally-unlisted
  concepts).
- The remaining 4 (USHG, NoHo Hospitality, HAND Hospitality, City Roots) were
  filled in by fetching each group's own website directly (ushg.com,
  nhgnyc.com, handhospitality.com, cityrootsnyc.com) — not guessed from
  memory. One judgment call: NoHo Hospitality's San Morello, Evening Bar, The
  Brakeman, and Penny Red's were excluded as Shinola Hotel Detroit concepts,
  keeping the count NYC-only.

If Truffle asks "where did this number come from," every concept name in
`location_queries` is traceable to either the original brief or a cited
source page — nothing was invented.

## Scoring engine

Weighted 0-100 score, weights defined as constants at the top of
[`src/scoring.py`](src/scoring.py) and [`src/config.py`](src/config.py) so
they're easy to tune and defend:

| Signal | Weight | Logic |
|---|---|---|
| Location count | 25% | 0-1 locs = 5pts, 2-3 = 15pts, 4-10 = 25pts (sweet spot), 11+ = 10pts (likely already has enterprise tooling) |
| Active ops hiring | 25% | Any matching open posting = full points, none = 0 |
| Recent expansion/press | 25% | Full points within 30 days, linear decay to 0 by 180 days |
| Negative review density | 15% | % of sampled reviews flagging stockouts/wait times/wrong orders, scaled to the weight |
| No named POS/inventory vendor | 10% | Greenfield (no vendor mentioned in job posts or press) = full points |

A score of 0 in any row means **either** the signal genuinely didn't fire
**or** the underlying data was unavailable — the per-signal source badge in
the UI (🟢 live data / 🟡 estimate / ⚪ unavailable) is what tells you which.
Don't read a low total score as "confirmed low fit" without checking how
many rows are actually ⚪.

### Location growth tracking (informational, not yet scored)

Every enrichment run logs a timestamped snapshot of each prospect's location
count to a `history` table. Once the pipeline has run more than once on
different days, the dashboard shows a "grew from X to Y locations between
[date] and [date]" caption under any prospect where the count actually
changed. This is free (derived from data already collected) and arguably a
stronger expansion signal than a single press mention — it's not yet part of
the weighted score, since folding it in would mean rebalancing the existing
100-point rubric, which is worth a deliberate decision rather than a silent
change.

## What's simplified for this demo

Nothing is *faked* — but a few things are simplified because a 2-week
part-time demo isn't the place to build a production data pipeline:

1. **Hiring signal has near-zero coverage via live API** for this seed list,
   because hospitality groups rarely use Greenhouse/Lever. The dashboard has
   a manual-entry fallback (expand "Add hiring signal manually" on any
   prospect showing "data unavailable") — always tagged ✏️ manual input,
   never presented as live data, and it survives future "Run / refresh
   enrichment" clicks rather than getting overwritten back to unavailable.
2. **Vendor/greenfield detection** only scans text already pulled from job
   postings and press headlines — it's a real keyword scan against a fixed
   list of known BOH/POS vendor names (`KNOWN_VENDOR_KEYWORDS` in
   `config.py`), not an exhaustive vendor-detection system.
3. **Review sampling is capped at 5 most-recent reviews per location** (a
   Google Places API limit) — the negative-review signal reflects a small,
   Google-selected sample, not the full review history.

## Scaling beyond this demo

To go from 10 NYC prospects to hundreds across multiple cities:

- **Concept/brand mapping**: the biggest accuracy lever. Either manually
  research each group's concept list (as done for 4 of the 10 here) or find
  a data provider that maps parent companies to locations directly (e.g. a
  paid business-data API) instead of relying on Places Text Search guesses.
- **NewsAPI**: the free tier's 30-day lookback and request cap won't hold at
  scale — move to a paid plan or a different news API.
- **Jobs signal**: Greenhouse/Lever coverage is too sparse for hospitality.
  A paid jobs aggregator (e.g. a licensed Indeed/LinkedIn data feed) would be
  needed for this signal to be reliably useful.
- **Rate limiting & caching**: at hundreds of prospects, add request
  throttling and cache Places/News results for a day or two rather than
  re-querying on every dashboard refresh.
- **Storage**: SQLite is fine through the low hundreds of prospects; move to
  Postgres if this becomes a persistent multi-user tool rather than a
  single-analyst demo.
- **Multi-city**: the seed list, `location_queries`, and city name are the
  only NYC-specific pieces — add a `city` field to each prospect and filter/
  facet the dashboard by it.

## Project structure

```
truffle-prospect-engine/
├── app.py                        # Streamlit dashboard
├── src/
│   ├── config.py                 # Seed list + all scoring weights (tune here)
│   ├── db.py                     # SQLite schema + read/write helpers
│   ├── scoring.py                # Weighted scoring engine + "why now" generator
│   ├── pipeline.py                # Ties enrichment + scoring together
│   └── enrichment/
│       ├── google_places.py      # Location count, ratings, reviews
│       ├── jobs_search.py        # Greenhouse/Lever hiring signal
│       └── news_search.py        # NewsAPI expansion signal
├── data/prospects.db             # SQLite db (gitignored, regenerated on each run)
└── .env                          # API keys (gitignored, never committed)
```
