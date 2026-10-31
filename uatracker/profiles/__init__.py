"""Product profiles: everything that differs between the kinds of products the tracker follows.

A profile says which details Claude extracts from an ad, which text rules run first,
how two ads are compared for the fair price, and how an ad is summarised in alerts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from pydantic import BaseModel


@dataclass
class Profile:
    name: str
    item: str                                   # "bike", "thermostat" (for messages)
    Details: type[BaseModel]                    # what Claude extracts per ad
    system_prompt: str
    extract_version: int
    rules: Callable[[dict], dict]               # quick text rules, always run
    combine: Callable[[dict, dict | None], dict]
    features: list[str]                         # price-model features, first is "intercept"
    feature_fn: Callable[[dict, dict], np.ndarray]
    similarity: Callable[[np.ndarray, np.ndarray], float]
    group: Callable[[dict], str]                # only ads in the same group are compared
    prior: Callable[[dict, dict, dict], float]  # price guess before there is enough data
    facts: Callable[[dict], str]                # one-line summary for alerts
    short_name: Callable[[dict], str]           # e.g. "2023 Family", "T6R"
    make_ctx: Callable[[list[dict]], dict] = lambda used: {}
    key_info_missing: Callable[[dict, dict], bool] = lambda l, ctx: False
    quantity: Callable[[dict], int] = lambda l: 1
    curve: Callable | None = None               # optional depreciation curve for the site
    comp_price_range: tuple[float, float] = (0, 1e9)
    min_item_price: float = 0                   # cheaper ads are accessories: rules only, no Claude
    max_similarity: float = 4.0
    new_counts_as_used: bool = False            # True when "new in box" ads compete with used ones
    bad_conditions: tuple[str, ...] = ("needs_work", "defect")
    extra: dict = field(default_factory=dict)


def is_item(attrs: dict | None) -> bool:
    """Is this ad for the product itself (not an accessory, wanted ad, other brand...)?"""
    a = attrs or {}
    return bool(a.get("is_item", a.get("is_complete_bike", False)))


def get(name: str) -> Profile:
    if name == "cargo_bike":
        from .cargo_bike import PROFILE
    elif name == "thermostat":
        from .thermostat import PROFILE
    else:
        raise ValueError(f"unknown product profile {name!r}")
    return PROFILE
