"""Profile for the Honeywell Home T6 / T6R smart thermostat."""
from __future__ import annotations

import re
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from . import Profile

EXTRACT_VERSION = 1


class ThermostatDetails(BaseModel):
    is_item: bool = Field(description="The ad sells a Honeywell (Home/Lyric) T6 or T6R thermostat itself. False for other "
                                      "models (Round, T3, T4, T5, Evohome, Chronotherm...), loose parts only, wanted ads")
    model: Literal["t6_wired", "t6r_wireless", "unknown"]
    quantity: int = Field(description="Number of T6/T6R thermostats for sale in this ad, usually 1")
    condition: Literal["sealed_new", "new_open_box", "like_new", "used", "defect", "unknown"]
    includes_receiver: bool | None = Field(description="T6R: the wireless receiver/relay box is included. "
                                                       "null if not mentioned or for the wired T6")
    table_stand: bool = Field(description="Table stand included")
    original_box: bool = Field(description="Comes in its original box/packaging")
    shipping: bool = Field(description="Seller offers to ship (verzenden)")
    seller_type: Literal["private", "dealer", "unknown"]
    red_flags: list[str] = Field(description="Possible scam/defect signs, short English phrases. Empty if none")
    summary: str = Field(description="One short English line describing what is for sale")


SYSTEM = """You extract structured facts from Dutch (sometimes Belgian or English) Marktplaats ads that \
mention the Honeywell Home T6 smart thermostat. You receive several ads as JSON and return one item \
per ad, with `ref` copied exactly.

Background:
- Two versions: the wired "T6" (t6_wired; wired to the boiler, also called "bedraad") and the \
  wireless "T6R" (t6r_wireless; "draadloos", works with a separate wireless receiver/relay box that \
  is wired to the boiler). Older names: "Lyric T6" / "Lyric T6R". If the ad only says "T6" and \
  nothing about wired or wireless or a receiver, use "unknown".
- is_item=false for other Honeywell models (Round, T3, T4, T5, Evohome, Chronotherm, Lyric Round), \
  for loose parts only (just a receiver, wall plate or table stand), for wanted ads ("gezocht") and \
  for installation services.
- condition: "sealed_new" only when the box was never opened (geseald, ongeopend, in folie); \
  "new_open_box" for new but opened/never installed; "like_new" (zo goed als nieuw), "used", "defect".
- includes_receiver: only for the T6R; true when the receiver/relay box ("ontvanger", "relais") is \
  included, false when the ad says it isn't, null when not mentioned.
- seller_type "dealer" for shops/installers (BTW/VAT invoice, garantie, "wij").
- red_flags: only real warning signs, e.g. price far too low for a sealed unit, payment up front \
  outside Marktplaats, "tikkie vooraf", many identical units at a very low price.
Use null / "unknown" / false when the ad doesn't say."""


def _has(p: str, text: str) -> bool:
    return re.search(p, text, re.I) is not None


def rules(l: dict) -> dict:
    title = l.get("title") or ""
    text = f"{title}\n{l.get('description') or ''}".lower()
    price = l.get("price_eur")
    d: dict = {}
    mentions = _has(r"(honeywell|lyric|resideo).{0,40}\bt6r?\b|\bt6r?\b.{0,40}(honeywell|lyric|thermostaat)", text)
    part_only = _has(r"\b(ontvanger|relais|receiver|tafelstandaard|wandplaat|muurplaat|montageplaat)\b", title) \
        and not _has(r"thermostaat|thermostat|\bset\b|\bkit\b", title)
    other_model = _has(r"\b(round|evohome|chronotherm|t3r?|t4r?|t5)\b", title) and not _has(r"\bt6r?\b", title)
    d["is_item"] = bool(
        mentions and not part_only and not other_model
        and not _has(r"\b(gezocht|gevraagd|wanted|installatie|monteren)\b", title)
        and not (price is not None and price < 25)
    )
    if _has(r"\bt6r\b|draadloos|wireless|ontvanger|relais", text):
        d["model"] = "t6r_wireless"
    elif _has(r"bedraad|wired|\bkabel", text):
        d["model"] = "t6_wired"
    else:
        d["model"] = "unknown"
    m = re.search(r"\b([2-9])\s*(x|stuks|st\.)\b", text)
    d["quantity"] = int(m.group(1)) if m else 1

    cond = (l.get("condition_raw") or "").lower()
    if _has(r"defect|kapot|werkt niet|voor onderdelen", text):
        d["condition"] = "defect"
    elif _has(r"geseald|sealed|ongeopend|niet geopend|in folie|seal", text):
        d["condition"] = "sealed_new"
    elif cond == "nieuw" or _has(r"nooit gebruikt|nooit geïnstalleerd|nieuw in doos", text):
        d["condition"] = "new_open_box"
    elif "zo goed als nieuw" in cond or _has(r"als nieuw|z\.?g\.?a\.?n", text):
        d["condition"] = "like_new"
    elif cond == "gebruikt" or _has(r"gebruikt", text):
        d["condition"] = "used"
    else:
        d["condition"] = "unknown"
    if d["model"] == "t6r_wireless":
        if _has(r"zonder\s+(ontvanger|relais|receiver)|(ontvanger|relais)\s+(ontbreekt|niet aanwezig)", text):
            d["includes_receiver"] = False
        elif _has(r"ontvanger|relais|receiver|bdr", text):
            d["includes_receiver"] = True
        else:
            d["includes_receiver"] = None
    else:
        d["includes_receiver"] = None
    d["table_stand"] = _has(r"tafelstandaard|tafel\s*standaard|tafelvoet|table\s*stand|standaard", text)
    d["original_box"] = _has(r"\bdoos\b|verpakking|\bbox\b|originele\s+doos", text)
    d["shipping"] = _has(r"verzend|verzond|opsturen|kan verstuurd|postnl", text)
    d["seller_type"] = "dealer" if (l.get("seller_has_website") or _has(r"\bbtw\b|factuur|garantie|installateur|\bb\.?v\.?\b", text)) else "private"
    flags = []
    if _has(r"tikkie\s*vooraf|vooruit\s*betalen|betaal\s*vooraf|alleen\s*verzenden", text):
        flags.append("asks for payment up front")
    d["red_flags"] = flags
    d["summary"] = ""
    return d


def combine(rule_d: dict, llm_d: dict | None) -> dict:
    out = dict(rule_d)
    if llm_d:
        for k, v in llm_d.items():
            if v in (None, "unknown", "", []):
                continue
            out[k] = v
        out["red_flags"] = sorted(set(rule_d.get("red_flags", [])) | set(llm_d.get("red_flags") or []))
    q = out.get("quantity") or 1
    out["quantity"] = q if 1 <= q <= 20 else 1
    out["is_item"] = bool(out.get("is_item"))
    # Honeywell's naming: "T6" is the wired version, "T6R" the wireless one. An ad that only
    # says "T6" (no wireless, no receiver) is treated as wired, and marked as an assumption.
    out["model_assumed"] = False
    if (out.get("model") or "unknown") == "unknown" and out["is_item"]:
        out["model"] = "t6_wired"
        out["model_assumed"] = True
    return out


COND_RANK = {"sealed_new": 0, "new_open_box": 1, "like_new": 1.5, "used": 2.5, "unknown": 2.2, "defect": 6}
COND_FACTOR = {"sealed_new": 0.75, "new_open_box": 0.68, "like_new": 0.6, "used": 0.5, "unknown": 0.5, "defect": 0.2}
COND_LABEL = {"sealed_new": "sealed new", "new_open_box": "new, box opened", "like_new": "as new",
              "used": "used", "defect": "defect"}
MODEL_LABEL = {"t6_wired": "T6 (wired)", "t6r_wireless": "T6R (wireless)"}

FEATURES = ["intercept", "t6r", "unknown_model", "sealed", "open_box", "like_new", "defect",
            "receiver_missing", "table_stand", "original_box", "dealer"]
IX = {f: k for k, f in enumerate(FEATURES)}


def group(l: dict) -> str:
    return (l.get("attrs") or {}).get("model") or "unknown"


def feature_fn(l: dict, ctx: dict) -> np.ndarray:
    a = l.get("attrs") or {}
    c = a.get("condition") or "unknown"
    v = {
        "intercept": 1.0,
        "t6r": a.get("model") == "t6r_wireless",
        "unknown_model": (a.get("model") or "unknown") == "unknown",
        "sealed": c == "sealed_new", "open_box": c == "new_open_box", "like_new": c == "like_new",
        "defect": c == "defect",
        "receiver_missing": a.get("model") == "t6r_wireless" and a.get("includes_receiver") is False,
        "table_stand": bool(a.get("table_stand")), "original_box": bool(a.get("original_box")),
        "dealer": a.get("seller_type") == "dealer",
    }
    return np.array([float(v[f]) for f in FEATURES])


def _cond_rank(f: np.ndarray) -> float:
    for name, rank in (("sealed", 0), ("open_box", 1), ("like_new", 1.5), ("defect", 6)):
        if f[IX[name]]:
            return rank
    return 2.5


def similarity(fi: np.ndarray, fj: np.ndarray) -> float:
    return (
        abs(_cond_rank(fi) - _cond_rank(fj))
        + 1.5 * (fi[IX["receiver_missing"]] != fj[IX["receiver_missing"]])
        + 0.3 * (fi[IX["table_stand"]] != fj[IX["table_stand"]])
        + 0.3 * (fi[IX["original_box"]] != fj[IX["original_box"]])
        + 0.5 * (fi[IX["dealer"]] != fj[IX["dealer"]])
    )


def prior(l: dict, msrp: dict, ctx: dict) -> float:
    a = l.get("attrs") or {}
    base = msrp.get(a.get("model") or "unknown") or msrp.get("unknown") or 175
    f = COND_FACTOR.get(a.get("condition") or "unknown", 0.5)
    if a.get("model") == "t6r_wireless" and a.get("includes_receiver") is False:
        f *= 0.75
    return base * f


def key_info_missing(l: dict, ctx: dict) -> bool:
    a = l.get("attrs") or {}
    return (a.get("model") or "unknown") == "unknown" or (a.get("condition") or "unknown") == "unknown"


def quantity(l: dict) -> int:
    return int((l.get("attrs") or {}).get("quantity") or 1)


def facts(l: dict) -> str:
    a = l.get("attrs") or {}
    bits = [
        f"{a['quantity']}×" if (a.get("quantity") or 1) > 1 else None,
        MODEL_LABEL.get(a.get("model"), "") + (" (assumed)" if a.get("model_assumed") else "") or None,
        COND_LABEL.get(a.get("condition")),
        "with receiver" if a.get("includes_receiver") else "no receiver" if a.get("includes_receiver") is False else None,
        "table stand" if a.get("table_stand") else None,
        "in box" if a.get("original_box") else None,
        "ships" if a.get("shipping") else None,
        "dealer" if a.get("seller_type") == "dealer" else None,
        f"{l['distance_km']:.0f} km away" if l.get("distance_km") is not None else l.get("city"),
    ]
    return " · ".join(b for b in bits if b)


def short_name(l: dict) -> str:
    a = l.get("attrs") or {}
    name = {"t6_wired": "Honeywell T6", "t6r_wireless": "Honeywell T6R"}.get(a.get("model"), "Honeywell T6")
    q = a.get("quantity") or 1
    return f"{q}× {name}" if q > 1 else name


PROFILE = Profile(
    name="thermostat", item="thermostat", Details=ThermostatDetails, system_prompt=SYSTEM,
    extract_version=EXTRACT_VERSION, rules=rules, combine=combine,
    features=FEATURES, feature_fn=feature_fn, similarity=similarity, group=group, prior=prior,
    facts=facts, short_name=short_name, key_info_missing=key_info_missing, quantity=quantity,
    comp_price_range=(15, 500), min_item_price=25, max_similarity=3.0, new_counts_as_used=True,
)
