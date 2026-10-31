from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("UA_DATA_DIR", ROOT / "data"))
SITE_DIR = Path(os.environ.get("UA_SITE_DIR", ROOT / "site"))
WEB_DIR = ROOT / "web"


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.environ.get("UA_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def products(cfg: dict) -> list[tuple[str, dict]]:
    """[(product_id, product_config)], with shared settings filled in.
    A config without `products` (the original single-product layout) counts as Urban Arrow."""
    items = cfg.get("products")
    if not items:
        legacy = {k: v for k, v in cfg.items() if k not in ("llm", "home", "alerts")}
        legacy.setdefault("name", "Urban Arrow")
        legacy.setdefault("profile", "cargo_bike")
        items = {"urban-arrow": legacy}
    out = []
    for pid, pc in items.items():
        pc = dict(pc or {})
        pc["id"] = pid
        pc["alerts"] = {**(cfg.get("alerts") or {}), **(pc.get("alerts") or {})}
        pc["home"] = {**(cfg.get("home") or {}), "max_distance_km": pc.get("max_distance_km", 250)}
        pc["llm"] = cfg.get("llm") or {}
        pc.setdefault("new_item_pages", pc.get("new_bike_pages") or [])
        out.append((pid, pc))
    return out
