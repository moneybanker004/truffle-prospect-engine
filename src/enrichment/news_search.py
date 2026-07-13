"""
Expansion/press signal via NewsAPI.org's /v2/everything endpoint.

Known limitation of NewsAPI's free "Developer" tier (real, not hidden):
  - Only returns articles from roughly the last 30 days.
  - Free tier is intended for development/testing, not a paid production product.
    That fits this project (a demo, not a monetized service), but if Truffle
    wants this signal running indefinitely in production, it needs a paid
    NewsAPI plan or a swap to a different news API.

If NEWSAPI_KEY is unset or the call fails, the signal is reported as
source="unavailable" — never a guessed headline or date.

Query design note: an earlier version searched article BODY text for the
company name plus expansion keywords (NewsAPI's `q` param). That surfaced
real but misleading matches — e.g. a Miami hotel-renovation story that
mentioned "Major Food Group" once, in passing, as an existing restaurant
tenant, with a headline about something else entirely. Requiring the company
name to appear in the article's own HEADLINE (`qInTitle`) is a much stronger
guarantee that the story shown is actually about them — at the cost of fewer
matches, which is the right tradeoff for a signal used to justify a sales call.
"""

from datetime import datetime, timezone

import requests

NEWSAPI_URL = "https://newsapi.org/v2/everything"


def enrich_expansion_signal(company_name: str, api_key: str) -> dict:
    empty = {
        "expansion_signal_found": None,
        "expansion_headline": None,
        "expansion_published_at": None,
        "expansion_source": "unavailable",
    }
    if not api_key:
        return empty

    params = {
        "qInTitle": f'"{company_name}"',
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 5,
        "apiKey": api_key,
    }
    try:
        resp = requests.get(NEWSAPI_URL, params=params, timeout=15)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
    except requests.RequestException:
        return empty

    if not articles:
        return {
            "expansion_signal_found": 0,
            "expansion_headline": None,
            "expansion_published_at": None,
            "expansion_source": "newsapi",
        }

    top = articles[0]
    return {
        "expansion_signal_found": 1,
        "expansion_headline": top.get("title"),
        "expansion_published_at": top.get("publishedAt"),
        "expansion_source": "newsapi",
    }


def days_since(iso_timestamp: str | None) -> float | None:
    if not iso_timestamp:
        return None
    try:
        published = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - published).total_seconds() / 86400
    except ValueError:
        return None
