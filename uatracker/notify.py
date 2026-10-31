"""Email alerts for new good deals and price drops, sent through Gmail.

Needs two repository secrets:
  GMAIL_ADDRESS       your Gmail address (mail is sent from and to this address)
  GMAIL_APP_PASSWORD  a Gmail app password (not your normal password)
Optional: EMAIL_TO to send to a different address.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

log = logging.getLogger(__name__)

MAX_NEW = 15
MAX_DROPS = 10

MOTOR = {
    "bosch_cargo_line_speed": "Cargo Line Speed", "bosch_cargo_line": "Cargo Line",
    "bosch_performance_cx": "Performance CX", "bosch_performance": "Performance",
    "bosch_active_plus": "Active Plus", "shimano_steps": "Shimano STEPS",
}


def eur(n) -> str:
    return "€" + f"{n:,.0f}".replace(",", ".") if n is not None else "–"


def pick(listings: dict[str, dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """New good deals not alerted before, and price drops since the last alert."""
    min_pct = cfg.get("min_deal_pct", 10)
    max_km = cfg.get("max_distance_km", 9999)

    min_conf = {"low": 0, "medium": 1, "high": 2}[cfg.get("min_confidence", "medium")]

    def ok(l):
        d, a, f = l.get("deal") or {}, l.get("attrs") or {}, l.get("fair") or {}
        dist = l.get("distance_km")
        return (l.get("active") and l.get("price_eur") and d.get("pct") is not None
                and d["pct"] >= min_pct and (dist is None or dist <= max_km)
                and a.get("is_complete_bike") and a.get("condition") != "needs_work"
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


def _facts(l: dict) -> str:
    a = l.get("attrs") or {}
    bits = [
        str(a["model_year"]) if a.get("model_year") else None,
        (a.get("variant") or "").title() if a.get("variant") not in (None, "unknown", "other") else None,
        MOTOR.get(a.get("motor")),
        f"{a['battery_wh']} Wh" if a.get("battery_wh") else None,
        f"{a['mileage_km']:,} km".replace(",", ".") if a.get("mileage_km") else None,
        "rain tent" if a.get("rain_tent") else None,
        "dealer" if a.get("seller_type") == "dealer" else None,
        f"{l['distance_km']:.0f} km away" if l.get("distance_km") is not None else l.get("city"),
    ]
    return " · ".join(b for b in bits if b)


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
  <a href="{escape(l['url'])}" style="color:#1f7a4d;font-size:13px">Open on Marktplaats →</a>
</td></tr>"""


def _row_text(l: dict, was: float | None = None) -> str:
    f, d = l.get("fair") or {}, l.get("deal") or {}
    head = f"{eur(l['price_eur'])}" + (f" (was {eur(was)})" if was else "")
    lines = [f"{head} — {d.get('pct', 0):.0f}% below fair (≈ {eur(f.get('price'))})",
             l.get("title", ""), _facts(l)]
    lines += [f"! {x}" for x in d.get("flags") or []]
    lines.append(l["url"])
    return "\n".join(lines)


def build(new: list[dict], drops: list[dict], site_url: str | None, first: bool) -> tuple[str, str, str]:
    n_new, n_drop = len(new), len(drops)
    if first:
        subject = f"Urban Arrow tracker is running: {n_new} good deals right now"
        intro = (f"Your tracker is set up. These are the best of the {n_new} current listings that are at least "
                 f"10% below their fair price. From now on you'll only get an email when something new appears.")
    else:
        parts = []
        if n_new:
            parts.append(f"{n_new} new deal{'s' if n_new != 1 else ''}")
        if n_drop:
            parts.append(f"{n_drop} price drop{'s' if n_drop != 1 else ''}")
        top = (new or drops)[0]
        subject = f"Urban Arrow: {' + '.join(parts)} — best {eur(top['price_eur'])}, {top['deal']['pct']:.0f}% below fair"
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
                'Always check the bike, frame number and proof of purchase.</p></div>')
    return subject, "\n\n".join(text), "".join(html)


def send(listings: dict[str, dict], cfg: dict, site_url: str | None = None) -> None:
    sender = os.environ.get("GMAIL_ADDRESS")
    password = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    to = os.environ.get("EMAIL_TO") or sender
    new, drops = pick(listings, cfg)
    first = not any("alerted_price" in l for l in listings.values())
    log.info("alerts: %d new deals, %d price drops%s", len(new), len(drops), " (first email)" if first else "")
    if not (sender and password) or not (new or drops):
        if not (sender and password):
            log.info("email not configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD); skipping alerts")
        return

    subject, text, html = build(new, drops, site_url, first)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Urban Arrow tracker <{sender}>", to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(sender, password)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.warning("email failed: %s", e)
        return
    log.info("email sent: %s", subject)
    # everything that qualified now counts as notified, so the first email doesn't repeat tomorrow
    for l in new + drops:
        l["alerted_price"] = l["price_eur"]
