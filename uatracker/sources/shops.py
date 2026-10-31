"""New bikes from shop product pages, read from their schema.org Product data."""
from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

from .http import SourceError, get, json_ld_objects, polite_sleep, session

log = logging.getLogger(__name__)


def _offers(obj: dict) -> list[dict]:
    off = obj.get("offers")
    if isinstance(off, dict):
        if off.get("@type") == "AggregateOffer" and off.get("offers"):
            return off["offers"] if isinstance(off["offers"], list) else [off["offers"]]
        return [off]
    return off if isinstance(off, list) else []


def _price(offer: dict) -> float | None:
    for key in ("price", "lowPrice"):
        v = offer.get(key)
        if v is None and isinstance(offer.get("priceSpecification"), dict):
            v = offer["priceSpecification"].get("price")
        try:
            return float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return None


def fetch(pages: list[dict]) -> list[dict]:
    s = session()
    out = []
    for page in pages:
        url = page["url"]
        try:
            html = get(s, url).text
        except SourceError as e:
            log.warning("shop page failed: %s", e)
            continue
        products = [o for o in json_ld_objects(html)
                    if str(o.get("@type")).lower() in ("product", "['product']")]
        best = None
        for p in products:
            for off in _offers(p):
                price = _price(off)
                if price and price > 1000 and (best is None or price < best[1]):
                    best = (p, price, off)
        if not best:
            log.warning("no product price found on %s", url)
            continue
        p, price, off = best
        domain = urlparse(url).netloc.removeprefix("www.")
        image = p.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url")
        out.append({
            "id": "shop:" + hashlib.sha1(url.encode()).hexdigest()[:12],
            "source": "shop",
            "shop": domain,
            "url": url,
            "title": p.get("name") or page.get("title") or url,
            "description": (p.get("description") or "")[:2000],
            "price_eur": round(price),
            "price_type": "FIXED",
            "in_stock": "InStock" in str(off.get("availability", "")),
            "kind": "new",
            "variant_hint": page.get("variant"),
            "image": image,
            "city": domain,
        })
        polite_sleep((1.0, 3.0))
    return out
