"""Turn listing text into structured bike details.

Two layers:
1. rules  – fast regexes for Dutch/English listing text; always run, no cost
2. Claude – reads the full text and fills in / corrects fields (see llm.py)
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Variant = Literal["family", "shorty", "tender", "cargo", "other", "unknown"]
Motor = Literal[
    "bosch_cargo_line_speed", "bosch_cargo_line", "bosch_performance_cx",
    "bosch_performance", "bosch_active_plus", "shimano_steps", "other", "unknown",
]
GearHub = Literal["enviolo_automatiq", "enviolo", "nexus", "rohloff", "other", "unknown"]
Condition = Literal["new", "like_new", "good", "fair", "needs_work", "unknown"]
SellerType = Literal["private", "dealer", "unknown"]


class BikeDetails(BaseModel):
    is_complete_bike: bool = Field(description="False for parts/accessories, wanted ads, rentals, lease offers")
    variant: Variant
    model_year: int | None = Field(description="Build year / model year, null if not stated")
    motor: Motor
    battery_wh: int | None = Field(description="Total battery capacity in Wh (sum if two batteries)")
    dual_battery: bool
    gear_hub: GearHub
    mileage_km: int | None = Field(description="Odometer reading in km. NOT range or top speed")
    condition: Condition
    seller_type: SellerType
    rain_tent: bool = Field(description="Rain tent / huif / regentent included")
    child_seat_extras: bool = Field(description="Extra child seat, Maxi-Cosi adapter or similar included")
    warranty: bool = Field(description="Seller offers warranty (garantie)")
    accessories: list[str] = Field(description="Other notable included extras, short English words")
    red_flags: list[str] = Field(description="Possible scam/stolen/defect signs, short English phrases. Empty if none")
    summary: str = Field(description="One short English line describing the bike")


FIELDS = list(BikeDetails.model_fields)


def text_hash(listing: dict) -> str:
    t = f"{listing.get('title','')}\n{listing.get('description','')}\n{listing.get('price_eur')}"
    return hashlib.sha1(t.encode()).hexdigest()[:16]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


EXCLUDE = r"\b(gezocht|gevraagd|wanted|te huur|verhuur|huren|lease|leasen|abonnement)\b"
ACCESSORY = (
    r"\b(regentent|huif|regenhoes|hoes|tent|kussens?|bankje|zitje|stoeltje|adapter|maxi.?cosi|"
    r"accu|batterij|lader|oplader|spatbord|standaard|wiel|velg|band|banden|slot|onderdelen|"
    r"display|motor|frame|bak|box|dekzeil|cover|spiegel|tas|bagagedrager)\b"
)
BIKE_WORDS = r"\b(bakfiets|e-?bike|elektrisch|fiets|cargo ?bike|bouwjaar|km|kilometer|gereden)\b"


def rules(listing: dict) -> dict:
    title = listing.get("title") or ""
    text = f"{title}\n{listing.get('description') or ''}"
    low = text.lower()
    price = listing.get("price_eur")
    d: dict = {}

    mentions_ua = _has(r"urban\s*-?\s*arrow", text)
    accessory_title = _has(ACCESSORY, title) and not _has(BIKE_WORDS, title)
    d["is_complete_bike"] = bool(
        mentions_ua
        and not _has(EXCLUDE, title)
        and not (accessory_title and (price is None or price < 2000))
        and not (price is not None and price < 800)
    )

    if _has(r"\btender\b", low):
        d["variant"] = "tender"
    elif _has(r"\bshorty\b", low):
        d["variant"] = "shorty"
    elif _has(r"\bcargo\s*(l|xl)\b|flatbed|\bpick.?up\b", low):
        d["variant"] = "cargo"
    elif _has(r"\bfamily\b", low):
        d["variant"] = "family"
    else:
        d["variant"] = "unknown"

    year_now = date.today().year
    years = []
    for m in re.finditer(r"(?:bouwjaar|model(?:jaar)?|jaar|uit|van|year|aankoop|gekocht in)?\s*[:\-]?\s*\b(20[12]\d)\b", low):
        y = int(m.group(1))
        if 2012 <= y <= year_now + 1:
            years.append((0 if m.group(0).strip()[:1].isalpha() else 1, y))
    d["model_year"] = sorted(years)[0][1] if years else None

    if _has(r"cargo\s*line\s*speed|s-?pedelec|speed\s*pedelec|45\s*km", low):
        d["motor"] = "bosch_cargo_line_speed"
    elif _has(r"cargo\s*line", low):
        d["motor"] = "bosch_cargo_line"
    elif _has(r"performance\s*(line\s*)?cx|\bcx\b", low):
        d["motor"] = "bosch_performance_cx"
    elif _has(r"performance(\s*line)?\b", low):
        d["motor"] = "bosch_performance"
    elif _has(r"active\s*(line\s*)?plus", low):
        d["motor"] = "bosch_active_plus"
    elif _has(r"shimano\s*steps|\bsteps\b|e6[01]00|e5000", low):
        d["motor"] = "shimano_steps"
    else:
        d["motor"] = "unknown"

    wh = [int(x) for x in re.findall(r"(\d{3,4})\s*wh\b", low) if 250 <= int(x) <= 2000]
    dual = _has(r"2\s*x\s*\d{3}\s*wh|dubbele\s*accu|twee\s*accu|2\s*accu|dual\s*battery|extra\s*accu|tweede\s*accu", low)
    d["dual_battery"] = dual
    if wh:
        d["battery_wh"] = max(wh) if not dual or max(wh) > 900 else min(sum(wh), max(wh) * 2)
    else:
        d["battery_wh"] = None

    if _has(r"automatiq|automatisch\s*schakel", low):
        d["gear_hub"] = "enviolo_automatiq"
    elif _has(r"enviolo|nuvinci|n380|traploos|stepless|continu", low):
        d["gear_hub"] = "enviolo"
    elif _has(r"nexus|\b[578]\s*versnelling", low):
        d["gear_hub"] = "nexus"
    elif _has(r"rohloff", low):
        d["gear_hub"] = "rohloff"
    else:
        d["gear_hub"] = "unknown"

    d["mileage_km"] = _mileage(low)

    cond = (listing.get("condition_raw") or "").lower()
    if _has(r"defect|kapot|opknapper|voor onderdelen|werkt niet|niet werkend", low):
        d["condition"] = "needs_work"
    elif cond == "nieuw" or listing.get("source") == "shop":
        d["condition"] = "new"
    elif "zo goed als nieuw" in cond or _has(r"als nieuw|z\.?g\.?a\.?n|nieuwstaat|nieuwe staat|zgan", low):
        d["condition"] = "like_new"
    elif cond == "gebruikt" or _has(r"goede staat|nette staat|prima staat", low):
        d["condition"] = "good"
    else:
        d["condition"] = "unknown"

    dealer = listing.get("seller_has_website") or listing.get("source") == "shop" or _has(
        r"\bgarantie\b|incl\.?\s*btw|\bbtw\b|inruil|financier|showroom|onze winkel|fietsenwinkel|"
        r"\bb\.?v\.?\b|occasion|afleverbeurt|rijklaar", low)
    d["seller_type"] = "dealer" if dealer else "private"
    d["warranty"] = _has(r"\bgarantie\b|warranty", low) and not _has(r"geen garantie|zonder garantie", low)
    d["rain_tent"] = _has(r"regentent|regen\s*tent|huif|regenhoes|regenkap|rain\s*(tent|cover)", low)
    d["child_seat_extras"] = _has(r"maxi.?cosi|babyschaal|extra\s*(zitje|stoeltje|bankje)|kinderzitje|baby\s*seat", low)

    flags = []
    if _has(r"alleen\s*verzenden|vooruit\s*betalen|betaal\s*vooraf|tikkie\s*vooraf|whatsapp\s*\+?\d", low):
        flags.append("asks for prepayment / shipping only")
    if _has(r"zonder\s*(sleutel|papieren|bon|factuur)|geen\s*(sleutel|papieren|bon|factuur)", low):
        flags.append("no keys or proof of purchase")
    d["red_flags"] = flags
    d["accessories"] = []
    d["summary"] = ""
    return d


_KM = re.compile(
    r"(?:(?:km[- ]?stand|kilometerstand|tellerstand|teller|gereden|odometer|stand)\s*[:\-]?\s*(?:ca\.?|ongeveer|±|\+/-)?\s*"
    r"(\d{1,3}(?:[.,]\d{3})+|\d{1,6})\s*(?:km|kilometer)?)"
    r"|(\d{1,3}(?:[.,]\d{3})+|\d{3,6})\s*(?:km|kilometer)\b(?!\s*(?:/|per)\s*(?:u|h|uur))"
    r"(?!\s*(?:bereik|actieradius|range))",
    re.I,
)


def _mileage(low: str) -> int | None:
    for m in _KM.finditer(low):
        raw = m.group(1) or m.group(2)
        if m.group(2):  # bare "1234 km": make sure it isn't a range ("bereik tot 80 km")
            ctx = re.split(r"[.,;\n]", low[max(0, m.start() - 30): m.start()])[-1]
            if re.search(r"bereik|actieradius|range|tot\s*$|afstand|radius|max", ctx):
                continue
        n = int(re.sub(r"[.,]", "", raw))
        if 10 <= n <= 80000:
            return n
    return None


def combine(rule_d: dict, llm_d: dict | None) -> dict:
    """LLM wins where it gives a concrete answer, rules fill the gaps."""
    if not llm_d:
        return dict(rule_d)
    out = dict(rule_d)
    for k, v in llm_d.items():
        if v in (None, "unknown", "", []):
            continue
        out[k] = v
    out["red_flags"] = sorted(set(rule_d.get("red_flags", [])) | set(llm_d.get("red_flags") or []))
    return out
