"""Pipeline for every product in config.yaml: scrape -> merge -> extract -> price -> alerts -> website.

Usage:
  python -m uatracker run              # everything (what the GitHub Action runs)
  python -m uatracker run --no-scrape  # re-run extraction/pricing/site on stored data
  python -m uatracker build            # only rebuild the website from stored data
  python -m uatracker run --product honeywell-t6   # just one product
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from pathlib import Path

from . import llm, notify, pricing, profiles, site, store
from .config import DATA_DIR, SITE_DIR, load_config, products
from .extract import text_hash
from .profiles import Profile, is_item
from .sources import marktplaats, shops
from .sources.http import SourceError

log = logging.getLogger("uatracker")


def scrape(pcfg: dict, known: dict, report: dict) -> tuple[list[dict], set[str]]:
    """Returns (listings, sources that are complete enough to mark missing ads as removed)."""
    out, ok = [], set()
    home = pcfg.get("home", {})
    for key, source in (("marktplaats", "marktplaats"), ("tweedehands_be", "2dehands")):
        sc = pcfg.get(key) or {}
        if not sc.get("enabled"):
            continue
        stats: list = []
        rep = report.setdefault("sources", {})[source] = {"searches": stats}
        try:
            items = marktplaats.fetch(sc, home, source, known, stats)
        except SourceError as e:
            rep["error"] = str(e)[:300]
            log.error("%s failed, keeping previous data: %s", source, e)
            continue
        out += items
        rep["unique_listings"] = len(items)
        if marktplaats.DEBUG_SAMPLES:
            rep["description_debug"] = list(marktplaats.DEBUG_SAMPLES)
            marktplaats.DEBUG_SAMPLES.clear()
        log.info("%s: %d listings", source, len(items))
        # Only treat missing ads as sold/removed when today's search looks complete.
        before = sum(1 for l in known.values() if l.get("source") == source and l.get("active"))
        partial = any(s.get("error") for s in stats) or len(items) < 0.5 * before
        rep["complete"] = not partial
        if partial:
            log.warning("%s: search looks incomplete (%d now vs %d active before); not marking removals",
                        source, len(items), before)
        else:
            ok.add(source)
    pages = pcfg.get("new_item_pages") or []
    if pages:
        items = shops.fetch(pages)
        out += items
        if items:
            ok.add("shop")
        log.info("shops: %d products", len(items))
    return out, ok


def _haversine(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def worth_reading(l: dict, min_price: float = 800) -> bool:
    """Cheap accessories don't need Claude: the text rules already know they aren't the
    product. Everything that could be the product gets read."""
    price = l.get("price_eur")
    return is_item(l["rule_attrs"]) or price is None or price >= min_price


def enrich(listings: dict, pcfg: dict, p: Profile) -> None:
    home = pcfg.get("home", {})
    todo = []
    for l in listings.values():
        if l.get("distance_km") is None and l.get("lat") and l.get("lon") and home.get("lat"):
            l["distance_km"] = round(_haversine(home["lat"], home["lon"], l["lat"], l["lon"]), 1)
        h = text_hash(l)
        if l.get("attrs_hash") != h or l.get("attrs_version") != p.extract_version:
            l["rule_attrs"] = p.rules(l)
            l["attrs"] = p.combine(l["rule_attrs"], None)
            if l.get("variant_hint") and l["attrs"].get("variant") == "unknown":
                l["attrs"]["variant"] = l["variant_hint"]
            l["attrs_hash"] = h
            l["attrs_version"] = p.extract_version
            l["attrs_source"] = "rules"
            l.pop("llm_attrs", None)
        else:
            # re-apply the combining rules, so rule changes reach ads Claude already read
            l["attrs"] = p.combine(l["rule_attrs"], l.get("llm_attrs"))
            if l.get("variant_hint") and l["attrs"].get("variant") == "unknown":
                l["attrs"]["variant"] = l["variant_hint"]
        if l.get("active") and l.get("attrs_source") != "claude" and worth_reading(l, p.min_item_price):
            todo.append(l)

    llm_cfg = pcfg.get("llm", {})
    todo.sort(key=lambda l: (not is_item(l["rule_attrs"]), l.get("first_seen") or ""))
    log.info("Claude will read %d listings", min(len(todo), llm_cfg.get("max_listings_per_run", 120)))
    todo = todo[: llm_cfg.get("max_listings_per_run", 120)]
    results = llm.extract(todo, llm_cfg, p)
    for l in todo:
        if l["id"] in results:
            l["llm_attrs"] = results[l["id"]]
            l["attrs"] = p.combine(l["rule_attrs"], results[l["id"]])
            if l.get("variant_hint") and l["attrs"].get("variant") == "unknown":
                l["attrs"]["variant"] = l["variant_hint"]
            l["attrs_source"] = "claude"


def migrate_legacy_data(first_product: str) -> None:
    """The first version kept one product's data directly in data/. Move it to data/<product>/."""
    target = DATA_DIR / first_product
    for name in ("listings.json", "last_run.json"):
        old = DATA_DIR / name
        if old.exists() and not (target / name).exists():
            target.mkdir(parents=True, exist_ok=True)
            old.replace(target / name)
            log.info("moved %s to %s/", old, target)


def run_product(pid: str, pcfg: dict, args) -> dict:
    p = profiles.get(pcfg.get("profile", "cargo_bike"))
    data_dir = Path(DATA_DIR) / pid
    for key in ("marktplaats", "tweedehands_be"):
        if pcfg.get(key):
            pcfg[key].setdefault("detail_min_price", p.min_item_price)
    listings = store.load(data_dir)
    log.info("[%s] loaded %d stored listings", pid, len(listings))

    report = None
    if args.command == "run":
        if not args.no_scrape:
            report = {"started_at": store.now_iso()}
            scraped, ok = scrape(pcfg, listings, report)
            stats = store.merge(listings, scraped, ok)
            report["merge"] = stats
            log.info("[%s] merge: %s", pid, stats)
        enrich(listings, pcfg, p)

    msrp = pcfg.get("msrp_estimate", {})
    model_info = pricing.run(listings, msrp, p)
    pricing.score_new(listings, msrp, p)
    log.info("[%s] pricing: %s used items, %s comparables, regression=%s", pid,
             model_info["n_used"], model_info["n_comparables"], bool(model_info["regression"]))

    if args.command == "run" and not args.no_alerts:
        site_url = os.environ.get("SITE_URL")
        notify.send(listings, pcfg["alerts"], f"{site_url}#{pid}" if site_url else None,
                    product=pcfg.get("name", pid), profile=p)

    store.save(listings, data_dir)
    if report is not None:
        active = [l for l in listings.values() if l.get("active")]
        report.update(
            finished_at=store.now_iso(),
            active_listings=len(active),
            active_items=sum(1 for l in active if is_item(l.get("attrs"))),
            llm_backend=llm.pick_backend(pcfg.get("llm", {})),
        )
        store.save_report(report, data_dir)
    return {"id": pid, "config": pcfg, "profile": p, "listings": listings, "model": model_info}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uatracker")
    ap.add_argument("command", choices=["run", "build"])
    ap.add_argument("--no-scrape", action="store_true")
    ap.add_argument("--no-alerts", action="store_true")
    ap.add_argument("--product", help="only this product id")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    prods = products(cfg)
    migrate_legacy_data(prods[0][0])
    results = []
    for pid, pcfg in prods:
        if args.product and args.product != pid:
            # still publish the other products' stored data on the site
            p = profiles.get(pcfg.get("profile", "cargo_bike"))
            listings = store.load(Path(DATA_DIR) / pid)
            results.append({"id": pid, "config": pcfg, "profile": p, "listings": listings,
                            "model": pricing.run(listings, pcfg.get("msrp_estimate", {}), p)})
            continue
        results.append(run_product(pid, pcfg, args))
    site.build(results, SITE_DIR)
    log.info("site written to %s", SITE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
