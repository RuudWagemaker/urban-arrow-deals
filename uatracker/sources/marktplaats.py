"""Marktplaats (and its Belgian twin 2dehands) via the JSON search endpoint the website itself uses.

Note: Marktplaats' terms don't allow automated collection. This runs once a day with
pauses between requests, for personal use. If they block it, see README for alternatives.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlencode

from .http import SourceError, get, json_ld_objects, meta_content, polite_sleep, session, strip_html

log = logging.getLogger(__name__)

PRICE_TYPES_WITH_PRICE = {"FIXED", "MIN_BID", "FAST_BID", "NEGOTIABLE"}


def _searches(cfg: dict) -> list[tuple[str, int | None]]:
    """Each query is run inside the bike category and (by default) across all categories,
    because sellers don't always pick the right category."""
    cats = []
    if cfg.get("category_l1"):
        cats.append(cfg["category_l1"])
    if cfg.get("also_search_all_categories", True) or not cats:
        cats.append(None)
    return [(q, c) for q in cfg.get("queries", ["urban arrow"]) for c in cats]


def fetch(cfg: dict, home: dict, source: str, known: dict[str, dict],
          stats: list | None = None) -> list[dict]:
    s = session()
    host = cfg["host"].rstrip("/")
    page_size = int(cfg.get("page_size", 100))
    results: dict[str, dict] = {}
    stats = stats if stats is not None else []

    for q, cat in _searches(cfg):
        offset, seen_here, total = 0, 0, None
        entry = {"query": q, "category": cat, "pages": 0, "listings": 0, "total": None, "error": None}
        stats.append(entry)
        for _ in range(cfg.get("max_pages_per_query", 5)):
            params = {
                "query": q,
                "limit": page_size,
                "offset": offset,
                "searchInTitleAndDescription": "true",
                "sortBy": "SORT_INDEX",
                "sortOrder": "DECREASING",
                "viewOptions": "list-view",
            }
            if cat:
                params["l1CategoryId"] = cat
            if home.get("postcode") and source == "marktplaats":
                params["postcode"] = home["postcode"]
            try:
                r = get(s, f"{host}/lrp/api/search?{urlencode(params)}",
                        headers={"Accept": "application/json"})
                data = r.json()
            except (SourceError, ValueError) as e:
                entry["error"] = str(e)[:300]
                log.warning("%s %r cat=%s failed: %s", source, q, cat, e)
                break
            page = data.get("listings") or []
            for raw in page:
                item = normalize(raw, host, source)
                if item:
                    results[item["id"]] = item
            total = data.get("totalResultCount")
            seen_here += len(page)
            entry.update(pages=entry["pages"] + 1, listings=seen_here, total=total)
            # the server may return fewer than we asked for, so step by what we actually got
            offset += len(page)
            log.info("%s %r cat=%s: page of %d (have %d of %s)", source, q, cat, len(page), seen_here, total)
            if not page or (total is not None and offset >= total):
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


DETAILS_VERSION = 2   # bump to re-open listing pages after improving the description reader
DEBUG_SAMPLES: list[dict] = []   # page snippets saved to last_run.json when the reader fails
MIN_FULL = 220               # search snippets are cut at 200 characters


def _fetch_details(s, cfg: dict, results: dict[str, dict], known: dict[str, dict]) -> None:
    """Search results only carry a short snippet. Open each listing once for the full text."""
    budget = cfg.get("max_detail_fetches_per_run", 50)
    tried = found = 0
    for lid, item in results.items():
        prev = known.get(lid)
        if prev and (prev.get("full_description") or prev.get("details_version") == DETAILS_VERSION):
            if len(prev.get("description") or "") > len(item.get("description") or ""):
                item["description"] = prev["description"]
            item["full_description"] = prev.get("full_description", False)
            item["details_version"] = prev.get("details_version")
            continue
        # cheap accessories don't need their full text
        if item.get("price_eur") is not None and item["price_eur"] < 800:
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
        tried += 1
        item["details_version"] = DETAILS_VERSION
        snippet = item.get("description") or ""
        desc = full_description(html, snippet)
        if desc and len(desc) > len(snippet):
            item["description"] = desc
            item["full_description"] = True
            found += 1
        elif len(DEBUG_SAMPLES) < 3:
            DEBUG_SAMPLES.append(_debug_sample(html, snippet, item["url"]))
    log.info("listing pages: opened %d, full description found in %d", tried, found)


def _needle_regex(snippet: str, words: int = 7) -> re.Pattern | None:
    """Regex for the start of the snippet that tolerates any whitespace, <br>, &nbsp; or
    JSON escapes between the words."""
    all_words = re.findall(r"\w+", snippet)
    # use plain-ASCII words: accented letters may be encoded differently in the page
    ws = []
    for w in all_words:
        if w.isascii():
            ws.append(w)
            if len(ws) == words:
                break
        elif ws:
            ws = []
    if len(ws) < 4:
        return None
    gap = r"(?:[^\w<]|\\[nrtu]|\\u00a0|&nbsp;|&#\d+;|<br\s*/?>|<[^>]{0,40}>)+"
    return re.compile(gap.join(re.escape(w) for w in ws), re.I)


def _enclosing_json_string(html: str, pos: int) -> str | None:
    """If position `pos` is inside a JSON string, return that whole string decoded."""
    i = pos
    while i > 0 and pos - i < 50000:
        i -= 1
        if html[i] == '"' and html[i - 1] != "\\":
            break
    else:
        return None
    j = pos
    while j < len(html) and j - pos < 50000:
        if html[j] == '"' and html[j - 1] != "\\":
            break
        j += 1
    try:
        return json.loads(html[i:j + 1])
    except ValueError:
        return None


def full_description(html: str, snippet: str) -> str | None:
    best = None

    def consider(text):
        nonlocal best
        if text:
            text = strip_html(text)
            if len(text) > len(best or ""):
                best = text

    for obj in json_ld_objects(html):
        if isinstance(obj.get("description"), str):
            consider(obj["description"])
    consider(_longest_embedded_description(html))

    # Find where the snippet's text sits in the page, whatever the field is called.
    rx = _needle_regex(snippet)
    if rx:
        for m in list(rx.finditer(html))[:20]:
            consider(_enclosing_json_string(html, m.start()))
        # visible page text: from the snippet to the next section heading
        body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = strip_html(body)
        m = rx.search(text)
        if m:
            rest = text[m.start(): m.start() + 8000]
            stop = re.search(r"(?<=.{80})\b(Kenmerken|Bekijk ook|Advertentienummer|Meer van deze adverteerder|"
                             r"Deel deze advertentie|Bied of stel een vraag|Vergelijkbare advertenties)\b", rest, re.S)
            consider(rest[: stop.start()] if stop else None)
    if best and snippet and rx and not rx.search(best):
        # whatever we found must contain the snippet, otherwise it's some other text on the page
        best = None
    return best


def _debug_sample(html: str, snippet: str, url: str) -> dict:
    rx = _needle_regex(snippet)
    hits = [m.start() for m in rx.finditer(html)][:3] if rx else []
    return {
        "url": url,
        "page_length": len(html),
        "snippet_found_at": hits,
        "context": [html[max(0, h - 400): h + 600] for h in hits[:2]],
        "script_tags": re.findall(r"<script[^>]{0,120}>", html)[:15],
        "has_json_ld": "application/ld+json" in html,
    }


def _longest_embedded_description(html: str) -> str | None:
    """The listing page embeds its data as JSON in a script; take the longest description in it."""
    best = None
    for m in re.finditer(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', html):
        try:
            text = json.loads('"' + m.group(1) + '"')
        except ValueError:
            continue
        if len(text) > len(best or ""):
            best = text
    return best
