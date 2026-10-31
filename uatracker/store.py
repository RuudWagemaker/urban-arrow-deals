"""Listing storage: one JSON file with every listing ever seen, plus its price history.

JSON (not SQLite) on purpose: the daily job commits it to git, and text diffs stay small.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .config import DATA_DIR

LISTINGS_FILE = "listings.json"


def today() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(data_dir: Path = DATA_DIR) -> dict[str, dict]:
    p = Path(data_dir) / LISTINGS_FILE
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return {item["id"]: item for item in json.load(f)}


def save(listings: dict[str, dict], data_dir: Path = DATA_DIR) -> None:
    p = Path(data_dir) / LISTINGS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(listings.values(), key=lambda x: x["id"])
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1, sort_keys=True)
    tmp.replace(p)


def save_report(report: dict, data_dir: Path = DATA_DIR) -> None:
    """Small summary of the last run (what each search returned), committed with the data."""
    p = Path(data_dir) / "last_run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def merge(listings: dict[str, dict], scraped: list[dict], sources_ok: set[str]) -> dict:
    """Merge today's scrape into the store.

    - new listings get first_seen; known ones get last_seen and price changes appended
    - listings from a source that scraped OK but no longer appear are marked inactive
      (if a source failed today we leave its listings alone)
    - a "new" listing that looks like a relist of a recently removed one inherits its history
    Returns counts for logging.
    """
    t = today()
    seen = set()
    stats = {"new": 0, "updated": 0, "price_changes": 0, "removed": 0, "relists": 0}

    for s in scraped:
        lid = s["id"]
        seen.add(lid)
        cur = listings.get(lid)
        if cur is None:
            cur = dict(s)
            cur["first_seen"] = t
            cur["new_ad_on"] = t            # date this ad appeared (also for relists)
            cur["new_ad_at"] = now_iso()
            cur["price_history"] = [[t, s.get("price_eur")]]
            relist = _find_relist(listings, s)
            if relist:
                cur["relist_of"] = relist["id"]
                cur["first_seen"] = relist.get("first_seen", t)
                cur["price_history"] = relist.get("price_history", []) + cur["price_history"]
                relist["superseded_by"] = lid
                stats["relists"] += 1
            listings[lid] = cur
            stats["new"] += 1
        else:
            old_price = cur.get("price_eur")
            for k, v in s.items():
                if k in ("description",) and not v and cur.get(k):
                    continue  # keep the longer description fetched earlier
                cur[k] = v
            if s.get("price_eur") != old_price:
                cur.setdefault("price_history", []).append([t, s.get("price_eur")])
                stats["price_changes"] += 1
            stats["updated"] += 1
        cur["last_seen"] = t
        cur["active"] = True
        cur.pop("removed_on", None)

    for lid, cur in listings.items():
        if cur.get("active") and lid not in seen and cur.get("source") in sources_ok:
            cur["active"] = False
            cur["removed_on"] = t
            stats["removed"] += 1
    return stats


def _find_relist(listings: dict[str, dict], s: dict) -> dict | None:
    title = _norm_title(s.get("title", ""))
    if not title:
        return None
    for cur in listings.values():
        if cur.get("active") or cur.get("superseded_by") or cur.get("source") != s.get("source"):
            continue
        if _norm_title(cur.get("title", "")) != title or cur.get("city") != s.get("city"):
            continue
        removed = cur.get("removed_on")
        if removed and (date.fromisoformat(today()) - date.fromisoformat(removed)).days <= 45:
            return cur
    return None
