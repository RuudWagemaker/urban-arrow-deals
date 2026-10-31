"""Fair-price model for second-hand Urban Arrows.

How the fair price is found, in order of preference:
1. Adjusted comparables — the most similar bikes (same variant, close in age, battery,
   motor, mileage), each price corrected for its differences using the regression below,
   then a weighted median. This is how a car appraiser works and it's easy to explain.
2. Regression — log(price) explained by age, variant, motor, battery, gear hub, mileage,
   extras and seller type (ridge regression, fitted on all collected listings).
3. Prior — a list-price × depreciation curve, used until enough data is collected.

Asking prices are not sale prices, so comparables are weighted: listings that vanished
quickly (probably sold) count more, listings that have sat for months count less.
"""
from __future__ import annotations

from datetime import date

import numpy as np

COMP_PRICE_TYPES = {"FIXED", "MIN_BID", "NEGOTIABLE"}
VARIANT_GROUPS = {"family": "family", "unknown": "family", "other": "family",
                  "shorty": "shorty", "tender": "tender", "cargo": "cargo"}
NEW_MOTORS = {"bosch_cargo_line", "bosch_cargo_line_speed", "bosch_performance_cx"}
OLD_MOTORS = {"bosch_active_plus", "shimano_steps"}

FEATURES = [
    "intercept", "age", "age_sq", "shorty", "tender", "cargo", "motor_new", "motor_old",
    "battery_kwh", "dual_battery", "enviolo", "log_km", "rain_tent", "child_extras",
    "dealer", "like_new", "needs_work", "speed",
]


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _age(l: dict, year_fallback: float) -> tuple[float, bool]:
    a = l.get("attrs") or {}
    ref = (_d(l.get("last_seen")) or date.today())
    y = a.get("model_year")
    if y:
        return max(0.0, ref.year + (ref.month - 6) / 12 - y), False
    return max(0.0, ref.year - year_fallback), True


def _battery_default(age: float) -> float:
    return 0.4 if age > 7 else 0.5 if age > 3 else 0.545


def features(l: dict, year_fallback: float) -> np.ndarray:
    a = l.get("attrs") or {}
    age, _ = _age(l, year_fallback)
    group = VARIANT_GROUPS.get(a.get("variant") or "unknown", "family")
    motor = a.get("motor") or "unknown"
    wh = a.get("battery_wh")
    kwh = wh / 1000 if wh and 250 <= wh <= 2000 else _battery_default(age)
    km = a.get("mileage_km")
    if km is None:
        km = 1500 * max(age, 0.3)
    v = {
        "intercept": 1.0,
        "age": age,
        "age_sq": age * age / 10,
        "shorty": group == "shorty",
        "tender": group == "tender",
        "cargo": group == "cargo",
        "motor_new": motor in NEW_MOTORS,
        "motor_old": motor in OLD_MOTORS,
        "battery_kwh": kwh,
        "dual_battery": bool(a.get("dual_battery")),
        "enviolo": (a.get("gear_hub") or "").startswith("enviolo"),
        "log_km": float(np.log1p(km / 1000)),
        "rain_tent": bool(a.get("rain_tent")),
        "child_extras": bool(a.get("child_seat_extras")),
        "dealer": a.get("seller_type") == "dealer",
        "like_new": a.get("condition") in ("like_new", "new"),
        "needs_work": a.get("condition") == "needs_work",
        "speed": motor == "bosch_cargo_line_speed",
    }
    return np.array([float(v[f]) for f in FEATURES])


def is_used_bike(l: dict) -> bool:
    a = l.get("attrs") or {}
    return bool(a.get("is_complete_bike")) and l.get("kind") != "new" and a.get("condition") != "new"


def is_comparable(l: dict, today: date) -> bool:
    last = _d(l.get("last_seen"))
    return (
        is_used_bike(l)
        and not l.get("superseded_by")
        and l.get("price_type") in COMP_PRICE_TYPES
        and l.get("price_eur") is not None
        and 600 <= l["price_eur"] <= 20000
        and (l.get("attrs") or {}).get("condition") != "needs_work"
        and last is not None and (today - last).days <= 365
    )


def sold_weight(l: dict) -> float:
    first, last = _d(l.get("first_seen")), _d(l.get("last_seen"))
    days = (last - first).days if first and last else 0
    if not l.get("active") and days <= 30:
        return 1.5  # disappeared fast: likely sold near this price
    if l.get("active") and days > 60:
        return 0.6  # still unsold after two months: probably too high
    return 1.0


def prior_price(l: dict, msrp: dict, year_fallback: float) -> float:
    a = l.get("attrs") or {}
    group = VARIANT_GROUPS.get(a.get("variant") or "unknown", "family")
    base = msrp.get(group) or msrp.get("other") or 7000
    age, _ = _age(l, year_fallback)
    # rough Dutch cargo e-bike curve: ~-20% when it leaves the shop, then ~-9% per year
    return base * 0.85 * (0.91 ** max(age, 0.25))


def fit_ridge(X: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float = 2.0):
    W = np.sqrt(w)[:, None]
    Xw, yw = X * W, y * W[:, 0]
    reg = lam * np.eye(X.shape[1])
    reg[0, 0] = 0.0
    beta = np.linalg.solve(Xw.T @ Xw + reg, Xw.T @ yw)
    resid = y - X @ beta
    sigma = float(np.sqrt(np.average(resid ** 2, weights=w)))
    return beta, sigma


def _wquantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    idx = np.argsort(values)
    v, w = values[idx], weights[idx]
    cw = np.cumsum(w) - 0.5 * w
    return float(np.interp(q * w.sum(), cw, v))


def _similarity_distance(fi: np.ndarray, fj: np.ndarray) -> float:
    ix = {f: k for k, f in enumerate(FEATURES)}
    return (
        abs(fi[ix["age"]] - fj[ix["age"]])
        + 2.0 * abs(fi[ix["battery_kwh"]] - fj[ix["battery_kwh"]])
        + 0.5 * (fi[ix["motor_new"]] != fj[ix["motor_new"]])
        + 0.5 * (fi[ix["motor_old"]] != fj[ix["motor_old"]])
        + 0.4 * abs(fi[ix["log_km"]] - fj[ix["log_km"]])
        + 0.5 * (fi[ix["dealer"]] != fj[ix["dealer"]])
        + 0.3 * (fi[ix["enviolo"]] != fj[ix["enviolo"]])
        + 1.0 * (fi[ix["speed"]] != fj[ix["speed"]])
    )


def deal_label(pct: float) -> str:
    if pct >= 15:
        return "great deal"
    if pct >= 5:
        return "good deal"
    if pct > -5:
        return "fair price"
    if pct > -15:
        return "a bit pricey"
    return "overpriced"


def run(listings: dict[str, dict], msrp: dict, min_regression_n: int = 25, k: int = 12) -> dict:
    """Adds `fair` and `deal` to every used bike. Returns model info for the website."""
    today = date.today()
    used = [l for l in listings.values() if is_used_bike(l)]
    years = [l["attrs"]["model_year"] for l in used if (l.get("attrs") or {}).get("model_year")]
    year_fallback = float(np.median(years)) if years else today.year - 4

    comps = [l for l in used if is_comparable(l, today)]
    F = {l["id"]: features(l, year_fallback) for l in used}

    beta = sigma = None
    if len(comps) >= min_regression_n:
        X = np.array([F[l["id"]] for l in comps])
        y = np.log([l["price_eur"] for l in comps])
        w = np.array([sold_weight(l) for l in comps])
        beta, sigma = fit_ridge(X, y, w)

    group_of = lambda l: VARIANT_GROUPS.get((l.get("attrs") or {}).get("variant") or "unknown", "family")

    for l in used:
        fi = F[l["id"]]
        chain = {l["id"], l.get("relist_of"), l.get("superseded_by")}
        cands = []
        for c in comps:
            if c["id"] in chain or group_of(c) != group_of(l):
                continue
            dist = _similarity_distance(fi, F[c["id"]])
            if dist <= 4.0:
                cands.append((dist, c))
        cands.sort(key=lambda t: t[0])
        cands = cands[:k]

        method, lo, hi, used_comps = None, None, None, []
        if len(cands) >= 4:
            vals, ws = [], []
            for dist, c in cands:
                p = c["price_eur"]
                if beta is not None:
                    p = p * float(np.exp(beta @ (fi - F[c["id"]])))
                vals.append(p)
                ws.append(sold_weight(c) / (1.0 + dist))
                used_comps.append({"id": c["id"], "price": c["price_eur"], "adjusted": round(p)})
            vals, ws = np.array(vals), np.array(ws)
            fair = _wquantile(vals, ws, 0.5)
            lo, hi = _wquantile(vals, ws, 0.25), _wquantile(vals, ws, 0.75)
            method = "comparables"
        elif beta is not None:
            fair = float(np.exp(beta @ fi))
            lo, hi = fair * np.exp(-sigma * 0.67), fair * np.exp(sigma * 0.67)
            method = "regression"
        else:
            fair = prior_price(l, msrp, year_fallback)
            lo, hi = fair * 0.82, fair * 1.18
            method = "list-price estimate"

        lo, hi = min(lo, fair * 0.95), max(hi, fair * 1.05)
        _, year_imputed = _age(l, year_fallback)
        conf = ("high" if len(used_comps) >= 8 and not year_imputed
                else "medium" if (len(used_comps) >= 4 or beta is not None) and not year_imputed
                else "low")
        l["fair"] = {
            "price": round(fair, -1), "low": round(lo, -1), "high": round(hi, -1),
            "method": method, "confidence": conf, "n_comps": len(used_comps),
            "comps": used_comps[:6],
        }
        ask = l.get("price_eur")
        if ask:
            pct = (fair - ask) / fair * 100
            flags = list((l.get("attrs") or {}).get("red_flags") or [])
            if pct > 40:
                flags.append("price far below market: check carefully (scam/stolen/defect?)")
            l["deal"] = {"pct": round(pct, 1), "label": deal_label(pct), "flags": flags}
        else:
            l["deal"] = {"pct": None, "label": "no price", "flags": list((l.get("attrs") or {}).get("red_flags") or [])}

    curve = []
    if beta is not None:
        ref = {"attrs": {"variant": "family", "motor": "bosch_cargo_line", "gear_hub": "enviolo",
                         "battery_wh": 545, "condition": "good", "seller_type": "private"},
               "last_seen": today.isoformat()}
        for age in range(0, 11):
            ref["attrs"]["model_year"] = today.year - age
            ref["attrs"]["mileage_km"] = int(1500 * max(age, 0.3))
            if age > 3:
                ref["attrs"]["motor"] = "bosch_performance_cx"
                ref["attrs"]["battery_wh"] = 500
            curve.append({"age": age, "price": round(float(np.exp(beta @ features(ref, year_fallback))), -1)})

    return {
        "n_used": len(used),
        "n_comparables": len(comps),
        "regression": None if beta is None else {
            "n": len(comps), "sigma": round(sigma, 3),
            "effects_pct": {f: round((float(np.exp(b)) - 1) * 100, 1) for f, b in zip(FEATURES, beta) if f != "intercept"},
        },
        "family_curve": curve,
    }


def score_new(listings: dict[str, dict], msrp: dict) -> None:
    """New bikes: compare with the estimated list price and the cheapest offer per variant."""
    new = [l for l in listings.values()
           if l.get("active") and l.get("price_eur") and (l.get("kind") == "new" or (l.get("attrs") or {}).get("condition") == "new")
           and (l.get("attrs") or {}).get("is_complete_bike", True)]
    cheapest: dict[str, int] = {}
    for l in new:
        g = VARIANT_GROUPS.get((l.get("attrs") or {}).get("variant") or l.get("variant_hint") or "unknown", "family")
        l["variant_group"] = g
        cheapest[g] = min(cheapest.get(g, 10**9), l["price_eur"])
    for l in new:
        g = l["variant_group"]
        ref = msrp.get(g) or msrp.get("other")
        l["new_deal"] = {
            "vs_msrp_pct": round((ref - l["price_eur"]) / ref * 100, 1) if ref else None,
            "is_cheapest": l["price_eur"] == cheapest[g],
        }
