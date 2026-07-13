"""
SQLite storage for prospects, their raw enrichment signals, and computed scores.

Three tables:
  prospects  - static identity info from the seed list
  enrichment - raw signal values pulled from each data source, plus where each came from
  scores     - computed weighted score + breakdown + the "why now" one-liner

`source` fields are always one of: "google_places_api", "greenhouse_api", "lever_api",
"newsapi", "manual_csv", or "unavailable" — so the UI can be honest about what's real
API data vs. manually entered vs. not available at all.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from src.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    short_name TEXT,
    notes TEXT,
    search_query TEXT
);

CREATE TABLE IF NOT EXISTS enrichment (
    prospect_id INTEGER PRIMARY KEY REFERENCES prospects(id),

    location_count INTEGER,
    location_count_source TEXT DEFAULT 'unavailable',
    location_names TEXT,               -- JSON list of matched place names, for transparency

    avg_rating REAL,
    reviews_sampled INTEGER,
    negative_review_hits INTEGER,
    negative_review_examples TEXT,     -- JSON list of matched review snippets
    review_source TEXT DEFAULT 'unavailable',

    hiring_signal_found INTEGER,       -- 0/1
    hiring_titles TEXT,                -- JSON list of matched job titles
    hiring_source TEXT DEFAULT 'unavailable',

    expansion_signal_found INTEGER,    -- 0/1
    expansion_headline TEXT,
    expansion_published_at TEXT,
    expansion_source TEXT DEFAULT 'unavailable',

    vendor_named INTEGER,              -- 0/1, 1 = a named POS/inventory vendor was found
    vendor_name TEXT,
    vendor_source TEXT DEFAULT 'unavailable',

    enriched_at TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    prospect_id INTEGER PRIMARY KEY REFERENCES prospects(id),
    total_score REAL,
    location_score REAL,
    hiring_score REAL,
    expansion_score REAL,
    review_score REAL,
    vendor_score REAL,
    why_now TEXT,
    computed_at TEXT
);

-- Append-only log, one row per enrichment run per prospect. Lets the
-- dashboard show location-count growth over time once at least two runs
-- exist — a real signal (no new API calls) once this pipeline has run more
-- than once. See db.log_snapshot() / db.get_location_growth().
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER REFERENCES prospects(id),
    location_count INTEGER,
    total_score REAL,
    recorded_at TEXT
);

-- Individual restaurants, at a finer grain than the parent group.
-- prospect_id set  -> one of the 10 seed groups' own locations (e.g. one of
--                     Major Food Group's 6 restaurants), scored on its own
--                     reviews so a single strained location surfaces even if
--                     the group overall looks low-priority.
-- prospect_id NULL -> an independently-discovered standalone restaurant, not
--                     part of any seed group. See src/discovery.py.
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER REFERENCES prospects(id),
    place_id TEXT UNIQUE NOT NULL,
    name TEXT,
    rating REAL,
    reviews_sampled INTEGER,
    negative_review_hits INTEGER,
    negative_review_examples TEXT,     -- JSON list
    pain_score REAL,                   -- 0-100, review-density based only — see scoring.score_location_pain
    discovered_via TEXT,               -- e.g. a NewsAPI headline, for standalone discoveries
    source TEXT DEFAULT 'unavailable', -- 'google_places_api' or 'unavailable'
    last_seen_at TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_prospect(name: str, short_name: str, notes: str, search_query: str) -> int:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO prospects (name, short_name, notes, search_query)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET short_name=excluded.short_name,
                                                notes=excluded.notes,
                                                search_query=excluded.search_query""",
            (name, short_name, notes, search_query),
        )
        row = conn.execute("SELECT id FROM prospects WHERE name = ?", (name,)).fetchone()
        return row["id"]


def save_enrichment(prospect_id: int, data: dict):
    data = dict(data)
    data["prospect_id"] = prospect_id
    data["enriched_at"] = datetime.now(timezone.utc).isoformat()
    for key in ("location_names", "negative_review_examples", "hiring_titles"):
        if key in data and not isinstance(data[key], str):
            data[key] = json.dumps(data[key])

    columns = list(data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_clause = ", ".join([f"{c}=excluded.{c}" for c in columns if c != "prospect_id"])
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO enrichment ({", ".join(columns)}) VALUES ({placeholders})
                ON CONFLICT(prospect_id) DO UPDATE SET {update_clause}""",
            [data[c] for c in columns],
        )


def save_score(prospect_id: int, data: dict):
    data = dict(data)
    data["prospect_id"] = prospect_id
    data["computed_at"] = datetime.now(timezone.utc).isoformat()
    columns = list(data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_clause = ", ".join([f"{c}=excluded.{c}" for c in columns if c != "prospect_id"])
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO scores ({", ".join(columns)}) VALUES ({placeholders})
                ON CONFLICT(prospect_id) DO UPDATE SET {update_clause}""",
            [data[c] for c in columns],
        )


def log_snapshot(prospect_id: int, location_count: int | None, total_score: float | None):
    """Append one row to the history log — called once per prospect per pipeline run."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO history (prospect_id, location_count, total_score, recorded_at) VALUES (?, ?, ?, ?)",
            (prospect_id, location_count, total_score, datetime.now(timezone.utc).isoformat()),
        )


def get_location_growth(prospect_id: int) -> dict | None:
    """
    Compares the earliest and latest logged location_count for a prospect.
    Returns None until at least two runs with a known (non-null) location_count
    exist — a single data point can't show growth, and reporting one would
    imply we know more than we do.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT location_count, recorded_at FROM history
               WHERE prospect_id = ? AND location_count IS NOT NULL
               ORDER BY recorded_at ASC""",
            (prospect_id,),
        ).fetchall()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    if first["location_count"] == last["location_count"]:
        return None
    return {
        "first_count": first["location_count"],
        "first_date": first["recorded_at"],
        "latest_count": last["location_count"],
        "latest_date": last["recorded_at"],
        "delta": last["location_count"] - first["location_count"],
    }


def save_location(prospect_id: int | None, location: dict):
    """
    Upsert one individual restaurant's own review-based data, keyed on its
    Google place_id. prospect_id=None for a standalone (non-group) discovery.
    """
    data = dict(location)
    data["prospect_id"] = prospect_id
    data["last_seen_at"] = datetime.now(timezone.utc).isoformat()
    if not isinstance(data.get("negative_review_examples"), str):
        data["negative_review_examples"] = json.dumps(data.get("negative_review_examples") or [])

    columns = list(data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_clause = ", ".join([f"{c}=excluded.{c}" for c in columns if c != "place_id"])
    with get_conn() as conn:
        conn.execute(
            f"""INSERT INTO locations ({", ".join(columns)}) VALUES ({placeholders})
                ON CONFLICT(place_id) DO UPDATE SET {update_clause}""",
            [data[c] for c in columns],
        )


def get_locations_for_prospect(prospect_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM locations WHERE prospect_id = ? ORDER BY pain_score DESC NULLS LAST",
            (prospect_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_standalone_locations() -> list[dict]:
    """Independently-discovered restaurants not tied to any of the 10 seed groups."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM locations WHERE prospect_id IS NULL ORDER BY pain_score DESC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]


def get_known_group_place_ids() -> set[str]:
    """place_ids already tracked under one of the 10 seed groups — used to
    dedupe independent discovery so a group's own restaurant can't also show
    up as a separate 'independent' find."""
    with get_conn() as conn:
        rows = conn.execute("SELECT place_id FROM locations WHERE prospect_id IS NOT NULL").fetchall()
        return {r["place_id"] for r in rows}


def get_all_prospects_with_scores() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, e.*, s.*
               FROM prospects p
               LEFT JOIN enrichment e ON e.prospect_id = p.id
               LEFT JOIN scores s ON s.prospect_id = p.id
               ORDER BY s.total_score DESC NULLS LAST"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_prospect_detail(prospect_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT p.*, e.*, s.*
               FROM prospects p
               LEFT JOIN enrichment e ON e.prospect_id = p.id
               LEFT JOIN scores s ON s.prospect_id = p.id
               WHERE p.id = ?""",
            (prospect_id,),
        ).fetchone()
        return dict(row) if row else None


def save_manual_hiring_override(prospect_id: int, hiring_signal_found: int, hiring_titles: list[str]):
    """
    User-entered fallback for the hiring signal, used when Greenhouse/Lever
    found no board (most hospitality groups don't have one). Always tagged
    hiring_source='manual_csv' so the UI shows it as manual input, never live
    API data. Left in place across re-enrichment runs unless the user clears it
    or a live board is later found — see pipeline.enrich_and_score_prospect.
    """
    save_enrichment(prospect_id, {
        "hiring_signal_found": hiring_signal_found,
        "hiring_titles": hiring_titles,
        "hiring_source": "manual_csv",
    })


def clear_manual_hiring_override(prospect_id: int):
    save_enrichment(prospect_id, {
        "hiring_signal_found": None,
        "hiring_titles": [],
        "hiring_source": "unavailable",
    })
