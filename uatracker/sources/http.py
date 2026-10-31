from __future__ import annotations

import json
import random
import re
import time
from html import unescape

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class SourceError(RuntimeError):
    pass


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    })
    return s


def polite_sleep(delay: list[float] | tuple[float, float] | None) -> None:
    lo, hi = delay or (1.5, 4.0)
    time.sleep(random.uniform(lo, hi))


def get(s: requests.Session, url: str, **kw) -> requests.Response:
    for attempt in range(3):
        try:
            r = s.get(url, timeout=30, **kw)
        except requests.RequestException as e:
            err = e
        else:
            if r.status_code == 200:
                return r
            if r.status_code in (403, 401):
                raise SourceError(f"{url} -> HTTP {r.status_code} (blocked?)")
            err = SourceError(f"{url} -> HTTP {r.status_code}")
        time.sleep(5 * (attempt + 1))
    raise SourceError(str(err))


_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)


def json_ld_objects(html: str) -> list[dict]:
    """All schema.org objects in a page, flattened (handles @graph and lists)."""
    out: list[dict] = []

    def walk(x):
        if isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            out.append(x)
            if "@graph" in x:
                walk(x["@graph"])
            if "hasVariant" in x:
                walk(x["hasVariant"])

    for block in _LD_RE.findall(html):
        try:
            walk(json.loads(block.strip()))
        except json.JSONDecodeError:
            continue
    return out


def meta_content(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)',
        html, re.I,
    )
    return unescape(m.group(1)) if m else None


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", unescape(text)).strip()
