"""
wiki_utils.py — shared helpers for the wikipedia-trends skill.

Talks to two public, keyless APIs:
  - Wikimedia REST "Pageviews" analytics API (per-article and aggregate)
    https://wikimedia.org/api/rest_v1/metrics/pageviews/
  - Wikidata API (wbsearchentities / wbgetentities), used to resolve a
    topic typed in any language to the exact article title used on each
    requested language Wikipedia (titles are NOT simple translations —
    e.g. "intermittent fasting" is "Post przerywany" on pl.wikipedia).

Design notes for reliability on cheap/fast models:
  - Every public function returns plain JSON-able dicts/lists, never
    raises on "expected" conditions (missing data, unknown article) —
    it returns a structured {"error": ...} instead, so a calling script
    can decide what to do without needing to parse tracebacks.
  - All HTTP calls go through `_get()`, which retries transient errors,
    enforces the required descriptive User-Agent (Wikimedia blocks
    generic/absent User-Agents), and caches responses to disk so that
    repeated or follow-up questions in the same session don't re-hit
    the network for data already fetched.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Wikimedia asks every API consumer to identify itself. Replace the contact
# info if you fork this for your own deployment — see
# https://meta.wikimedia.org/wiki/User-Agent_policy
USER_AGENT = (
    "wikipedia-trends-skill/1.0 "
    "(https://github.com/anthropic/agent-skills; contact: skill-user@example.com) "
    "python-requests"
)

PAGEVIEWS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Wikimedia pageviews API only has reliable per-article data from this month.
DATA_AVAILABLE_FROM = "2015-07"


def _cache_path(key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return os.path.join(CACHE_DIR, f"{h}.json")


def _get(url: str, params: Optional[dict] = None, cache_ttl_seconds: int = 24 * 3600,
          retries: int = 3, timeout: int = 20) -> dict:
    """GET a JSON endpoint with disk caching and retry-on-transient-error.

    Returns the parsed JSON body on success, or a dict of the shape
    {"error": "...", "status_code": int|None} on failure — callers should
    check for the "error" key rather than relying on exceptions.
    """
    cache_key = url + "?" + json.dumps(params or {}, sort_keys=True)
    cpath = _cache_path(cache_key)
    if os.path.exists(cpath) and (time.time() - os.path.getmtime(cpath)) < cache_ttl_seconds:
        with open(cpath, "r", encoding="utf-8") as f:
            return json.load(f)

    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if resp.status_code == 404:
                # Wikimedia returns 404 for "no data in this range" — that is
                # meaningful information, not a failure, so cache it too.
                result = {"error": "not_found", "status_code": 404}
                with open(cpath, "w", encoding="utf-8") as f:
                    json.dump(result, f)
                return result
            resp.raise_for_status()
            data = resp.json()
            with open(cpath, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return {"error": last_error or "unknown_error", "status_code": None}


def normalize_date(date_str: str) -> str:
    """Normalize a date the caller may give as YYYY-MM-DD, YYYY-MM, or
    already YYYYMMDD[HH] into the YYYYMMDD form the Wikimedia API needs.
    Missing day defaults to '01' (a valid calendar day) — never '00',
    which the API rejects."""
    digits = date_str.replace("-", "").strip()
    if not digits.isdigit():
        raise ValueError(f"Unrecognized date format: {date_str!r} (use YYYY-MM-DD or YYYY-MM)")
    if len(digits) >= 8:
        return digits[:8]
    if len(digits) == 6:  # YYYYMM
        return digits + "01"
    if len(digits) == 4:  # YYYY
        return digits + "0101"
    raise ValueError(f"Unrecognized date format: {date_str!r} (use YYYY-MM-DD or YYYY-MM)")


def normalize_project(lang_or_project: str) -> str:
    """Accept either a bare language code ('pl') or a full project id
    ('pl.wikipedia') and return the full project id Wikimedia expects."""
    lang_or_project = lang_or_project.strip().lower()
    if ".wikipedia" in lang_or_project or ".m.wikipedia" in lang_or_project:
        return lang_or_project
    return f"{lang_or_project}.wikipedia"


def fetch_per_article(project: str, article_title: str, start: str, end: str,
                       granularity: str = "monthly", access: str = "all-access",
                       agent: str = "user") -> dict:
    """Fetch a pageviews time series for one article on one project.

    start/end format: YYYYMMDD (daily) — the function also accepts
    YYYY-MM-DD / YYYY-MM and normalizes them.
    agent='user' (default) excludes bots/spiders, which is what you want
    when the question is "are humans getting more interested in X".
    """
    project = normalize_project(project)
    start_n = normalize_date(start)
    end_n = normalize_date(end)
    # per-article article titles must use underscores, matching wiki URL form
    article = article_title.strip().replace(" ", "_")
    url = f"{PAGEVIEWS_BASE}/per-article/{project}/{access}/{agent}/{requests.utils.quote(article, safe='')}/{granularity}/{start_n}/{end_n}"
    data = _get(url)
    if "error" in data:
        return {"project": project, "article": article_title, "error": data["error"], "items": []}
    items = data.get("items", [])
    series = [{"timestamp": it["timestamp"], "views": it["views"]} for it in items]
    return {"project": project, "article": article_title, "items": series}


def fetch_aggregate(project: str, start: str, end: str, granularity: str = "monthly",
                     access: str = "all-access", agent: str = "user") -> dict:
    """Fetch total pageviews for an entire wiki (used to normalize an
    article's views into a 'share of all reading on that wiki')."""
    project = normalize_project(project)
    start_n = normalize_date(start)
    end_n = normalize_date(end)
    url = f"{PAGEVIEWS_BASE}/aggregate/{project}/{access}/{agent}/all-agents/{granularity}/{start_n}/{end_n}"
    data = _get(url)
    if "error" in data:
        return {"project": project, "error": data["error"], "items": []}
    items = data.get("items", [])
    series = [{"timestamp": it["timestamp"], "views": it["views"]} for it in items]
    return {"project": project, "items": series}


def wikidata_search(query: str, language: str = "en", limit: int = 5) -> list[dict]:
    """Search Wikidata entities matching a free-text topic. Returns
    candidates with id (QID), label, description — used to disambiguate
    a topic before pulling per-language article titles."""
    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": language,
        "uselang": language,
        "format": "json",
        "limit": limit,
    }
    data = _get(WIKIDATA_API, params=params, cache_ttl_seconds=7 * 24 * 3600)
    if "error" in data:
        return []
    out = []
    for item in data.get("search", []):
        out.append({
            "qid": item.get("id"),
            "label": item.get("label"),
            "description": item.get("description", ""),
        })
    return out


def wikidata_sitelinks(qid: str, langs: list[str]) -> dict:
    """Given a Wikidata QID, return the exact article title used on each
    requested language's Wikipedia (sitelinks), plus which requested
    languages have no article at all (a data point in itself — it means
    the topic has literally never been written up in that language)."""
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "sitelinks|labels|descriptions",
        "format": "json",
    }
    data = _get(WIKIDATA_API, params=params, cache_ttl_seconds=7 * 24 * 3600)
    if "error" in data:
        return {"qid": qid, "error": data["error"], "titles": {}, "missing_langs": langs}
    entity = data.get("entities", {}).get(qid, {})
    sitelinks = entity.get("sitelinks", {})
    titles = {}
    missing = []
    for lang in langs:
        site_key = f"{lang}wiki"
        if site_key in sitelinks:
            titles[lang] = sitelinks[site_key]["title"]
        else:
            missing.append(lang)
    label = entity.get("labels", {}).get("en", {}).get("value", "")
    desc = entity.get("descriptions", {}).get("en", {}).get("value", "")
    return {"qid": qid, "label": label, "description": desc, "titles": titles, "missing_langs": missing}
