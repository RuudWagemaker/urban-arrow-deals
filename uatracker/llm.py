"""Claude-powered extraction of item details from listing text (fields come from the product profile).

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
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, ValidationError, create_model

from . import profiles
from .profiles import Profile

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 6000  # listings are almost always far shorter

_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {}


def models(p: Profile) -> tuple[type[BaseModel], type[BaseModel]]:
    """(item model, batch model) for a profile: the profile's details plus `ref`."""
    if p.name not in _MODELS:
        item = create_model(f"{p.Details.__name__}Item", __base__=p.Details, ref=(str, ...))
        batch = create_model(f"{p.Details.__name__}Batch", items=(list[item], ...))
        _MODELS[p.name] = (item, batch)
    return _MODELS[p.name]


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


def _via_api(listings: list[dict], model: str, p: Profile) -> list[BaseModel]:
    import anthropic

    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=model,
            max_tokens=16000,
            system=p.system_prompt,
            messages=[{"role": "user", "content": _payload(listings)}],
            output_format=models(p)[1],
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


def _via_cli(listings: list[dict], model: str, p: Profile) -> list[BaseModel]:
    Batch = models(p)[1]
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--system-prompt", p.system_prompt,
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


def extract(listings: list[dict], cfg: dict, profile: Profile | None = None) -> dict[str, dict]:
    """Returns {listing_id: details_dict} for the listings Claude handled."""
    p = profile or profiles.get("cargo_bike")
    backend = pick_backend(cfg)
    if backend == "none" or not listings:
        if listings:
            log.info("No Claude backend configured; using rule-based extraction only.")
        return {}
    model = cfg.get("model", "claude-haiku-5-5")
    size = max(1, int(cfg.get("batch_size", 8)))
    call = _via_api if backend == "api" else _via_cli
    chunks = [listings[i:i + size] for i in range(0, len(listings), size)]
    results: dict[str, dict] = {}
    # a few requests at a time: much faster, still gentle on rate limits
    with ThreadPoolExecutor(max_workers=int(cfg.get("parallel_requests", 4))) as pool:
        for items in pool.map(lambda c: call(c, model, p), chunks):
            for item in items:
                d = item.model_dump()
                results[d.pop("ref")] = d
            log.info("Claude (%s, %s): %d/%d listings extracted", backend, model, len(results), len(listings))
    return results
