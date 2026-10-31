import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from uatracker import pricing, store
from uatracker.extract import _mileage, combine, rules
from uatracker.sources.marktplaats import normalize

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw():
    return json.loads((FIX / "marktplaats_search.json").read_text())["listings"]


def test_normalize(raw):
    a, b, c = (normalize(r, "https://www.marktplaats.nl", "marktplaats") for r in raw)
    assert a["id"] == "marktplaats:m1000000001"
    assert a["price_eur"] == 4250 and a["distance_km"] == 5.4
    assert a["url"].startswith("https://www.marktplaats.nl/v/")
    assert a["image"] == "https://images.example/1.jpg"
    assert a["kind"] == "used"
    assert c["price_eur"] is None and c["kind"] == "new" and c["seller_has_website"]


def test_rules(raw):
    a, b, c = (rules(normalize(r, "https://www.marktplaats.nl", "marktplaats")) for r in raw)
    assert a["is_complete_bike"] and a["variant"] == "family" and a["model_year"] == 2022
    assert a["motor"] == "bosch_cargo_line" and a["battery_wh"] == 545 and a["gear_hub"] == "enviolo"
    assert a["mileage_km"] == 3150 and a["rain_tent"] and a["seller_type"] == "private"
    assert not b["is_complete_bike"]  # accessory
    assert c["variant"] == "shorty" and c["motor"] == "bosch_performance_cx" and c["gear_hub"] == "nexus"
    assert c["seller_type"] == "dealer" and c["warranty"] and c["condition"] == "new"


@pytest.mark.parametrize("text,km", [
    ("kilometerstand 12.400", 12400),
    ("pas 850 km gereden", 850),
    ("max 25 km/u, actieradius 70 km", None),
    ("bereik tot 80 km. teller: 4300 km", 4300),
    ("4.150km op de teller", 4150),
])
def test_mileage(text, km):
    assert _mileage(text) == km


def test_combine_prefers_llm_but_keeps_rules():
    out = combine({"variant": "family", "model_year": 2020, "red_flags": ["a"]},
                  {"variant": "shorty", "model_year": None, "red_flags": ["b"]})
    assert out["variant"] == "shorty" and out["model_year"] == 2020 and out["red_flags"] == ["a", "b"]


def _listing(i, year, price, active=True, days=10, variant="family"):
    first = (date.today() - timedelta(days=days)).isoformat()
    return {
        "id": f"t:{i}", "source": "t", "title": f"bike {i}", "price_eur": price, "price_type": "FIXED",
        "kind": "used", "active": active, "first_seen": first, "last_seen": date.today().isoformat(),
        "attrs": {"is_complete_bike": True, "variant": variant, "model_year": year,
                  "motor": "bosch_cargo_line" if year >= 2022 else "bosch_performance_cx",
                  "battery_wh": 545 if year >= 2022 else 500, "gear_hub": "enviolo",
                  "mileage_km": 1500 * (2026 - year), "condition": "good", "seller_type": "private"},
    }


def test_pricing_flags_cheap_and_expensive():
    rnd = random.Random(1)
    L = {}
    for i in range(80):
        y = rnd.randint(2016, 2025)
        fair = 7000 * 0.8 * 0.88 ** (date.today().year - y)
        L[f"t:{i}"] = _listing(i, y, round(fair * rnd.uniform(0.9, 1.1), -1))
    L["t:cheap"] = _listing("cheap", 2021, 2400)
    L["t:dear"] = _listing("dear", 2018, 5200)
    info = pricing.run(L, {"family": 7000})
    assert info["regression"] is not None
    assert L["t:cheap"]["deal"]["label"] in ("great deal", "good deal")
    assert L["t:dear"]["deal"]["label"] == "overpriced"
    assert L["t:cheap"]["fair"]["method"] == "comparables"
    # model should learn that older bikes are cheaper
    assert info["regression"]["effects_pct"]["age"] < 0


def test_pricing_prior_with_little_data():
    L = {"t:1": _listing(1, 2022, 4000)}
    pricing.run(L, {"family": 7500})
    assert L["t:1"]["fair"]["method"] == "list-price estimate"
    assert L["t:1"]["fair"]["confidence"] == "low"


def test_merge_tracks_prices_and_removals():
    L = {}
    a = {"id": "s:1", "source": "s", "title": "UA family", "city": "Oss", "price_eur": 4000}
    store.merge(L, [a], {"s"})
    assert L["s:1"]["active"] and L["s:1"]["price_history"][0][1] == 4000
    store.merge(L, [dict(a, price_eur=3800)], {"s"})
    assert [p for _, p in L["s:1"]["price_history"]] == [4000, 3800]
    store.merge(L, [], {"other"})  # source s failed today -> untouched
    assert L["s:1"]["active"]
    store.merge(L, [], {"s"})
    assert not L["s:1"]["active"] and L["s:1"]["removed_on"]
    # relisted under a new id -> inherits history
    stats = store.merge(L, [dict(a, id="s:2", price_eur=3700)], {"s"})
    assert stats["relists"] == 1 and L["s:2"]["relist_of"] == "s:1"
    assert [p for _, p in L["s:2"]["price_history"]] == [4000, 3800, 3700]


def test_alerts_only_once_and_on_price_drop():
    from uatracker import notify
    l = _listing("a", 2022, 3000)
    l["deal"] = {"pct": 20, "label": "great deal", "flags": []}
    l["fair"] = {"price": 3750, "confidence": "high"}
    l["distance_km"] = 10
    L = {"a": l}
    new, drops = notify.pick(L, {"min_deal_pct": 10, "max_distance_km": 100})
    assert new == [l] and drops == []
    l["alerted_price"] = 3000
    assert notify.pick(L, {"min_deal_pct": 10, "max_distance_km": 100}) == ([], [])
    l["price_eur"] = 2800
    assert notify.pick(L, {"min_deal_pct": 10, "max_distance_km": 100}) == ([], [l])


def test_search_plan_covers_category_and_all():
    from uatracker.sources.marktplaats import _searches
    assert _searches({"queries": ["urban arrow"], "category_l1": 445}) == [("urban arrow", 445), ("urban arrow", None)]


def test_email_content():
    from uatracker import notify
    l = _listing("a", 2022, 3000)
    l.update(url="https://www.marktplaats.nl/v/x", deal={"pct": 18, "label": "great deal", "flags": ["check km"]},
             fair={"price": 3650, "low": 3400, "high": 3900, "confidence": "high"}, distance_km=4)
    subject, text, html = notify.build([l], [], "https://site", first=False)
    assert "1 new deal" in subject and "€3.000" in subject
    assert "check km" in text and "https://www.marktplaats.nl/v/x" in html


def test_other_brands_and_project_bikes_are_not_emailed():
    from uatracker import notify
    from uatracker.extract import combine
    a = combine({"is_complete_bike": True, "is_urban_arrow": True}, {"is_urban_arrow": False})
    assert not a["is_complete_bike"]
    l = _listing("p", 2020, 900)
    l["attrs"]["condition"] = "needs_work"
    l.update(deal={"pct": 60, "label": "needs work", "flags": []}, fair={"price": 3000, "confidence": "high"})
    assert notify.pick({"p": l}, {"min_deal_pct": 10}) == ([], [])


def test_embedded_description_is_found():
    from uatracker.sources.marktplaats import _longest_embedded_description
    html = '<script>window.__CONFIG__={"a":{"description":"kort"},"listing":{"description":"Urban Arrow 2021\\nKilometerstand 4.000 km \\"top\\""}}</script>'
    assert _longest_embedded_description(html) == 'Urban Arrow 2021\nKilometerstand 4.000 km "top"'


def test_cheap_accessories_skip_claude():
    from uatracker.__main__ import worth_reading
    assert not worth_reading({"price_eur": 150, "rule_attrs": {"is_complete_bike": False}})
    assert worth_reading({"price_eur": None, "rule_attrs": {"is_complete_bike": False}})
    assert worth_reading({"price_eur": 3000, "rule_attrs": {"is_complete_bike": False}})


def test_ntfy_messages(monkeypatch):
    from uatracker import notify
    posts = []

    class R:
        status_code = 200
        text = ""
    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: posts.append(json) or R())
    monkeypatch.setenv("NTFY_TOPIC", "ua-deals-test")
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    L = {}
    for i in range(14):
        l = _listing(i, 2022, 3000 - i)
        l.update(url=f"https://m/{i}", image="https://img/1.jpg", distance_km=5,
                 deal={"pct": 25, "label": "great deal", "flags": []},
                 fair={"price": 4000, "low": 3800, "high": 4200, "confidence": "high"})
        L[l["id"]] = l
    notify.send(L, {"min_deal_pct": 10}, "https://site")
    assert posts[0]["title"].startswith("Urban Arrow tracker is running: 14") and len(posts) == 6
    assert all(p["topic"] == "ua-deals-test" for p in posts)
    assert posts[1]["click"].startswith("https://m/") and posts[1]["attach"] == "https://img/1.jpg"
    # second scan: nothing new -> nothing sent; a price drop -> one notification
    posts.clear()
    notify.send(L, {"min_deal_pct": 10}, "https://site")
    assert posts == []
    L["t:3"]["price_eur"] = 2500
    notify.send(L, {"min_deal_pct": 10}, "https://site")
    assert len(posts) == 1 and posts[0]["title"].startswith("Price drop: €2.500 (was €2.997)")


SNIP = "Te koop: Urban Arrow Family uit 2021 in nette staat. Bosch Performance CX motor met 500Wh accu, " \
       "Enviolo naaf. Inclusief regenhuif en kussens. Altijd binnen gestald en goed onderhouden door de"
FULL = SNIP + " dealer.\nKilometerstand: 4.250 km\nBouwjaar 2021, aankoopbon aanwezig. Café-ophalen in Rosmalen."


@pytest.mark.parametrize("page", [
    # description in an embedded JSON blob under an unknown key, with escapes
    '<script>window.__STATE__ = {"ad":{"body":' + json.dumps(FULL) + ',"x":1}};</script><p>' + SNIP[:120] + '</p>',
    # description only as visible HTML
    '<html><body><h1>Urban Arrow</h1><div class="desc">' + FULL.replace("\n", "<br>") +
    '</div><div>Kenmerken</div><div>Merk: Urban Arrow</div></body></html>',
    # JSON-LD
    '<script type="application/ld+json">' + json.dumps({"@type": "Product", "description": FULL}) + '</script>',
])
def test_full_description_found(page):
    from uatracker.sources.marktplaats import full_description
    d = full_description(page, SNIP[:200])
    assert d and "Kilometerstand: 4.250 km" in d and "Rosmalen" in d and "Merk:" not in d


def test_unrelated_long_text_is_ignored():
    from uatracker.sources.marktplaats import full_description
    page = '<script>{"description":"' + "Marktplaats is de grootste ... " * 20 + '"}</script>'
    assert full_description(page, SNIP[:200]) is None


def test_empty_search_is_retried(monkeypatch):
    from uatracker.sources import marktplaats
    calls = []

    class R:
        def __init__(self, d): self.d = d
        def json(self): return self.d
    ads = [{"itemId": f"m{i}", "title": "Urban Arrow Family", "priceInfo": {"priceCents": 300000, "priceType": "FIXED"}}
           for i in range(30)]

    def fake_get(s, url, **kw):
        calls.append(url)
        return R({"listings": [], "totalResultCount": 0} if len(calls) == 1 else {"listings": ads, "totalResultCount": 30})
    monkeypatch.setattr(marktplaats, "get", fake_get)
    monkeypatch.setattr(marktplaats, "polite_sleep", lambda *a: None)
    monkeypatch.setattr(marktplaats.time, "sleep", lambda *a: None)
    known = {f"marktplaats:m{i}": {"source": "marktplaats", "active": True, "title": "Urban Arrow Family"} for i in range(30)}
    stats = []
    out = marktplaats.fetch({"host": "https://x", "queries": ["urban arrow"], "fetch_details": False}, {}, "marktplaats", known, stats)
    assert len(out) == 30 and stats[0]["retries"] == 1


# ---------- multi-product / thermostat ----------

def _t6(title, desc="", price=100, cond="Gebruikt"):
    return {"id": "x", "title": title, "description": desc, "price_eur": price, "condition_raw": cond}


@pytest.mark.parametrize("title,desc,price,cond,expect", [
    ("Honeywell T6R geseald", "ongeopend, kan verzonden worden", 150, "Nieuw",
     {"is_item": True, "model": "t6r_wireless", "condition": "sealed_new", "shipping": True}),
    ("Honeywell T6 bedraad", "werkt prima", 70, "Gebruikt", {"is_item": True, "model": "t6_wired", "condition": "used"}),
    ("Honeywell T6R zonder ontvanger", "", 55, "Gebruikt", {"includes_receiver": False}),
    ("2x Honeywell T6R thermostaat", "", 190, "Gebruikt", {"quantity": 2}),
    ("Honeywell Round thermostaat", "", 25, "Gebruikt", {"is_item": False}),
    ("Ontvanger Honeywell T6R", "losse ontvanger", 30, "Gebruikt", {"is_item": False}),
    ("Gezocht: Honeywell T6R", "", None, "Gebruikt", {"is_item": False}),
])
def test_thermostat_rules(title, desc, price, cond, expect):
    from uatracker import profiles
    d = profiles.get("thermostat").rules(_t6(title, desc, price, cond))
    for k, v in expect.items():
        assert d[k] == v, (k, d[k])


def test_thermostat_pricing_per_unit_and_receiver():
    from uatracker import profiles
    p = profiles.get("thermostat")
    L = {}
    for i in range(30):
        a = {"is_item": True, "model": "t6r_wireless", "condition": "used", "includes_receiver": True, "quantity": 1}
        L[f"t:{i}"] = {"id": f"t:{i}", "source": "marktplaats", "price_eur": 95 + (i % 5) * 4, "price_type": "FIXED",
                       "active": True, "first_seen": date.today().isoformat(), "last_seen": date.today().isoformat(), "attrs": a}
    two = dict(L["t:0"], id="t:two", price_eur=150, attrs=dict(L["t:0"]["attrs"], quantity=2))
    L["t:two"] = two
    info = pricing.run(L, {"t6r_wireless": 200}, p)
    assert info["regression"] is not None
    # two units at €75 each against ~€100 per unit: a great deal, fair price is for both
    assert 180 <= two["fair"]["price"] <= 230 and two["deal"]["label"] == "great deal"


def test_products_config_and_legacy_layout():
    from uatracker.config import products
    legacy = {"home": {"name": "x"}, "alerts": {"min_deal_pct": 10}, "marktplaats": {"enabled": True}}
    (pid, pc), = products(legacy)
    assert pid == "urban-arrow" and pc["profile"] == "cargo_bike" and pc["alerts"]["min_deal_pct"] == 10
    cfg = {"alerts": {"min_deal_pct": 10, "max_distance_km": 120},
           "products": {"a": {"profile": "cargo_bike"}, "b": {"profile": "thermostat", "alerts": {"min_deal_pct": 15}}}}
    out = dict(products(cfg))
    assert out["b"]["alerts"] == {"min_deal_pct": 15, "max_distance_km": 120}
