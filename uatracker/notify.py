"""Daily Telegram message with new good deals and price drops (optional)."""
from __future__ import annotations

import logging
import os
from html import escape

import requests

from .store import today

log = logging.getLogger(__name__)


def _line(l: dict) -> str:
    a = l.get("attrs") or {}
    bits = [str(a["model_year"]) if a.get("model_year") else None,
            a.get("variant") if a.get("variant") not in (None, "unknown") else None,
            f"{a['battery_wh']}Wh" if a.get("battery_wh") else None,
            f"{a['mileage_km']:,} km".replace(",", ".") if a.get("mileage_km") else None,
            f"{l['distance_km']:.0f} km away" if l.get("distance_km") is not None else l.get("city")]
    deal = l.get("deal") or {}
    fair = (l.get("fair") or {}).get("price")
    head = f"<b>€{l['price_eur']:,}</b>".replace(",", ".")
    if fair:
        head += f" (fair ≈ €{fair:,.0f}, {deal.get('pct', 0):+.0f}%)".replace(",", ".")
    flags = "".join(f"\n⚠️ {escape(f)}" for f in deal.get("flags") or [])
    return (f"{head}\n{escape(l.get('title', ''))}\n{escape(' · '.join(b for b in bits if b))}"
            f"\n<a href=\"{escape(l['url'])}\">Open listing</a>{flags}")


def pick(listings: dict[str, dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    t = today()
    min_pct = cfg.get("min_deal_pct", 10)
    max_km = cfg.get("max_distance_km", 9999)

    def ok(l):
        d = l.get("deal") or {}
        dist = l.get("distance_km")
        return (l.get("active") and l.get("price_eur") and d.get("pct") is not None
                and d["pct"] >= min_pct and (dist is None or dist <= max_km))

    new = [l for l in listings.values() if ok(l) and l.get("first_seen") == t and not l.get("relist_of")]
    drops = []
    if cfg.get("notify_price_drops", True):
        for l in listings.values():
            h = l.get("price_history") or []
            if ok(l) and len(h) >= 2 and h[-1][0] == t and h[-2][1] and h[-1][1] and h[-1][1] < h[-2][1] \
                    and l.get("first_seen") != t:
                drops.append(l)
    key = lambda l: -(l["deal"]["pct"])
    return sorted(new, key=key), sorted(drops, key=key)


def send(listings: dict[str, dict], cfg: dict, site_url: str | None = None) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    new, drops = pick(listings, cfg)
    log.info("alerts: %d new deals, %d price drops", len(new), len(drops))
    if not (token and chat) or not (new or drops):
        return
    parts = []
    if new:
        parts.append("🚲 <b>New Urban Arrow deals</b>\n\n" + "\n\n".join(_line(l) for l in new[:8]))
    if drops:
        parts.append("📉 <b>Price drops</b>\n\n" + "\n\n".join(_line(l) for l in drops[:5]))
    if site_url:
        parts.append(f'<a href="{escape(site_url)}">All listings</a>')
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
            "chat_id": chat, "text": "\n\n".join(parts), "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if r.status_code != 200:
            log.warning("telegram: HTTP %s %s", r.status_code, r.text[:300])
    except requests.RequestException as e:
        log.warning("telegram failed: %s", e)
