"""Profile for electric cargo bikes (Urban Arrow)."""
from __future__ import annotations

from datetime import date

import numpy as np

from ..extract import EXTRACT_VERSION, BikeDetails, combine, rules
from . import Profile

SYSTEM = """You extract structured facts from Dutch (sometimes Belgian or English) second-hand and \
new listings for Urban Arrow electric cargo bikes ("bakfiets"). You receive several listings as JSON \
and return one item per listing, with `ref` copied exactly.

Background:
- Variants: "family" (the classic two-wheel family bakfiets with EPP foam box and bench; includes \
  Family Performance / Family Cargo Line), "shorty" (shorter box, 1 bench), "tender" (Tender \
  1500/2500 business models), "cargo" (Cargo L/XL, flatbed, pick-up business models). "other" for \
  anything else Urban Arrow, "unknown" if the text doesn't say.
- Motors: Bosch Cargo Line (2021+), Cargo Line Speed (45 km/h S-pedelec), Performance Line CX, \
  Performance Line, Active Line Plus (older/cheaper), Shimano STEPS (older models).
- Batteries are usually 400, 500, 545, 625, 725 or 800 Wh. With two batteries, give the total.
- Gear hubs: Enviolo (also called NuVinci, stepless/traploos), Enviolo AutomatiQ, Shimano Nexus.
- mileage_km is the odometer reading ("kilometerstand", "gereden", "teller"). Never use range \
  ("bereik", "actieradius") or top speed ("25 km/u").
- seller_type "dealer" when it is a shop/company (warranty, VAT/BTW, trade-in, "wij", showroom).
- is_urban_arrow=false when the bike for sale is another brand (Babboe, Bakfiets.nl, Johnny Loco, \
  Christiania, ...) and Urban Arrow is only mentioned for comparison or compatibility.
- is_complete_bike=false for parts or accessories only (rain tent, battery, bench), wanted ads \
  ("gezocht"), rentals and lease offers.
- red_flags: only real warning signs, e.g. price far too low for the year, payment up front or \
  shipping only, no keys/proof of ownership, frame number missing, serious damage not reflected in price.
Use null / "unknown" / false when the listing doesn't say. Do not guess the model year from photos \
or from the motor generation."""

VARIANT_GROUPS = {"family": "family", "unknown": "family", "other": "family",
                  "shorty": "shorty", "tender": "tender", "cargo": "cargo"}
NEW_MOTORS = {"bosch_cargo_line", "bosch_cargo_line_speed", "bosch_performance_cx"}
OLD_MOTORS = {"bosch_active_plus", "shimano_steps"}
MOTOR = {
    "bosch_cargo_line_speed": "Cargo Line Speed", "bosch_cargo_line": "Cargo Line",
    "bosch_performance_cx": "Performance CX", "bosch_performance": "Performance",
    "bosch_active_plus": "Active Plus", "shimano_steps": "Shimano STEPS",
}

FEATURES = [
    "intercept", "age", "age_sq", "shorty", "tender", "cargo", "motor_new", "motor_old",
    "battery_kwh", "dual_battery", "enviolo", "log_km", "rain_tent", "child_extras",
    "dealer", "like_new", "needs_work", "speed",
]
IX = {f: k for k, f in enumerate(FEATURES)}


def _d(s):
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def age(l: dict, ctx: dict) -> tuple[float, bool]:
    a = l.get("attrs") or {}
    ref = _d(l.get("last_seen")) or date.today()
    y = a.get("model_year")
    if y:
        return max(0.0, ref.year + (ref.month - 6) / 12 - y), False
    return max(0.0, ref.year - ctx.get("year_fallback", date.today().year - 4)), True


def make_ctx(used: list[dict]) -> dict:
    years = [l["attrs"]["model_year"] for l in used if (l.get("attrs") or {}).get("model_year")]
    return {"year_fallback": float(np.median(years)) if years else date.today().year - 4}


def group(l: dict) -> str:
    a = l.get("attrs") or {}
    return VARIANT_GROUPS.get(a.get("variant") or l.get("variant_hint") or "unknown", "family")


def feature_fn(l: dict, ctx: dict) -> np.ndarray:
    a = l.get("attrs") or {}
    ag, _ = age(l, ctx)
    g = group(l)
    motor = a.get("motor") or "unknown"
    wh = a.get("battery_wh")
    kwh = wh / 1000 if wh and 250 <= wh <= 2000 else (0.4 if ag > 7 else 0.5 if ag > 3 else 0.545)
    km = a.get("mileage_km")
    if km is None:
        km = 1500 * max(ag, 0.3)
    v = {
        "intercept": 1.0, "age": ag, "age_sq": ag * ag / 10,
        "shorty": g == "shorty", "tender": g == "tender", "cargo": g == "cargo",
        "motor_new": motor in NEW_MOTORS, "motor_old": motor in OLD_MOTORS,
        "battery_kwh": kwh, "dual_battery": bool(a.get("dual_battery")),
        "enviolo": (a.get("gear_hub") or "").startswith("enviolo"),
        "log_km": float(np.log1p(km / 1000)),
        "rain_tent": bool(a.get("rain_tent")), "child_extras": bool(a.get("child_seat_extras")),
        "dealer": a.get("seller_type") == "dealer",
        "like_new": a.get("condition") in ("like_new", "new"),
        "needs_work": a.get("condition") == "needs_work",
        "speed": motor == "bosch_cargo_line_speed",
    }
    return np.array([float(v[f]) for f in FEATURES])


def similarity(fi: np.ndarray, fj: np.ndarray) -> float:
    return (
        abs(fi[IX["age"]] - fj[IX["age"]])
        + 2.0 * abs(fi[IX["battery_kwh"]] - fj[IX["battery_kwh"]])
        + 0.5 * (fi[IX["motor_new"]] != fj[IX["motor_new"]])
        + 0.5 * (fi[IX["motor_old"]] != fj[IX["motor_old"]])
        + 0.4 * abs(fi[IX["log_km"]] - fj[IX["log_km"]])
        + 0.5 * (fi[IX["dealer"]] != fj[IX["dealer"]])
        + 0.3 * (fi[IX["enviolo"]] != fj[IX["enviolo"]])
        + 1.0 * (fi[IX["speed"]] != fj[IX["speed"]])
    )


def prior(l: dict, msrp: dict, ctx: dict) -> float:
    base = msrp.get(group(l)) or msrp.get("other") or 7000
    ag, _ = age(l, ctx)
    # rough Dutch cargo e-bike curve: ~-20% when it leaves the shop, then ~-9% per year
    return base * 0.85 * (0.91 ** max(ag, 0.25))


def key_info_missing(l: dict, ctx: dict) -> bool:
    return age(l, ctx)[1]


def curve(beta: np.ndarray, ctx: dict) -> list[dict]:
    """Typical value of a privately sold Family by age, for the Market tab."""
    today = date.today()
    ref = {"attrs": {"variant": "family", "motor": "bosch_cargo_line", "gear_hub": "enviolo",
                     "battery_wh": 545, "condition": "good", "seller_type": "private"},
           "last_seen": today.isoformat()}
    out = []
    for ag in range(0, 11):
        ref["attrs"]["model_year"] = today.year - ag
        ref["attrs"]["mileage_km"] = int(1500 * max(ag, 0.3))
        if ag > 3:
            ref["attrs"]["motor"] = "bosch_performance_cx"
            ref["attrs"]["battery_wh"] = 500
        out.append({"age": ag, "price": round(float(np.exp(beta @ feature_fn(ref, ctx))), -1)})
    return out


def facts(l: dict) -> str:
    a = l.get("attrs") or {}
    bits = [
        str(a["model_year"]) if a.get("model_year") else None,
        (a.get("variant") or "").title() if a.get("variant") not in (None, "unknown", "other") else None,
        MOTOR.get(a.get("motor")),
        f"{a['battery_wh']} Wh" if a.get("battery_wh") else None,
        f"{a['mileage_km']:,} km".replace(",", ".") if a.get("mileage_km") else None,
        "rain tent" if a.get("rain_tent") else None,
        "dealer" if a.get("seller_type") == "dealer" else None,
        f"{l['distance_km']:.0f} km away" if l.get("distance_km") is not None else l.get("city"),
    ]
    return " · ".join(b for b in bits if b)


def short_name(l: dict) -> str:
    a = l.get("attrs") or {}
    v = (a.get("variant") or "").title() if a.get("variant") not in (None, "unknown", "other") else "Urban Arrow"
    return " ".join(x for x in [str(a.get("model_year") or ""), v] if x)


PROFILE = Profile(
    name="cargo_bike", item="bike", Details=BikeDetails, system_prompt=SYSTEM,
    extract_version=EXTRACT_VERSION, rules=rules, combine=combine,
    features=FEATURES, feature_fn=feature_fn, similarity=similarity, group=group, prior=prior,
    facts=facts, short_name=short_name, make_ctx=make_ctx, key_info_missing=key_info_missing,
    curve=curve, comp_price_range=(600, 20000), min_item_price=800, max_similarity=4.0,
)
