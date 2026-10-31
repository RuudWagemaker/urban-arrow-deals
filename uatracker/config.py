from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("UA_DATA_DIR", ROOT / "data"))
SITE_DIR = Path(os.environ.get("UA_SITE_DIR", ROOT / "site"))
WEB_DIR = ROOT / "web"


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.environ.get("UA_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
