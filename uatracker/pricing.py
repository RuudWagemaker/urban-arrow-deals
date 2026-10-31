"""Fair-price model for second-hand items (any product profile).

How the fair price is found, in order of preference:
1. Adjusted comparables — the most similar ads (same group, e.g. variant or model; close
   on the profile's similarity measure), each price corrected for its differences using
   the regression below, then a weighted median. This is how an appraiser works.
2. Regression — log(price) explained by the profile's features (ridge regression,
   fitted on all collected ads).
3. Prior — a list-price based estimate from the profile, until enough data is collected.

Asking prices are not sale prices, so comparables are weighted: ads that vanished
quickly (probably sold) count more, ads that have sat for months count less.
Prices are compared per unit when an ad sells several items.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from . import profiles
from .profiles import Profile, is_item

COMP_PRICE_TYPES = {"FIXED", "MIN_BID", "NEGOTIABLE"}


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def is_used_item(l: dict, p: Profile) -> bool:
    a = l.get("attrs") or {}
    if not is_item(a):
        return False
    if p.new_counts_as_used:
        return l.get("source") != "shop"
    return l.get("kind") != "new" and a.get("condition") != "new"


def unit_price(l: dict, p: Profile) -> float | None:
    price = l.get("price_eur")
    return price / max(1, p.quantity(l)) if price else None


def is_comparable(l: dict, today: date, p: Profile) -> bool:
    last = _d(l.get("last_seen"))
    up = unit_price(l, p)
    lo, hi = p.comp_price_range
    return (
        is_used_item(l, p)
        and not l.get("superseded_by")
        and l.get("price_type") in COMP_PRICE_TYPES
        and up is not None and lo <= up <= hi
        and (l.get("attrs") or {}).get("condition") not in p.bad_conditions
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


def run(listings: dict[str, dict], msrp: dict, profile: Profile | None = None,
        min_regression_n: int = 25, k: int = 12) -> dict:
    """Adds `fair` and `deal` to every used item. Returns model info for the website."""
    p = profile or profiles.get("cargo_bike")
    today = date.today()
    used = [l for l in listings.values() if is_used_item(l, p)]
    ctx = p.make_ctx(used)
    comps = [l for l in used if is_comparable(l, today, p)]
    F = {l["id"]: p.feature_fn(l, ctx) for l in used}

    beta = sigma = None
    if len(comps) >= min_regression_n:
        X = np.array([F[l["id"]] for l in comps])
        y = np.log([unit_price(l, p) for l in comps])
        w = np.array([sold_weight(l) for l in comps])
        beta, sigma = fit_ridge(X, y, w)

    for l in used:
        fi = F[l["id"]]
        chain = {l["id"], l.get("relist_of"), l.get("superseded_by")}
        cands = []
        for c in comps:
            if c["id"] in chain or p.group(c) != p.group(l):
                continue
            dist = p.similarity(fi, F[c["id"]])
            if dist <= p.max_similarity:
                cands.append((dist, c))
        cands.sort(key=lambda t: t[0])
        cands = cands[:k]

        used_comps = []
        if len(cands) >= 4:
            vals, ws = [], []
            for dist, c in cands:
                up = unit_price(c, p)
                if beta is not None:
                    up = up * float(np.exp(beta @ (fi - F[c["id"]])))
                vals.append(up)
                ws.append(sold_weight(c) / (1.0 + dist))
                used_comps.append({"id": c["id"], "price": c["price_eur"], "adjusted": round(up * p.quantity(l))})
            vals, ws = np.array(vals), np.array(ws)
            fair = _wquantile(vals, ws, 0.5)
            lo, hi = _wquantile(vals, ws, 0.25), _wquantile(vals, ws, 0.75)
            method = "comparables"
        elif beta is not None:
            fair = float(np.exp(beta @ fi))
            lo, hi = fair * np.exp(-sigma * 0.67), fair * np.exp(sigma * 0.67)
            method = "regression"
        else:
            fair = p.prior(l, msrp, ctx)
            lo, hi = fair * 0.82, fair * 1.18
            method = "list-price estimate"

        q = p.quantity(l)
        fair, lo, hi = fair * q, min(lo, fair * 0.95) * q, max(hi, fair * 1.05) * q
        missing = p.key_info_missing(l, ctx)
        conf = ("high" if len(used_comps) >= 8 and not missing
                else "medium" if (len(used_comps) >= 4 or beta is not None) and not missing
                else "low")
        rnd = -1 if fair >= 500 else 0
        l["fair"] = {
            "price": round(fair, rnd), "low": round(lo, rnd), "high": round(hi, rnd),
            "method": method, "confidence": conf, "n_comps": len(used_comps),
            "comps": used_comps[:6],
        }
        ask = l.get("price_eur")
        flags = list((l.get("attrs") or {}).get("red_flags") or [])
        if ask:
            pct = (fair - ask) / fair * 100
            if pct > 40:
                flags.append("price far below market: check carefully (scam/stolen/defect?)")
            bad = (l.get("attrs") or {}).get("condition") in p.bad_conditions
            l["deal"] = {"pct": round(pct, 1), "label": "needs work" if bad else deal_label(pct), "flags": flags}
        else:
            l["deal"] = {"pct": None, "label": "no price", "flags": flags}

    return {
        "n_used": len(used),
        "n_comparables": len(comps),
        "regression": None if beta is None else {
            "n": len(comps), "sigma": round(sigma, 3),
            "effects_pct": {f: round((float(np.exp(b)) - 1) * 100, 1) for f, b in zip(p.features, beta) if f != "intercept"},
        },
        "family_curve": p.curve(beta, ctx) if (beta is not None and p.curve) else [],
    }


def score_new(listings: dict[str, dict], msrp: dict, profile: Profile | None = None) -> None:
    """New items from shops (or new-condition ads): compare with the list price and the cheapest offer."""
    p = profile or profiles.get("cargo_bike")
    new = [l for l in listings.values()
           if l.get("active") and l.get("price_eur") and not is_used_item(l, p)
           and (l.get("kind") == "new" or (l.get("attrs") or {}).get("condition") == "new" or l.get("source") == "shop")
           and is_item(l.get("attrs")) is not False]
    cheapest: dict[str, int] = {}
    for l in new:
        g = p.group(l)
        l["variant_group"] = g
        cheapest[g] = min(cheapest.get(g, 10**9), l["price_eur"])
    for l in new:
        g = l["variant_group"]
        ref = msrp.get(g) or msrp.get("other")
        l["new_deal"] = {
            "vs_msrp_pct": round((ref - l["price_eur"]) / ref * 100, 1) if ref else None,
            "is_cheapest": l["price_eur"] == cheapest[g],
        }
