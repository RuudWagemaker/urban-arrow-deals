"""Alerts for new good deals and price drops. Two channels, use either or both:

ntfy (phone push notifications, no account or password):
  NTFY_TOPIC          a long random channel name; subscribe to the same name in the ntfy app

Email through Gmail:
  GMAIL_ADDRESS       your Gmail address (mail is sent from and to this address)
  GMAIL_APP_PASSWORD  a Gmail app password (not your normal password)
  EMAIL_TO            optional, to send to a different address
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

import requests

from .profiles import Profile, get as get_profile, is_item

log = logging.getLogger(__name__)

MAX_NEW = 15
MAX_DROPS = 10
MAX_PUSH = 10  # individual phone notifications per scan; the rest are summarised

def eur(n) -> str:
    return "€" + f"{n:,.0f}".replace(",", ".") if n is not None else "–"


def pick(listings: dict[str, dict], cfg: dict, profile=None) -> tuple[list[dict], list[dict]]:
    """New good deals not alerted before, and price drops since the last alert."""
    min_pct = cfg.get("min_deal_pct", 10)
    max_km = cfg.get("max_distance_km", 9999)

    min_conf = {"low": 0, "medium": 1, "high": 2}[cfg.get("min_confidence", "medium")]
    bad = profile.bad_conditions if profile else ("needs_work", "defect")

    def ok(l):
        d, a, f = l.get("deal") or {}, l.get("attrs") or {}, l.get("fair") or {}
        dist = l.get("distance_km")
        return (l.get("active") and l.get("price_eur") and d.get("pct") is not None
                and d["pct"] >= min_pct and (dist is None or dist <= max_km)
                and is_item(a) and a.get("condition") not in bad
                and {"low": 0, "medium": 1, "high": 2}.get(f.get("confidence"), 0) >= min_conf)

    new, drops = [], []
    for l in listings.values():
        if not ok(l):
            continue
        sent = l.get("alerted_price")
        if sent is None:
            new.append(l)
        elif cfg.get("notify_price_drops", True) and l["price_eur"] < sent:
            drops.append(l)
    # listings without warning signs first, then by size of the discount
    key = lambda l: (bool((l.get("attrs") or {}).get("red_flags")), -(l["deal"]["pct"]))
    return sorted(new, key=key), sorted(drops, key=key)


_P = None  # profile of the product being alerted (set in send)


def _prof() -> Profile:
    return _P or get_profile("cargo_bike")


def _facts(l: dict) -> str:
    return _prof().facts(l)


def _row_html(l: dict, was: float | None = None) -> str:
    f, d = l.get("fair") or {}, l.get("deal") or {}
    img = (f'<img src="{escape(l["image"])}" width="120" style="border-radius:6px;display:block" alt="">'
           if l.get("image") else "")
    price = f'<b style="font-size:18px">{eur(l["price_eur"])}</b>'
    if was:
        price += f' <s style="color:#888">{eur(was)}</s>'
    flags = "".join(f'<div style="color:#b8412f;font-size:13px">⚠️ {escape(x)}</div>' for x in d.get("flags") or [])
    return f"""
<tr><td style="padding:10px 12px 10px 0;vertical-align:top;width:120px">{img}</td>
<td style="padding:10px 0;vertical-align:top;font-family:system-ui,Arial,sans-serif;font-size:14px;color:#1c2420">
  <a href="{escape(l['url'])}" style="color:#1c2420;font-weight:600;text-decoration:none">{escape(l.get('title', ''))}</a>
  <div style="color:#5d6b64;font-size:13px;margin:2px 0 6px">{escape(_facts(l))}</div>
  {price} &nbsp; <span style="background:#127a3e;color:#fff;border-radius:10px;padding:2px 8px;font-size:12px">{d.get('pct', 0):.0f}% below fair</span>
  <div style="color:#5d6b64;font-size:13px">fair ≈ {eur(f.get('price'))} ({eur(f.get('low'))}–{eur(f.get('high'))}), {escape(f.get('confidence', ''))} confidence</div>
  {flags}
  <a href="{escape(l['url'])}" style="color:#1f7a4d;font-size:13px">Open listing →</a>
</td></tr>"""


def _row_text(l: dict, was: float | None = None) -> str:
    f, d = l.get("fair") or {}, l.get("deal") or {}
    head = f"{eur(l['price_eur'])}" + (f" (was {eur(was)})" if was else "")
    lines = [f"{head} — {d.get('pct', 0):.0f}% below fair (≈ {eur(f.get('price'))})",
             l.get("title", ""), _facts(l)]
    lines += [f"! {x}" for x in d.get("flags") or []]
    lines.append(l["url"])
    return "\n".join(lines)


def build(new: list[dict], drops: list[dict], site_url: str | None, first: bool,
          product: str = "Urban Arrow") -> tuple[str, str, str]:
    n_new, n_drop = len(new), len(drops)
    if first:
        subject = f"{product} tracker is running: {n_new} good deals right now"
        intro = (f"Your tracker is set up. These are the best of the {n_new} current listings that are at least "
                 f"10% below their fair price. From now on you'll only get an email when something new appears.")
    else:
        parts = []
        if n_new:
            parts.append(f"{n_new} new deal{'s' if n_new != 1 else ''}")
        if n_drop:
            parts.append(f"{n_drop} price drop{'s' if n_drop != 1 else ''}")
        top = (new or drops)[0]
        subject = f"{product}: {' + '.join(parts)} — best {eur(top['price_eur'])}, {top['deal']['pct']:.0f}% below fair"
        intro = ""

    def hist_was(l):
        h = [p for _, p in (l.get("price_history") or []) if p]
        return l.get("alerted_price") or (h[-2] if len(h) >= 2 else None)

    html = ['<div style="max-width:640px;font-family:system-ui,Arial,sans-serif;color:#1c2420">']
    text = []
    if intro:
        html.append(f'<p style="font-size:14px">{escape(intro)}</p>')
        text.append(intro + "\n")
    if new:
        html.append(f'<h2 style="font-size:17px;margin:16px 0 0">{"Best deals now" if first else "New deals"}</h2><table cellspacing="0" cellpadding="0">')
        html += [_row_html(l) for l in new[:MAX_NEW]]
        html.append("</table>")
        text.append("NEW DEALS\n\n" + "\n\n".join(_row_text(l) for l in new[:MAX_NEW]))
    if drops:
        html.append('<h2 style="font-size:17px;margin:16px 0 0">Price drops</h2><table cellspacing="0" cellpadding="0">')
        html += [_row_html(l, hist_was(l)) for l in drops[:MAX_DROPS]]
        html.append("</table>")
        text.append("PRICE DROPS\n\n" + "\n\n".join(_row_text(l, hist_was(l)) for l in drops[:MAX_DROPS]))
    if site_url:
        html.append(f'<p style="margin-top:20px"><a href="{escape(site_url)}" style="color:#1f7a4d;font-weight:600">See all listings →</a></p>')
        text.append(f"All listings: {site_url}")
    html.append('<p style="color:#888;font-size:12px">Fair prices are estimates from asking prices of similar listings. '
                'Always check the item and proof of purchase before paying.</p></div>')
    return subject, "\n\n".join(text), "".join(html)


def _push_one(l: dict, was: float | None) -> dict:
    f, d, a = l.get("fair") or {}, l.get("deal") or {}, l.get("attrs") or {}
    what = _prof().short_name(l)
    if was:
        title = f"Price drop: {eur(l['price_eur'])} (was {eur(was)}) · {what}"
    else:
        title = f"{eur(l['price_eur'])} · {what} · {d.get('pct', 0):.0f}% below fair"
    lines = [l.get("title", ""), _facts(l),
             f"Fair ≈ {eur(f.get('price'))} ({eur(f.get('low'))}–{eur(f.get('high'))}), {f.get('confidence', '')} confidence"]
    lines += [f"⚠️ {x}" for x in d.get("flags") or []]
    msg = {
        "title": title[:250],
        "message": "\n".join(lines),
        "tags": ["chart_with_downwards_trend"] if was else (["bike"] if _prof().item == "bike" else ["moneybag"]),
        "priority": 4 if d.get("pct", 0) >= 20 else 3,
        "click": l["url"],
        "actions": [{"action": "view", "label": "Open listing", "url": l["url"]}],
    }
    if l.get("image"):
        msg["attach"] = l["image"]
    return msg


def _send_ntfy(topic: str, new: list[dict], drops: list[dict], site_url: str | None, first: bool, was,
               product: str = "Urban Arrow") -> bool:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    msgs = []
    if first:
        best = new[0] if new else drops[0]
        msgs.append({"title": f"{product} tracker is running: {len(new)} good deals now",
                     "message": f"Best: {eur(best['price_eur'])}, {best['deal']['pct']:.0f}% below fair. "
                                "From now on you'll get a notification for each new deal.",
                     "tags": ["white_check_mark"], "click": site_url or best["url"]})
        msgs += [_push_one(l, None) for l in new[:5]]
    else:
        items = [(l, None) for l in new] + [(l, was(l)) for l in drops]
        msgs += [_push_one(l, w) for l, w in items[:MAX_PUSH]]
        if len(items) > MAX_PUSH:
            msgs.append({"title": f"+{len(items) - MAX_PUSH} more {product} deals",
                         "message": "See them all on the site.", "click": site_url or ""})
    ok = False
    for m in msgs:
        m["topic"] = topic
        if not m.get("click"):
            m.pop("click", None)
        try:
            r = requests.post(server, json=m, timeout=20)
            if r.status_code == 200:
                ok = True
            else:
                log.warning("ntfy: HTTP %s %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("ntfy failed: %s", e)
    if ok:
        log.info("ntfy: sent %d notifications", len(msgs))
    return ok


def _send_email(sender: str, password: str, to: str, new, drops, site_url, first, product="Urban Arrow") -> bool:
    subject, text, html = build(new, drops, site_url, first, product)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Deal tracker <{sender}>", to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(sender, password)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.warning("email failed: %s", e)
        return False
    log.info("email sent: %s", subject)
    return True


def send(listings: dict[str, dict], cfg: dict, site_url: str | None = None,
         product: str = "Urban Arrow", profile: Profile | None = None) -> None:
    global _P
    _P = profile or get_profile("cargo_bike")
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    sender = os.environ.get("GMAIL_ADDRESS")
    password = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    new, drops = pick(listings, cfg, _P)
    first = not any("alerted_price" in l for l in listings.values())
    log.info("alerts: %d new deals, %d price drops%s", len(new), len(drops), " (first alert)" if first else "")
    if not (topic or (sender and password)):
        log.info("no alert channel configured (NTFY_TOPIC or GMAIL_*); skipping alerts")
        return
    if not (new or drops):
        return

    def was(l):
        h = [p for _, p in (l.get("price_history") or []) if p]
        return l.get("alerted_price") or (h[-2] if len(h) >= 2 else None)

    sent = False
    if topic:
        sent |= _send_ntfy(topic, new, drops, site_url, first, was, product)
    if sender and password:
        sent |= _send_email(sender, password, os.environ.get("EMAIL_TO") or sender, new, drops, site_url, first, product)
    if sent:
        # everything that qualified now counts as notified, so nothing repeats next scan
        for l in new + drops:
            l["alerted_price"] = l["price_eur"]
