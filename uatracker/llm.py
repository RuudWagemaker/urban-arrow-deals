"""Claude-powered extraction of bike details from listing text.

Backends:
- api: Anthropic API (needs ANTHROPIC_API_KEY, billed per token — pennies per month)
- cli: Claude Code in headless mode (needs CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`;
       runs on your Claude Pro/Max subscription, no API billing)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from pydantic import BaseModel, ValidationError

from .extract import BikeDetails

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 6000  # listings are almost always far shorter


class ExtractedItem(BikeDetails):
    ref: str


class Batch(BaseModel):
    items: list[ExtractedItem]


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
- is_complete_bike=false for parts or accessories only (rain tent, battery, bench), wanted ads \
  ("gezocht"), rentals and lease offers.
- red_flags: only real warning signs, e.g. price far too low for the year, payment up front or \
  shipping only, no keys/proof of ownership, frame number missing, serious damage not reflected in price.
Use null / "unknown" / false when the listing doesn't say. Do not guess the model year from photos \
or from the motor generation."""


def pick_backend(cfg: dict) -> str:
    want = (cfg.get("backend") or "auto").lower()
    if want != "auto":
        return want
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and shutil.which("claude"):
        return "cli"
    return "none"


def _payload(listings: list[dict]) -> str:
    rows = [{
        "ref": l["id"],
        "title": l.get("title"),
        "asking_price_eur": l.get("price_eur"),
        "price_type": l.get("price_type"),
        "marketplace_condition": l.get("condition_raw"),
        "description": (l.get("description") or "")[:MAX_DESCRIPTION_CHARS],
    } for l in listings]
    return "Extract the details for each of these listings:\n" + json.dumps(rows, ensure_ascii=False, indent=1)


def _via_api(listings: list[dict], model: str) -> list[ExtractedItem]:
    import anthropic

    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": _payload(listings)}],
            output_format=Batch,
        )
    except anthropic.RateLimitError as e:
        log.warning("rate limited by Claude API: %s", e)
        return []
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e.message)
        return []
    except anthropic.APIConnectionError as e:
        log.warning("cannot reach Claude API: %s", e)
        return []
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log.warning("no structured output (stop_reason=%s)", resp.stop_reason)
        return []
    return resp.parsed_output.items


def _via_cli(listings: list[dict], model: str) -> list[ExtractedItem]:
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--system-prompt", SYSTEM,
        "--json-schema", json.dumps(Batch.model_json_schema()),
    ]
    try:
        proc = subprocess.run(cmd, input=_payload(listings), capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("claude CLI failed: %s", e)
        return []
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("claude CLI returned no JSON (exit %s): %s", proc.returncode, proc.stderr[-500:])
        return []
    if out.get("is_error") or not out.get("structured_output"):
        log.warning("claude CLI error: %s", str(out.get("result"))[:500])
        return []
    try:
        return Batch.model_validate(out["structured_output"]).items
    except ValidationError as e:
        log.warning("claude CLI output did not match schema: %s", e)
        return []


def extract(listings: list[dict], cfg: dict) -> dict[str, dict]:
    """Returns {listing_id: details_dict} for the listings Claude handled."""
    backend = pick_backend(cfg)
    if backend == "none" or not listings:
        if listings:
            log.info("No Claude backend configured; using rule-based extraction only.")
        return {}
    model = cfg.get("model", "claude-haiku-5-5")
    size = max(1, int(cfg.get("batch_size", 8)))
    call = _via_api if backend == "api" else _via_cli
    results: dict[str, dict] = {}
    for i in range(0, len(listings), size):
        chunk = listings[i:i + size]
        for item in call(chunk, model):
            d = item.model_dump()
            results[d.pop("ref")] = d
        log.info("Claude (%s, %s): %d/%d listings extracted", backend, model, len(results), len(listings))
    return results
