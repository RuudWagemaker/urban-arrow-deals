(() => {
  "use strict";

  const eur = (n) => n == null ? "–" : "€" + Math.round(n).toLocaleString("nl-NL");
  const $ = (s, el = document) => el.querySelector(s);
  const VARIANT = { family: "Family", shorty: "Shorty", tender: "Tender", cargo: "Cargo", other: "Other", unknown: "Variant ?" };
  const MOTOR = {
    bosch_cargo_line_speed: "Cargo Line Speed", bosch_cargo_line: "Cargo Line", bosch_performance_cx: "Performance CX",
    bosch_performance: "Performance", bosch_active_plus: "Active Plus", shimano_steps: "Shimano STEPS",
  };
  const HUB = { enviolo_automatiq: "Enviolo AutomatiQ", enviolo: "Enviolo", nexus: "Nexus", rohloff: "Rohloff" };
  const COND = { new: "new", like_new: "as new", good: "good", fair: "fair", needs_work: "needs work" };
  const LABEL_CLASS = { "needs work": "pricey", "great deal": "great", "good deal": "good", "fair price": "fair", "a bit pricey": "pricey", "overpriced": "over" };
  const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 864e5);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  let DATA = null;
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem("ua:" + k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem("ua:" + k, JSON.stringify(v)); } catch {} },
  };

  // ---------- tabs ----------
  const tabs = document.querySelectorAll(".tabs button");
  function showTab(name) {
    tabs.forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + name));
    if (name === "market") renderMarket();
    store.set("tab", name);
  }
  tabs.forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

  // ---------- cards ----------
  function chips(l) {
    const a = l.attrs || {};
    const c = [];
    if (a.model_year) c.push(String(a.model_year));
    c.push(VARIANT[a.variant] || "Variant ?");
    if (MOTOR[a.motor]) c.push(MOTOR[a.motor]);
    if (a.battery_wh) c.push(a.battery_wh + " Wh" + (a.dual_battery ? " (2 batteries)" : ""));
    if (HUB[a.gear_hub]) c.push(HUB[a.gear_hub]);
    if (a.mileage_km != null) c.push(a.mileage_km.toLocaleString("nl-NL") + " km");
    if (COND[a.condition] && l.kind !== "new") c.push(COND[a.condition]);
    if (a.rain_tent) c.push("rain tent");
    if (a.child_seat_extras) c.push("child seat extras");
    if (a.seller_type === "dealer") c.push(a.warranty ? "dealer + warranty" : "dealer");
    const where = l.shop || l.city;
    const dist = l.distance_km != null ? ` · ${Math.round(l.distance_km)} km` : "";
    const meta = [];
    if (where) meta.push(where + dist);
    if (l.first_seen) {
      const d = daysBetween(l.first_seen, l.active ? DATA.generated_at.slice(0, 10) : (l.removed_on || l.last_seen));
      meta.push(l.active ? (d <= 0 ? "listed today" : `listed ${d} d`) : `gone after ${d} d`);
    }
    return [c, meta];
  }

  function priceHistory(l) {
    const h = (l.price_history || []).filter((p) => p[1]);
    if (h.length < 2) return null;
    const first = h[0][1], last = h[h.length - 1][1];
    return last < first ? first : null;
  }

  function sparkline(l) {
    const h = (l.price_history || []).filter((p) => p[1]);
    if (h.length < 2) return "";
    const W = 360, H = 60, pad = 6;
    const xs = h.map((p) => new Date(p[0]).getTime()), ys = h.map((p) => p[1]);
    const end = new Date(l.last_seen || h[h.length - 1][0]).getTime();
    const x0 = Math.min(...xs), x1 = Math.max(end, x0 + 864e5);
    const y0 = Math.min(...ys) * 0.97, y1 = Math.max(...ys) * 1.03;
    const X = (x) => pad + (x - x0) / (x1 - x0) * (W - 2 * pad), Y = (y) => H - pad - (y - y0) / (y1 - y0) * (H - 2 * pad);
    let d = `M${X(xs[0])},${Y(ys[0])}`;
    for (let i = 1; i < h.length; i++) d += ` H${X(xs[i])} V${Y(ys[i])}`;
    d += ` H${X(end)}`;
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="Price history"><path d="${d}" fill="none" stroke="${css("--accent")}" stroke-width="2"/></svg>`;
  }

  function card(l, mode) {
    const el = $("#card").content.firstElementChild.cloneNode(true);
    if (!l.active) el.classList.add("inactive");
    el.querySelectorAll("a.thumb, a.title").forEach((a) => (a.href = l.url));
    const img = $("img", el);
    if (l.image) img.src = l.image; else img.remove();
    $(".title", el).textContent = l.title;

    const badge = $(".badge", el);
    if (mode === "used") {
      const d = l.deal || {};
      badge.textContent = !l.active ? "sold / removed" : d.pct == null ? "no price" : d.label === "needs work" ? "needs work" : `${d.label} ${d.pct > 0 ? "−" : "+"}${Math.abs(Math.round(d.pct))}%`;
      badge.className = "badge " + (l.active ? (LABEL_CLASS[d.label] || "none") : "none");
    } else {
      const n = l.new_deal || {};
      badge.textContent = n.is_cheapest ? "cheapest new" : (n.vs_msrp_pct > 0 ? `−${Math.round(n.vs_msrp_pct)}% vs. list` : "new");
      badge.className = "badge " + (n.is_cheapest ? "great" : n.vs_msrp_pct >= 5 ? "good" : "fair");
    }

    const [c, meta] = chips(l);
    $(".chips", el).innerHTML = c.map((t) => `<span class="chip">${esc(t)}</span>`).join("") +
      meta.map((t) => `<span class="chip dim">${esc(t)}</span>`).join("");

    const was = priceHistory(l);
    const ptype = l.price_type && l.price_type !== "FIXED" ? ` <span class="fairtxt">(${esc(priceType(l.price_type))})</span>` : "";
    let pb = `<span class="ask">${eur(l.price_eur)}${was ? `<s>${eur(was)}</s>` : ""}</span>${ptype}`;
    const f = l.fair;
    if (mode === "used" && f) {
      pb += `<span class="fairtxt">fair ≈ <b>${eur(f.price)}</b> (${eur(f.low)}–${eur(f.high)}) · ${f.confidence} confidence</span>`;
      if (l.price_eur) {
        const lo = Math.min(f.low, l.price_eur) * 0.92, hi = Math.max(f.high, l.price_eur) * 1.08;
        const P = (v) => ((v - lo) / (hi - lo) * 100).toFixed(1) + "%";
        pb += `<div class="range" title="Green band = fair range, dot = asking price"><div class="band" style="left:${P(f.low)};width:calc(${P(f.high)} - ${P(f.low)})"></div><div class="mid" style="left:${P(f.price)}"></div><div class="dot" style="left:${P(l.price_eur)}"></div></div>`;
      }
    } else if (mode === "new" && l.new_deal && l.new_deal.vs_msrp_pct != null) {
      pb += `<span class="fairtxt">list price ≈ ${eur(DATA.msrp_estimate[l.variant_group])}${l.in_stock === false ? " · not in stock" : ""}</span>`;
    }
    $(".pricebar", el).innerHTML = pb;

    const flags = (l.deal && l.deal.flags) || [];
    $(".flags", el).innerHTML = flags.map((x) => "⚠️ " + esc(x)).join("<br>");

    const det = [];
    if (l.attrs && l.attrs.summary) det.push(`<div>${esc(l.attrs.summary)}</div>`);
    if (l.attrs && l.attrs.accessories && l.attrs.accessories.length) det.push(`<div><b>Extras:</b> ${esc(l.attrs.accessories.join(", "))}</div>`);
    const sp = sparkline(l);
    if (sp) det.push(`<div><b>Price history</b>${sp}</div>`);
    if (f && f.comps && f.comps.length) {
      const byId = Object.fromEntries(DATA.used.map((u) => [u.id, u]));
      det.push(`<div><b>Compared with</b> (${f.n_comps} similar bikes, method: ${esc(f.method)})<table>${f.comps.map((cp) => {
        const u = byId[cp.id];
        const name = u ? `${u.attrs.model_year || "?"} · ${esc(u.title.slice(0, 48))}` : esc(cp.id);
        return `<tr><td>${u ? `<a href="${esc(u.url)}" target="_blank" rel="noopener">${name}</a>` : name}</td><td>${eur(cp.price)}</td><td class="muted">≈ ${eur(cp.adjusted)} for this bike</td></tr>`;
      }).join("")}</table></div>`);
    } else if (f) {
      det.push(`<div class="muted">Fair price from: ${esc(f.method)}.</div>`);
    }
    if (det.length) $(".detail", el).innerHTML = det.join(""); else $(".more", el).remove();
    return el;
  }

  function priceType(t) {
    return { MIN_BID: "bidding from", FAST_BID: "bids", NEGOTIABLE: "negotiable", SEE_DESCRIPTION: "see listing", RESERVED: "reserved", NOTK: "n.o.t.k." }[t] || t.toLowerCase();
  }
  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  // ---------- second-hand ----------
  const form = $("#filters");
  function filters() {
    const f = new FormData(form);
    return {
      variant: f.get("variant"), dist: +f.get("dist"), maxprice: +f.get("maxprice") || Infinity,
      minyear: +f.get("minyear") || 0, sort: f.get("sort"), dealers: f.get("dealers") === "on", removed: f.get("removed") === "on",
    };
  }
  // "Best deal" order: trustworthy listings first, then ones with warnings, needs-work last
  const rank = (l) => (l.deal?.label === "needs work" ? 3 : 0) + (l.fair?.confidence === "low" ? 1 : 0) + ((l.attrs?.red_flags || []).length ? 1 : 0);
  function renderUsed() {
    const F = filters();
    store.set("filters", Object.fromEntries(new FormData(form)));
    const group = (l) => ({ unknown: "family", other: "family" }[l.attrs.variant] || l.attrs.variant);
    let rows = DATA.used.filter((l) =>
      (F.removed || l.active) &&
      (!F.variant || group(l) === F.variant) &&
      (l.distance_km == null || l.distance_km <= F.dist) &&
      (l.price_eur == null || l.price_eur <= F.maxprice) &&
      (!F.minyear || (l.attrs.model_year || 0) >= F.minyear) &&
      (F.dealers || l.attrs.seller_type !== "dealer"));
    const by = {
      deal: (a, b) => (b.active - a.active) || (rank(a) - rank(b)) || ((b.deal?.pct ?? -999) - (a.deal?.pct ?? -999)),
      price: (a, b) => (a.price_eur ?? 1e9) - (b.price_eur ?? 1e9),
      newest: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""),
      distance: (a, b) => (a.distance_km ?? 1e9) - (b.distance_km ?? 1e9),
      year: (a, b) => (b.attrs.model_year || 0) - (a.attrs.model_year || 0),
    }[F.sort];
    rows.sort(by);
    const list = $("#used-list");
    list.replaceChildren(...rows.map((l) => card(l, "used")));
    if (!rows.length) list.innerHTML = `<div class="empty">No listings match these filters.</div>`;
    $("#count").textContent = `${rows.length} listing${rows.length === 1 ? "" : "s"}`;
  }
  form.addEventListener("input", renderUsed);

  // ---------- new ----------
  function renderNew() {
    const rows = DATA.new.filter((l) => l.active).sort((a, b) => (a.variant_group || "").localeCompare(b.variant_group || "") || a.price_eur - b.price_eur);
    const list = $("#new-list");
    list.replaceChildren(...rows.map((l) => card(l, "new")));
    if (!rows.length) list.innerHTML = `<div class="empty">No new bikes tracked yet. Add shop product pages under <code>new_bike_pages</code> in config.yaml.</div>`;
  }

  // ---------- market ----------
  let charts = [];
  function renderMarket() {
    if (!DATA || charts.length || typeof Chart === "undefined") return;
    const text = css("--muted"), grid = css("--line");
    Chart.defaults.color = text; Chart.defaults.borderColor = grid; Chart.defaults.font.family = "system-ui, sans-serif";
    const colors = { family: "#2a78c2", shorty: "#d1762a", tender: "#7b5cc4", cargo: "#2f9c78" };
    const groupOf = (v) => ({ unknown: "family", other: "family" }[v] || v);
    const pts = DATA.used.filter((l) => l.price_eur && l.attrs.model_year);
    const ds = Object.keys(colors).map((g) => {
      const p = pts.filter((l) => groupOf(l.attrs.variant) === g);
      return p.length && {
        label: VARIANT[g], data: p.map((l) => ({ x: l.attrs.model_year + (Math.random() - .5) * .35, y: l.price_eur, l })),
        backgroundColor: p.map((l) => l.active ? colors[g] : "transparent"), borderColor: colors[g], pointRadius: 4, pointHoverRadius: 6,
      };
    }).filter(Boolean);
    charts.push(new Chart($("#scatter"), {
      type: "scatter", data: { datasets: ds },
      options: {
        maintainAspectRatio: false,
        scales: { x: { ticks: { stepSize: 1, callback: (v) => String(Math.round(v)) } }, y: { ticks: { callback: (v) => eur(v) } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${eur(c.raw.y)} · ${c.raw.l.title.slice(0, 50)}` } } },
        onClick: (_, els) => { if (els[0]) window.open(ds[els[0].datasetIndex].data[els[0].index].l.url, "_blank", "noopener"); },
      },
    }));

    const curve = DATA.model.family_curve || [];
    $("#curve-note").textContent = curve.length
      ? "Model estimate for a privately sold Family with Enviolo, average mileage (Cargo Line motor up to 3 years old, Performance CX after that)."
      : "Appears once about 25 listings with a price have been collected.";
    if (curve.length) charts.push(new Chart($("#curve"), {
      type: "line",
      data: { labels: curve.map((c) => c.age === 0 ? "new" : c.age + " yr"), datasets: [{ data: curve.map((c) => c.price), borderColor: css("--accent"), tension: .25, pointRadius: 3 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => eur(c.raw) } } }, scales: { y: { ticks: { callback: (v) => eur(v) } } } },
    }));

    // median by year x variant
    const groups = {};
    DATA.used.filter((l) => l.price_eur && l.attrs.model_year).forEach((l) => {
      const k = l.attrs.model_year, g = groupOf(l.attrs.variant);
      ((groups[k] ||= {})[g] ||= []).push(l.price_eur);
    });
    const med = (a) => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
    const vs = Object.keys(colors).filter((g) => Object.values(groups).some((r) => r[g]));
    const years = Object.keys(groups).sort().reverse();
    $("#year-table").innerHTML = years.length ? `<table class="data"><tr><th>Year</th>${vs.map((g) => `<th>${VARIANT[g]}</th>`).join("")}</tr>${years.map((y) =>
      `<tr><td>${y}</td>${vs.map((g) => { const a = groups[y][g]; return `<td>${a ? `${eur(med(a))} <span class="muted">(${a.length})</span>` : "–"}</td>`; }).join("")}</tr>`).join("")}</table>`
      : `<p class="muted">No data yet.</p>`;

    const eff = DATA.model.regression && DATA.model.regression.effects_pct;
    const NAMES = {
      age: "Each year older", age_sq: "(age curve)", shorty: "Shorty vs Family", tender: "Tender vs Family", cargo: "Cargo vs Family",
      motor_new: "Cargo Line / CX motor", motor_old: "Older motor (Active Plus / Shimano)", battery_kwh: "+1 kWh battery",
      dual_battery: "Two batteries", enviolo: "Enviolo hub", log_km: "Higher mileage (per log-step)", rain_tent: "Rain tent included",
      child_extras: "Child seat extras", dealer: "Sold by a dealer", like_new: "As-new condition", needs_work: "Needs work", speed: "Speed pedelec (45 km/h)",
    };
    $("#effects").innerHTML = eff ? `<table class="data">${Object.entries(eff).filter(([k]) => k !== "age_sq").map(([k, v]) =>
      `<tr><td>${NAMES[k] || k}</td><td class="${v >= 0 ? "pos" : "neg"}">${v > 0 ? "+" : ""}${v}%</td></tr>`).join("")}</table>
      <p class="muted small">Based on ${DATA.model.regression.n} listings. Typical error ±${Math.round(DATA.model.regression.sigma * 100)}%.</p>`
      : `<p class="muted">Appears once about 25 listings with a price have been collected (${DATA.model.n_comparables} so far).</p>`;
  }

  // ---------- boot ----------
  fetch("data.json", { cache: "no-cache" }).then((r) => r.json()).then((d) => {
    DATA = d;
    const active = d.used.filter((l) => l.active);
    const good = active.filter((l) => l.deal && l.deal.pct >= 5).length;
    const today = d.generated_at.slice(0, 10);
    const fresh = active.filter((l) => l.first_seen === today).length;
    $("#summary").textContent = `${active.length} second-hand for sale · ${good} good deals · ${fresh} new today · from ${d.home.name || "home"}`;
    $("#updated").textContent = `Updated ${new Date(d.generated_at).toLocaleString("nl-NL")}. Prices are asking prices; "fair" is an estimate from collected listings.`;

    const saved = store.get("filters");
    if (saved) for (const [k, v] of Object.entries(saved)) { const el = form.elements[k]; if (el && el.type !== "checkbox") el.value = v; }
    if (saved) { form.elements.dealers.checked = "dealers" in saved; form.elements.removed.checked = "removed" in saved; }
    else form.elements.dist.value = String(d.default_max_distance_km >= 250 ? 250 : d.default_max_distance_km);

    renderUsed();
    renderNew();
    showTab(store.get("tab") || "used");
  }).catch((e) => { $("#summary").textContent = "Could not load data.json: " + e.message; });
})();
