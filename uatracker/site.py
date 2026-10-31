"""Build the static website: web/ files + one data.json."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import SITE_DIR, WEB_DIR
from .store import now_iso, today

PUBLIC_FIELDS = [
    "id", "source", "shop", "url", "title", "price_eur", "price_type", "kind", "city", "country",
    "distance_km", "image", "first_seen", "last_seen", "active", "removed_on", "price_history",
    "attrs", "fair", "deal", "new_deal", "variant_group", "in_stock", "relist_of", "new_ad_on", "new_ad_at",
]


def _public(l: dict) -> dict:
    out = {k: l[k] for k in PUBLIC_FIELDS if k in l}
    a = dict(out.get("attrs") or {})
    out["attrs"] = a
    return out


def build(listings: dict[str, dict], model_info: dict, config: dict, site_dir: Path = SITE_DIR) -> Path:
    site_dir = Path(site_dir)
    if site_dir.exists():
        shutil.rmtree(site_dir)
    shutil.copytree(WEB_DIR, site_dir)

    bikes = [l for l in listings.values() if (l.get("attrs") or {}).get("is_complete_bike")
             and not l.get("superseded_by")]
    used = [_public(l) for l in bikes if "fair" in l]
    new = [_public(l) for l in bikes if "new_deal" in l]
    data = {
        "generated_at": now_iso(),
        "today": today(),
        "tracking_since": min((l.get("first_seen") for l in listings.values() if l.get("first_seen")), default=today()),
        "home": config.get("home", {}),
        "default_max_distance_km": config.get("home", {}).get("max_distance_km", 250),
        "msrp_estimate": config.get("msrp_estimate", {}),
        "model": model_info,
        "used": used,
        "new": new,
    }
    with open(site_dir / "data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return site_dir
