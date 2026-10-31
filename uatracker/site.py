"""Build the static website: web/ files + products.json + one data file per product."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import SITE_DIR, WEB_DIR
from .profiles import is_item
from .store import now_iso, today

PUBLIC_FIELDS = [
    "id", "source", "shop", "url", "title", "price_eur", "price_type", "kind", "city", "country",
    "distance_km", "image", "first_seen", "last_seen", "active", "removed_on", "price_history",
    "attrs", "fair", "deal", "new_deal", "variant_group", "in_stock", "relist_of", "new_ad_on", "new_ad_at",
]


def _public(l: dict) -> dict:
    out = {k: l[k] for k in PUBLIC_FIELDS if k in l}
    out["attrs"] = dict(out.get("attrs") or {})
    return out


def product_data(r: dict) -> dict:
    listings, cfg = r["listings"], r["config"]
    items = [l for l in listings.values() if is_item(l.get("attrs")) and not l.get("superseded_by")]
    return {
        "id": r["id"],
        "name": cfg.get("name", r["id"]),
        "profile": r["profile"].name,
        "generated_at": now_iso(),
        "today": today(),
        "tracking_since": min((l.get("first_seen") for l in listings.values() if l.get("first_seen")), default=today()),
        "home": cfg.get("home", {}),
        "default_max_distance_km": cfg.get("home", {}).get("max_distance_km", 250),
        "msrp_estimate": cfg.get("msrp_estimate", {}),
        "model": r["model"],
        "used": [_public(l) for l in items if "fair" in l],
        "new": [_public(l) for l in items if "new_deal" in l],
    }


def build(results: list[dict], site_dir: Path = SITE_DIR) -> Path:
    site_dir = Path(site_dir)
    if site_dir.exists():
        shutil.rmtree(site_dir)
    shutil.copytree(WEB_DIR, site_dir)
    (site_dir / "data").mkdir()
    index = []
    for r in results:
        d = product_data(r)
        with open(site_dir / "data" / f"{r['id']}.json", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        index.append({
            "id": d["id"], "name": d["name"], "profile": d["profile"],
            "new_today": sum(1 for l in d["used"] + d["new"]
                             if l.get("active") and (l.get("new_ad_on") or l.get("first_seen")) == d["today"]),
        })
    with open(site_dir / "products.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": now_iso(), "products": index}, f, ensure_ascii=False)
    return site_dir
