"""Marktplaats (and its Belgian twin 2dehands) via the JSON search endpoint the website itself uses.

Note: Marktplaats' terms don't allow automated collection. This runs once a day with
pauses between requests, for personal use. If they block it, see README for alternatives.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from .http import SourceError, get, json_ld_objects, meta_content, polite_sleep, session, strip_html

log = logging.getLogger(__name__)

PRICE_TYPES_WITH_PRICE = {"FIXED", "MIN_BID", "FAST_BID", "NEGOTIABLE"}


def fetch(cfg: dict, home: dict, source: str, known: dict[str, dict]) -> list[dict]:
    s = session()
    host = cfg["host"].rstrip("/")
    results: dict[str, dict] = {}

    for q in cfg.get("queries", ["urban arrow"]):
        offset = 0
        for _ in range(cfg.get("max_pages_per_query", 3)):
            params = {
                "query": q,
                "limit": 100,
                "offset": offset,
                "searchInTitleAndDescription": "true",
                "sortBy": "SORT_INDEX",
                "sortOrder": "DECREASING",
                "viewOptions": "list-view",
            }
            if cfg.get("category_l1"):
                params["l1CategoryId"] = cfg["category_l1"]
            if home.get("postcode") and source == "marktplaats":
                params["postcode"] = home["postcode"]
            r = get(s, f"{host}/lrp/api/search?{urlencode(params)}",
                    headers={"Accept": "application/json"})
            data = r.json()
            page = data.get("listings") or []
            for raw in page:
                item = normalize(raw, host, source)
                if item:
                    results[item["id"]] = item
            total = data.get("totalResultCount") or 0
            offset += 100
            log.info("%s %r: %d listings (offset %d of %d)", source, q, len(page), offset, total)
            if not page or offset >= total:
                break
            polite_sleep(cfg.get("delay_seconds"))
        polite_sleep(cfg.get("delay_seconds"))

    if not results:
        raise SourceError(f"{source}: search returned nothing (blocked or API changed?)")

    if cfg.get("fetch_details", True):
        _fetch_details(s, cfg, results, known)
    return list(results.values())


def normalize(raw: dict, host: str, source: str) -> dict | None:
    item_id = raw.get("itemId")
    if not item_id:
        return None
    price = raw.get("priceInfo") or {}
    ptype = price.get("priceType") or "UNKNOWN"
    cents = price.get("priceCents")
    loc = raw.get("location") or {}
    seller = raw.get("sellerInformation") or {}
    attrs = {a.get("key"): a.get("value") for a in raw.get("attributes") or [] if a.get("key")}
    for a in raw.get("extendedAttributes") or []:
        if a.get("key") and a.get("key") not in attrs:
            attrs[a["key"]] = a.get("value")

    pics = raw.get("pictures") or []
    image = None
    if pics:
        image = pics[0].get("mediumUrl") or pics[0].get("largeUrl") or pics[0].get("extraSmallUrl")
    elif raw.get("imageUrls"):
        image = raw["imageUrls"][0]
    if image and image.startswith("//"):
        image = "https:" + image

    vip = raw.get("vipUrl") or ""
    dist = loc.get("distanceMeters")
    condition = attrs.get("condition") or ""
    return {
        "id": f"{source}:{item_id}",
        "source": source,
        "url": host + vip if vip.startswith("/") else vip,
        "title": raw.get("title") or "",
        "description": strip_html(raw.get("description") or ""),
        "price_eur": round(cents / 100) if cents and ptype in PRICE_TYPES_WITH_PRICE else None,
        "price_type": ptype,
        "condition_raw": condition,
        "kind": "new" if condition.lower() == "nieuw" else "used",
        "seller_has_website": bool(seller.get("showWebsiteUrl")),
        "seller_verified": bool(seller.get("isVerified")),
        "city": loc.get("cityName"),
        "country": loc.get("countryAbbreviation") or loc.get("countryName"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "distance_km": round(dist / 1000, 1) if isinstance(dist, (int, float)) and dist > 0 else None,
        "posted_at": raw.get("date"),
        "image": image,
    }


def _fetch_details(s, cfg: dict, results: dict[str, dict], known: dict[str, dict]) -> None:
    """Search results only carry a short snippet. Open each listing once for the full text."""
    budget = cfg.get("max_detail_fetches_per_run", 50)
    for lid, item in results.items():
        prev = known.get(lid)
        if prev and prev.get("full_description"):
            item["description"] = prev["description"]
            item["full_description"] = True
            continue
        if budget <= 0 or not item.get("url"):
            continue
        budget -= 1
        polite_sleep(cfg.get("delay_seconds"))
        try:
            html = get(s, item["url"]).text
        except SourceError as e:
            log.warning("detail fetch failed: %s", e)
            continue
        desc = None
        for obj in json_ld_objects(html):
            if obj.get("description") and len(obj["description"]) > len(desc or ""):
                desc = obj["description"]
        desc = desc or meta_content(html, "og:description")
        if desc and len(desc) >= len(item.get("description") or ""):
            item["description"] = strip_html(desc)
            item["full_description"] = True
