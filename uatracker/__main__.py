"""Daily pipeline: scrape -> merge -> extract -> price -> website -> alerts.

Usage:
  python -m uatracker run            # everything (what the daily GitHub Action runs)
  python -m uatracker run --no-scrape  # re-run extraction/pricing/site on stored data
  python -m uatracker build          # only rebuild the website from stored data
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys

from . import llm, notify, pricing, site, store
from .config import DATA_DIR, SITE_DIR, load_config
from .extract import combine, rules, text_hash
from .sources import marktplaats, shops
from .sources.http import SourceError

log = logging.getLogger("uatracker")


def scrape(cfg: dict, known: dict) -> tuple[list[dict], set[str]]:
    out, ok = [], set()
    home = cfg.get("home", {})
    for key, source in (("marktplaats", "marktplaats"), ("tweedehands_be", "2dehands")):
        sc = cfg.get(key) or {}
        if not sc.get("enabled"):
            continue
        try:
            items = marktplaats.fetch(sc, home, source, known)
            out += items
            ok.add(source)
            log.info("%s: %d listings", source, len(items))
        except SourceError as e:
            log.error("%s failed, keeping yesterday's data: %s", source, e)
    pages = cfg.get("new_bike_pages") or []
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


def enrich(listings: dict, cfg: dict) -> None:
    home = cfg.get("home", {})
    todo = []
    for l in listings.values():
        if l.get("distance_km") is None and l.get("lat") and l.get("lon") and home.get("lat"):
            l["distance_km"] = round(_haversine(home["lat"], home["lon"], l["lat"], l["lon"]), 1)
        h = text_hash(l)
        if l.get("attrs_hash") != h:
            l["rule_attrs"] = rules(l)
            l["attrs"] = combine(l["rule_attrs"], None)
            if l.get("variant_hint") and l["attrs"].get("variant") == "unknown":
                l["attrs"]["variant"] = l["variant_hint"]
            l["attrs_hash"] = h
            l["attrs_source"] = "rules"
        if l.get("active") and l.get("attrs_source") != "claude":
            todo.append(l)

    llm_cfg = cfg.get("llm", {})
    # likely bikes first, then newest
    todo.sort(key=lambda l: (not l["rule_attrs"].get("is_complete_bike"), l.get("first_seen") or ""), reverse=False)
    todo = todo[: llm_cfg.get("max_listings_per_run", 120)]
    results = llm.extract(todo, llm_cfg)
    for l in todo:
        if l["id"] in results:
            l["llm_attrs"] = results[l["id"]]
            l["attrs"] = combine(l["rule_attrs"], results[l["id"]])
            if l.get("variant_hint") and l["attrs"].get("variant") == "unknown":
                l["attrs"]["variant"] = l["variant_hint"]
            l["attrs_source"] = "claude"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uatracker")
    ap.add_argument("command", choices=["run", "build"])
    ap.add_argument("--no-scrape", action="store_true")
    ap.add_argument("--no-alerts", action="store_true")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    listings = store.load(DATA_DIR)
    log.info("loaded %d stored listings", len(listings))

    if args.command == "run":
        if not args.no_scrape:
            scraped, ok = scrape(cfg, listings)
            if not ok:
                log.error("every source failed; not touching stored data")
            stats = store.merge(listings, scraped, ok)
            log.info("merge: %s", stats)
        enrich(listings, cfg)

    msrp = cfg.get("msrp_estimate", {})
    model_info = pricing.run(listings, msrp)
    pricing.score_new(listings, msrp)
    log.info("pricing: %s used bikes, %s comparables, regression=%s",
             model_info["n_used"], model_info["n_comparables"], bool(model_info["regression"]))

    store.save(listings, DATA_DIR)
    site.build(listings, model_info, cfg, SITE_DIR)
    log.info("site written to %s", SITE_DIR)

    if args.command == "run" and not args.no_alerts:
        notify.send(listings, cfg.get("alerts", {}), os.environ.get("SITE_URL"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
