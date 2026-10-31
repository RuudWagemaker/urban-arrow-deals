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
