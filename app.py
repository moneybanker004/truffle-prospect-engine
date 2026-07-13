"""
Truffle Prospect Engine — a scored, ranked list of NYC multi-unit restaurant
groups likely to need Truffle's back-of-house platform right now.

Run: streamlit run app.py
"""

import json

import pandas as pd
import streamlit as st

from src import db
from src.config import SEED_PROSPECTS
from src.pipeline import run_pipeline
from src.scoring import score_prospect

st.set_page_config(page_title="Truffle Prospect Engine", page_icon="🍽️", layout="wide")

SOURCE_LABELS = {
    "google_places_api": "Google Places API (concept-matched)",
    "google_places_api_estimate": "Google Places API (single-query estimate — likely undercounts)",
    "greenhouse_api": "Greenhouse (public board)",
    "lever_api": "Lever (public board)",
    "newsapi": "NewsAPI.org",
    "manual_csv": "Manual input",
    "unavailable": "Data unavailable",
}


def source_badge(source: str) -> str:
    if source == "unavailable" or not source:
        return "⚪ data unavailable"
    if source == "manual_csv":
        return "✏️ manual input"
    if source == "google_places_api_estimate":
        return f"🟡 {SOURCE_LABELS[source]}"
    return f"🟢 {SOURCE_LABELS.get(source, source)}"


def load_json_field(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


@st.cache_data(ttl=3600)
def load_prospects():
    db.init_db()
    return db.get_all_prospects_with_scores()


@st.cache_data(ttl=3600)
def load_standalone_locations():
    db.init_db()
    return db.get_standalone_locations()


def run_enrichment():
    with st.spinner(
        "Enriching all 10 seed prospects (Google Places, job boards, news) "
        "and scanning for independent restaurant discoveries..."
    ):
        run_pipeline(SEED_PROSPECTS, verbose=False)
    st.cache_data.clear()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🍽️ Truffle Prospect Engine")
st.caption("NYC multi-unit restaurant groups, ranked by likelihood of needing back-of-house tooling right now.")

col_run, col_spacer = st.columns([1, 4])
with col_run:
    if st.button("🔄 Run / refresh enrichment", help="Calls Google Places, Greenhouse/Lever, and NewsAPI live"):
        run_enrichment()

prospects = load_prospects()

if not prospects:
    st.info("No prospects scored yet. Click **Run / refresh enrichment** to pull live data for the 10 seed companies.")
    st.stop()

scored = [p for p in prospects if p.get("total_score") is not None]

# ---------------------------------------------------------------------------
# Summary header stats
# ---------------------------------------------------------------------------
if scored:
    avg_score = round(sum(p["total_score"] for p in scored) / len(scored), 1)
    top3 = sorted(scored, key=lambda p: p["total_score"], reverse=True)[:3]

    s1, s2, s3 = st.columns(3)
    s1.metric("Prospects scored", len(scored))
    s2.metric("Average score", f"{avg_score}/100")
    s3.metric("Top prospect", top3[0]["short_name"], f"{top3[0]['total_score']}/100")

    st.markdown("**Top 3 right now:** " + " · ".join(f"**{p['short_name']}** ({p['total_score']})" for p in top3))

st.divider()

# ---------------------------------------------------------------------------
# Ranked table
# ---------------------------------------------------------------------------
df = pd.DataFrame([
    {
        "Rank": i + 1,
        "Group": p["short_name"],
        "Score": p.get("total_score"),
        "Locations": p.get("location_count"),
        "Why now": p.get("why_now") or "Not yet enriched",
        "id": p["id"],
    }
    for i, p in enumerate(sorted(prospects, key=lambda p: (p.get("total_score") is None, -(p.get("total_score") or 0))))
])

st.subheader("Ranked prospects")
st.dataframe(
    df.drop(columns=["id"]),
    width="stretch",
    hide_index=True,
    column_config={
        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
    },
)

csv_bytes = df.drop(columns=["id"]).to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Export CSV", csv_bytes, file_name="truffle_prospects.csv", mime="text/csv")

st.divider()

# ---------------------------------------------------------------------------
# Card view + detail drill-down
# ---------------------------------------------------------------------------
st.subheader("Prospect detail")

names = [p["short_name"] for p in sorted(prospects, key=lambda p: -(p.get("total_score") or 0))]
selected_name = st.selectbox("Select a prospect for the full signal breakdown", names)
selected = next(p for p in prospects if p["short_name"] == selected_name)

left, right = st.columns([2, 1])

with left:
    st.markdown(f"### {selected['name']}")
    st.markdown(f"*{selected.get('notes', '')}*")
    st.markdown(f"**Why now:** {selected.get('why_now') or 'Not yet enriched.'}")

    # Not a scored signal (yet) — informational only, and only shows once two
    # enrichment runs on different days have actually recorded a location-count
    # change. One data point can't show growth, so this stays silent until
    # "Run / refresh enrichment" has been clicked more than once over time.
    growth = db.get_location_growth(selected["id"])
    if growth:
        direction = "📈 grew" if growth["delta"] > 0 else "📉 shrank"
        st.caption(
            f"{direction} from {growth['first_count']} to {growth['latest_count']} locations "
            f"between {growth['first_date'][:10]} and {growth['latest_date'][:10]} "
            "(tracked across enrichment runs, not yet part of the score)"
        )

    st.markdown("#### Signal breakdown")
    signal_rows = [
        ("Location count (25 pts)", selected.get("location_score"), selected.get("location_count_source")),
        ("Active ops hiring (25 pts)", selected.get("hiring_score"), selected.get("hiring_source")),
        ("Recent expansion/press (25 pts)", selected.get("expansion_score"), selected.get("expansion_source")),
        ("Negative review density (15 pts)", selected.get("review_score"), selected.get("review_source")),
        ("Greenfield / no named vendor (10 pts)", selected.get("vendor_score"), selected.get("vendor_source")),
    ]
    for label, points, source in signal_rows:
        c1, c2, c3 = st.columns([2, 1, 2])
        c1.write(label)
        c2.write(f"{points if points is not None else 0} pts")
        c3.write(source_badge(source))

    # Greenhouse/Lever cover almost no hospitality groups, so this is the one
    # signal worth a manual fallback: you (or someone on the team) can check
    # a group's actual careers page / job listings and enter it here. Always
    # tagged "manual input" in the UI — never presented as live API data.
    hiring_source = selected.get("hiring_source")
    if hiring_source in (None, "unavailable", "manual_csv"):
        with st.expander("✏️ Add hiring signal manually" if hiring_source != "manual_csv" else "✏️ Edit manual hiring entry"):
            existing_titles = load_json_field(selected.get("hiring_titles"))
            # A form batches all inputs into a single submit-triggered rerun,
            # rather than each widget re-running the script individually —
            # avoids losing the checkbox/text state on an in-between rerun.
            with st.form(key=f"manual_hiring_form_{selected['id']}"):
                is_hiring = st.checkbox(
                    "Actively hiring for a kitchen manager / inventory / multi-unit ops role?",
                    value=bool(selected.get("hiring_signal_found")),
                )
                titles_text = st.text_input(
                    "Job title(s) found, comma-separated",
                    value=", ".join(existing_titles),
                    placeholder="e.g. Kitchen Manager, Purchasing Coordinator",
                )
                save_col, clear_col = st.columns([1, 1])
                submitted_save = save_col.form_submit_button("Save")
                submitted_clear = clear_col.form_submit_button("Clear", disabled=hiring_source != "manual_csv")

            if submitted_save:
                titles = [t.strip() for t in titles_text.split(",") if t.strip()]
                db.save_manual_hiring_override(selected["id"], 1 if is_hiring else 0, titles)
                updated = db.get_prospect_detail(selected["id"])
                db.save_score(selected["id"], score_prospect(updated))
                st.cache_data.clear()
                st.rerun()
            if submitted_clear and hiring_source == "manual_csv":
                db.clear_manual_hiring_override(selected["id"])
                updated = db.get_prospect_detail(selected["id"])
                db.save_score(selected["id"], score_prospect(updated))
                st.cache_data.clear()
                st.rerun()

with right:
    st.markdown("#### Raw data")
    st.metric("Locations found", selected.get("location_count") or "—")
    st.metric("Avg. Google rating", selected.get("avg_rating") or "—")
    st.metric("Reviews sampled", selected.get("reviews_sampled") or "—")

    location_names = load_json_field(selected.get("location_names"))
    if location_names:
        with st.expander(f"Matched Places results ({len(location_names)})"):
            for n in location_names:
                st.write(f"- {n}")

    hiring_titles = load_json_field(selected.get("hiring_titles"))
    if hiring_titles:
        with st.expander("Matched open postings"):
            for t in hiring_titles:
                st.write(f"- {t}")

    if selected.get("expansion_headline"):
        with st.expander("Press signal"):
            st.write(selected["expansion_headline"])
            st.caption(selected.get("expansion_published_at") or "")

    neg_examples = load_json_field(selected.get("negative_review_examples"))
    if neg_examples:
        with st.expander(f"Flagged review snippets ({len(neg_examples)})"):
            for ex in neg_examples:
                st.write(f"> {ex}")

individual_locations = db.get_locations_for_prospect(selected["id"])
if individual_locations:
    st.divider()
    st.subheader("Individual locations")
    st.caption(
        f"{selected['short_name']}'s own {len(individual_locations)} matched locations, each scored on "
        "its own reviews only (not the 5-signal group score above) — surfaces a single strained "
        "restaurant even inside a group that looks low-priority overall."
    )
    loc_df = pd.DataFrame([
        {
            "Restaurant": loc["name"],
            "Rating": loc.get("rating"),
            "Reviews sampled": loc.get("reviews_sampled"),
            "Review-pain score": loc.get("pain_score"),
        }
        for loc in individual_locations
    ])
    st.dataframe(
        loc_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Review-pain score": st.column_config.ProgressColumn(
                "Review-pain score", min_value=0, max_value=100, format="%.0f"
            ),
        },
    )

# ---------------------------------------------------------------------------
# Independent restaurant discovery (beta) — standalone spots outside the 10
# seed groups, found via Google's own "best new / trending" style rankings.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🔎 Independent restaurant discovery (beta)")
st.caption(
    "Standalone NYC restaurants outside the 10 seed groups, surfaced by Google Places' own ranking "
    "for \"best new / trending\" restaurant queries — not a verified opening-date list (Places has no "
    "such field), but every result below is a real, currently-operating, food-category business. "
    "These tend to be buzzy recent openings under high demand — worth outreach because they're likely "
    "still choosing their ops stack, not because reviews show strain yet (they usually don't, this early)."
)

standalone = load_standalone_locations()
if not standalone:
    st.info("No independent discoveries yet — click **Run / refresh enrichment** above to scan for them.")
else:
    disc_df = pd.DataFrame([
        {
            "Restaurant": loc["name"],
            "Rating": loc.get("rating"),
            "Reviews sampled": loc.get("reviews_sampled"),
            "Review-pain score": loc.get("pain_score"),
            "Discovered via": loc.get("discovered_via"),
        }
        for loc in standalone
    ])
    st.dataframe(
        disc_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Review-pain score": st.column_config.ProgressColumn(
                "Review-pain score", min_value=0, max_value=100, format="%.0f"
            ),
        },
    )
    st.download_button(
        "⬇️ Export discoveries CSV",
        disc_df.to_csv(index=False).encode("utf-8"),
        file_name="truffle_independent_discoveries.csv",
        mime="text/csv",
    )

st.divider()
st.caption(
    "Sources: Google Places API (New), Greenhouse/Lever public job board APIs, NewsAPI.org. "
    "Fields marked 'data unavailable' reflect an API that returned nothing or wasn't configured — "
    "never a fabricated value. See README for source coverage and free-tier limits."
)
