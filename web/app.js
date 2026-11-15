/* FINN — mobile-first client. No build step, no framework: one file, hash routes, a sheet, an ask bar. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ls = {
  get(k, d) { try { const v = localStorage.getItem("finn." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("finn." + k, JSON.stringify(v)); } catch {} },
};

const S = {
  live: null,               // true = API reachable, false = static snapshot
  hours: ls.get("hours", 24),
  read: new Set(ls.get("read", [])),
  blur: ls.get("blur", false),
  digest: {},
  meta: null,
};

/* ------------------------------------------------------------------ data */
const TOKEN = new URLSearchParams(location.search).get("t") || ls.get("token", "");
if (TOKEN) ls.set("token", TOKEN);

function snapKey(path) { return path.replace(/^\//, "").replace(/[^\w]+/g, "_").replace(/_+$/, ""); }

async function api(path, opts = {}) {
  if (S.live !== false) {
    try {
      const r = await fetch("api" + path, { ...opts, headers: { "content-type": "application/json", ...(TOKEN ? { "x-finn-token": TOKEN } : {}), ...(opts.headers || {}) } });
      if (r.ok) { if (S.live === null) setMode(true); return await r.json(); }
      // a JSON 404 is a real "not found"; an HTML 404 means there's no backend (static hosting)
      if (r.status === 404 && (r.headers.get("content-type") || "").includes("json")) throw Object.assign(new Error("not found"), { status: 404 });
      if (r.status === 404 || r.status === 405) throw new Error("no backend");
      if (r.status !== 401) throw new Error("api " + r.status);
    } catch (e) {
      if (e.status === 404) throw e;
      if (S.live === null) setMode(false);
    }
  }
  const r = await fetch("snapshot/" + snapKey(path) + ".json");
  if (!r.ok) throw Object.assign(new Error("offline"), { offline: true });
  return r.json();
}

function setMode(live) {
  S.live = live;
  $("#mode").innerHTML = live ? '<span class="pill-live">live</span>' : '<span class="pill-live snap" title="Static snapshot — run the backend for live data">snapshot</span>';
}

/* ------------------------------------------------------------------ formatting */
const f1 = (x) => (x == null ? "—" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: 1 }));
const f0 = (x) => (x == null ? "—" : Math.round(x).toLocaleString("en-IN"));
const pct = (x, d = 1) => (x == null ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d) + "%");
const cls = (x) => (x == null ? "mute" : x > 0 ? "up" : x < 0 ? "down" : "mute");
const initials = (n) => esc((n || "?").replace(/^(Dr|Mr|Ms|Mrs)\.?\s+/i, "").split(/\s+/).filter((w) => /[A-Za-z]/.test(w[0])).map((w) => w[0]).slice(0, 2).join(""));
function ago(ts) {
  const d = new Date(ts.replace(" ", "T") + "+05:30");
  const s = (Date.now() - d) / 1000;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  if (s < 86400 * 7) return Math.round(s / 86400) + "d ago";
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
function days(n) { return n < 30 ? n + "d" : n < 365 ? Math.round(n / 30) + "mo" : (n / 365).toFixed(1) + "y"; }
const ICON = {
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
};

function strength(s) {
  const n = Math.max(1, Math.round((s || 0) * 5));
  return `<span class="strength" title="Tie strength ${Math.round((s || 0) * 100)}/100">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
}
function spark(vals, w = 84, h = 28) {
  if (!vals || vals.length < 2) return "";
  const lo = Math.min(...vals), hi = Math.max(...vals), r = hi - lo || 1;
  const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / r) * (h - 4)).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="var(--${up ? "up" : "down"})" stroke-width="1.6" points="${pts}"/></svg>`;
}
function bars(series, labels, { fmt = f0, hiLast = true } = {}) {
  const v = series.map((x) => (x == null ? 0 : x));
  const mx = Math.max(...v.map(Math.abs), 1);
  return `<div class="bars">${v.map((x, i) => `<div class="b ${x < 0 ? "neg" : ""} ${hiLast && i < v.length - 1 ? "dim" : ""}" style="height:${Math.max(3, (Math.abs(x) / mx) * 100)}%" title="${esc(labels[i])}: ${fmt(series[i])}"></div>`).join("")}</div>
    <div class="blabels">${labels.map((l) => `<span>${esc(String(l).replace(/^(Mar|Jun|Sep|Dec) 20/, "'"))}</span>`).join("")}</div>`;
}
function ring(n, d) {
  const p = d ? n / d : 0, R = 26, C = 2 * Math.PI * R;
  return `<svg class="ring" viewBox="0 0 64 64"><circle cx="32" cy="32" r="${R}" fill="none" stroke="var(--line)" stroke-width="7"/>
    <circle cx="32" cy="32" r="${R}" fill="none" stroke="var(--${p >= 0.6 ? "up" : p >= 0.4 ? "amber" : "down"})" stroke-width="7" stroke-linecap="round" stroke-dasharray="${C * p} ${C}" transform="rotate(-90 32 32)"/>
    <text x="32" y="37" text-anchor="middle" font-family="JetBrains Mono" font-size="15" font-weight="600" fill="var(--ink)">${n}/${d}</text></svg>`;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 1800); }

/* ------------------------------------------------------------------ network fragments (used everywhere) */
function callRow(c, prefix = "Best placed to explain") {
  if (!c) return "";
  return `<div class="call" data-contact="${esc(c.id)}">
    <div class="avatar sm pii">${initials(c.name)}</div>
    <div class="t"><span class="s">${esc(prefix)}</span><b class="pii">${esc(c.name)}</b> · ${esc(c.why)}
      <span class="s">${strength(c.strength)} <span class="warmth ${c.warmth}">${c.warmth}</span> · last spoke ${days(c.days_since)} ago</span></div>
    <div class="go">${ICON.arrow}</div></div>`;
}
function pathRow(p, target) {
  const kind = { knows: "knows them", "same-org": "same organisation", "same-school": "same school" }[p.kind] || p.kind;
  return `<div class="path" data-contact="${esc(p.id)}">
    <div class="hop"><span class="mute">You</span><span class="arrow">→</span><span class="via pii">${esc(p.name)}</span>
      <span class="mute">(${esc(p.relation)})</span><span class="arrow">→</span><span class="pii">${esc(target)}</span></div>
    <div class="how">${esc(p.why)}</div>
    <div class="how mute">${strength(p.path_strength)} route strength · <span class="warmth ${p.warmth}">${p.warmth}</span> · ${esc(kind)}</div></div>`;
}

/* ------------------------------------------------------------------ BRIEF (push) */
const WINDOWS = [[24, "24h"], [72, "3 days"], [168, "7 days"], [720, "30 days"]];

async function viewBrief() {
  const v = $("#view");
  v.innerHTML = `<div class="kicker" id="dateline"></div><div class="lede" id="lede"><div class="skel" style="height:60px"></div></div>
    <div class="seg" id="win">${WINDOWS.map(([h, l]) => `<button data-h="${h}" class="${h === S.hours ? "on" : ""}">${l}</button>`).join("")}</div>
    <div id="brief"><div class="skel"></div><div class="skel"></div></div>`;
  $("#dateline").textContent = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" }) + " · your portfolio";
  $$("#win button").forEach((b) => (b.onclick = () => { S.hours = +b.dataset.h; ls.set("hours", S.hours); viewBrief(); }));
  let d;
  try { d = S.digest[S.hours] || (S.digest[S.hours] = await api(`/digest?hours=${S.hours}`)); }
  catch (e) { $("#brief").innerHTML = `<div class="empty">Couldn't load filings (${esc(e.message)}).</div>`; return; }
  renderBrief(d);
}

function renderBrief(d) {
  const unread = d.headline.filter((i) => !S.read.has(i.id));
  const readN = d.headline.length - unread.length;
  $("#lede").innerHTML = esc(d.lede).replace(/^(\d+ things? worth your attention)/, "<b>$1</b>");
  const badge = $("#briefBadge"); badge.hidden = !unread.length; badge.textContent = unread.length;
  let html = "";
  if (!d.headline.length && S.hours < 168) {
    html += `<div class="card" style="padding:12px 14px"><p style="margin:0">Nothing material in this window — weekends and pre-results weeks look like this. Here's what mattered earlier this week.</p></div>
      <div id="earlier"><div class="skel"></div></div>`;
    (S.digest[168] ? Promise.resolve(S.digest[168]) : api("/digest?hours=168").then((x) => (S.digest[168] = x))).then((w) => {
      const el = $("#earlier"); if (!el) return;
      const items = w.headline.filter((i) => !S.read.has(i.id)).slice(0, 4);
      el.innerHTML = `<h2 class="sec">Earlier this week <span class="n">${items.length}</span><span class="rule"></span></h2>` + items.map(card).join("") +
        `<button class="btn ghost" data-h="168">Open the full 7-day brief</button>`;
      bindCommon(el, w);
      $$("[data-h]", el).forEach((b) => (b.onclick = () => { S.hours = 168; ls.set("hours", 168); viewBrief(); }));
    }).catch(() => { const el = $("#earlier"); if (el) el.innerHTML = ""; });
  }
  if (unread.length) html += `<h2 class="sec">Needs you <span class="n">${unread.length}</span><span class="rule"></span></h2>` + unread.map(card).join("");
  if (readN) html += `<button class="btn ghost" id="showRead" style="margin:6px 0">${readN} marked read · show</button>`;
  if (d.warm && d.warm.length) {
    html += `<h2 class="sec">A reason to reconnect <span class="rule"></span></h2><div class="list">` +
      d.warm.slice(0, 3).map((w) => `<div class="li" data-contact="${esc(w.id)}"><div class="a"><b class="pii">${esc(w.name)}</b> <span class="warmth ${w.warmth}">${days(w.days_since)} quiet</span>
        <span class="sub">${esc(w.why)}</span></div><div class="z">${ICON.arrow}</div></div>`).join("") + `</div>`;
  }
  if (d.brief.length) {
    html += `<h2 class="sec">Worth a glance <span class="n">${d.brief.length}</span><span class="rule"></span></h2><div class="list">` +
      d.brief.map((i) => `<div class="li" data-item="${esc(i.id)}"><div class="a"><b>${esc(i.company)}</b> <span class="tag ${esc(i.category)}">${esc(i.label)}</span>
        <span class="sub">${esc(i.line || i.title)}</span></div><div class="z">${ago(i.ts)}</div></div>`).join("") + `</div>`;
  }
  if (d.routine.length) {
    const g = {};
    d.routine.forEach((i) => (g[i.label] = (g[i.label] || 0) + 1));
    html += `<h2 class="sec">Routine <span class="rule"></span></h2><details class="routine"><summary class="rsum">
      <span><b>${d.routine.length}</b> filings collapsed —</span><span class="rgroups">${Object.entries(g).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, n]) => `<span class="chip">${esc(k)} ${n}</span>`).join("")}</span><span class="chev">${ICON.arrow}</span></summary>
      <div class="list" style="margin-top:8px">${d.routine.map((i) => `<a class="li" href="${esc(i.pdf)}" target="_blank" rel="noopener"><div class="a"><b>${esc(i.company)}</b> <span class="mute">· ${esc(i.nse_desc)}</span><span class="sub">${esc(i.line)}</span></div><div class="z">${ago(i.ts)}</div></a>`).join("")}</div></details>`;
  }
  html += `<p class="foot">${d.counts.raw_filings} exchange filings scanned across ${new Set([...d.headline, ...d.brief, ...d.routine].map((i) => i.symbol)).size} holdings ·
    ranked by materiality × your interests × position size · classifier: ${esc(d.model)}</p>`;
  $("#brief").innerHTML = html;
  $$("#brief [data-h]").forEach((b) => (b.onclick = () => { S.hours = +b.dataset.h; ls.set("hours", S.hours); viewBrief(); }));
  const sr = $("#showRead"); if (sr) sr.onclick = () => { S.read.clear(); ls.set("read", []); renderBrief(d); };
  bindCommon($("#brief"), d);
}

function card(i) {
  const head = `<button class="dismiss" data-read="${esc(i.id)}" title="Mark read" aria-label="Mark read">${ICON.x}</button>
    <div class="meta"><span class="co">${esc(i.company)}</span><span class="tag ${esc(i.category)}">${esc(i.label)}</span>
      ${i.weight ? `<span class="wt">${f1(i.weight)}% of book</span>` : ""}<span class="dot"></span><span>${ago(i.ts)}</span></div>`;
  let body = "";
  if (i.results && i.people && i.people.length) body = resultsBlock(i) + `<div style="margin-top:12px"></div>` + peopleBlock({ ...i, title: "Also in this filing" });
  else if (i.results) body = resultsBlock(i);
  else if (i.people && i.people.length) body = peopleBlock(i);
  else body = `<h3>${esc(i.title)}</h3>${i.line && i.line !== i.title ? `<p>${esc(i.line)}</p>` : ""}`;
  const react = i.reaction ? `<span class="${cls(i.reaction.chg_pct)} num">${pct(i.reaction.chg_pct)}</span> next session on <span class="num">${f1(i.reaction.vol_x)}×</span> volume` : "";
  const why = [react, ...(i.why_rank || []).filter((w) => !w.startsWith("stock moved")).map(esc)].filter(Boolean);
  const noRoute = i.people?.length && !i.people.some((p) => p.paths && p.paths.length);
  const call = (!i.people?.length && !i.results) || noRoute
    ? (i.call && i.call.length ? callRow(i.call[0], noRoute ? "No route to them — but ask about it" : "Best placed to explain") : "")
    : "";
  return `<article class="card" data-item="${esc(i.id)}">${head}${body}${call}
    ${why.length ? `<div class="why">${why.join('<span class="dot" style="align-self:center"></span>')}</div>` : ""}
    <div class="actions"><button class="btn" data-research="${esc(i.symbol)}">${ICON.search} Dig in</button>
      ${i.pdf ? `<a class="btn ghost" href="${esc(i.pdf)}" target="_blank" rel="noopener">${ICON.doc} Filing</a>` : ""}
      ${i.updates ? `<span class="mute" style="font-size:12px;align-self:center">+${i.updates.length} follow-up filing${i.updates.length > 1 ? "s" : ""}</span>` : ""}</div></article>`;
}

function resultsBlock(i) {
  const r = i.results;
  const rows = r.metrics.map((m) => `<div class="l">${esc(m.label)}</div><div class="v">${f0(m.value)}</div><div class="d ${cls(m.qoq)}">${pct(m.qoq, 0)}</div><div class="d ${cls(m.yoy)}">${pct(m.yoy, 0)}</div>`).join("");
  return `<h3>${esc(r.quarter)} results · ${esc(r.basis.toLowerCase())}</h3>
    <div class="rgrid"><div class="h">${esc(r.unit)}</div><div class="h r">${esc(r.quarter)}</div><div class="h r">QoQ</div><div class="h r">YoY</div>${rows}</div>
    ${i.results_read ? `<div class="read">${esc(i.results_read)}</div>` : ""}
    <a class="src" href="${esc(r.source)}" target="_blank" rel="noopener">from the filing's XBRL · filed ${esc(r.filed)}</a>
    ${i.call && i.call[0] ? callRow(i.call[0], "Sense-check the quarter with") : ""}`;
}

function peopleBlock(i) {
  const verb = { appointed: "Incoming", "assumed office": "Takes office", resigned: "Exit", retired: "Retires", ceased: "Steps down", "re-appointed": "Re-appointed", "completes term": "Term ends" };
  return `<h3>${esc(i.title)}</h3>` + i.people.slice(0, 3).map((p) => {
    const orgs = (p.career?.orgs || []).map((o) => `<span class="chip">${esc(o.org)}</span>`).join("");
    const sch = (p.career?.schools || []).filter((s, k, a) => a.findIndex((x) => x.slice(0, 12) === s.slice(0, 12)) === k).map((s) => `<span class="chip">${esc(s)}</span>`).join("");
    const you = (p.you || []).map((y) => `<span class="chip acc">${esc(y)}</span>`).join("");
    const paths = (p.paths || []).slice(0, 1).map((x) => pathRow(x, p.name)).join("");
    const more = (p.paths || []).length > 1 ? `<button class="btn ghost" style="margin-top:6px" data-person="${esc(p.name)}">${p.paths.length - 1} more route${p.paths.length > 2 ? "s" : ""}</button>` : "";
    return `<div class="person"><div class="avatar">${initials(p.name)}</div><div>
      <div class="nm">${esc(p.name)}</div><div class="act">${esc(verb[p.action] || "Named")}${p.role ? " · " + esc(p.role) : ""}</div>
      ${p.bio ? `<div class="bio" title="Tap to expand">${esc(p.bio)}</div>${p.bio_from && p.bio_from !== i.pdf ? `<div class="mute" style="font-size:11.5px">Profile found in an earlier filing</div>` : ""}` : (p.action === "appointed" ? `<div class="mute" style="font-size:12.5px;margin-top:4px">No profile in this filing.</div>` : "")}
      ${orgs || sch ? `<div class="chips">${orgs}${sch}</div>` : ""}${you ? `<div class="chips">${you}</div>` : ""}
      ${paths || (p.action && /appointed|office/.test(p.action) ? `<div class="mute" style="font-size:12.5px">No one in your network links to them yet.</div>` : "")}${more}
    </div></div>`;
  }).join("");
}

/* ------------------------------------------------------------------ WATCH (alerts) */
async function viewWatch() {
  const v = $("#view");
  const prefs = await api("/prefs").catch(() => ({ price_move_pct: 4, volume_multiple: 2, alert_categories: [] }));
  const cats = ["results", "M&A", "management_change", "litigation", "fund_raise", "credit_rating", "capex", "pledging", "insider_trading", "dividend"];
  const lab = { results: "Results", "M&A": "M&A", management_change: "Management", litigation: "Litigation", fund_raise: "Fund raise", credit_rating: "Rating", capex: "Capex", pledging: "Pledge", insider_trading: "Insider", dividend: "Dividend" };
  v.innerHTML = `<div class="kicker">Triggered alerts</div><div class="lede">You decide what “unusual” means. FINN watches.</div>
    <div class="ctl"><label>Price move beyond <b class="num" id="pv">±${prefs.price_move_pct}%</b></label><input type="range" id="pr" min="1" max="10" step="0.5" value="${prefs.price_move_pct}">
      <label>Volume above <b class="num" id="vv">${prefs.volume_multiple}×</b> its 20-day average</label><input type="range" id="vr" min="1.2" max="5" step="0.1" value="${prefs.volume_multiple}">
      <label style="margin-top:10px">Alert me the moment one of these is filed</label>
      <div class="toggles" id="cats">${cats.map((c) => `<button data-c="${esc(c)}" class="${prefs.alert_categories.includes(c) ? "on" : ""}">${lab[c]}</button>`).join("")}</div></div>
    <div id="alerts"><div class="skel"></div></div>`;
  let t;
  const reload = () => { clearTimeout(t); t = setTimeout(loadAlerts, 250); };
  $("#pr").oninput = (e) => { $("#pv").textContent = "±" + e.target.value + "%"; reload(); };
  $("#vr").oninput = (e) => { $("#vv").textContent = e.target.value + "×"; reload(); };
  $$("#cats button").forEach((b) => (b.onclick = () => { b.classList.toggle("on"); savePrefs(); reload(); }));
  $("#pr").onchange = $("#vr").onchange = savePrefs;
  function savePrefs() {
    if (!S.live) return;
    api("/prefs", { method: "POST", body: JSON.stringify({ price_move_pct: +$("#pr").value, volume_multiple: +$("#vr").value, alert_categories: $$("#cats button.on").map((b) => b.dataset.c) }) })
      .then(() => { S.digest = {}; toast("Thresholds saved"); });
  }
  async function loadAlerts() {
    const p = +$("#pr").value, vol = +$("#vr").value;
    let a;
    try { a = await api(S.live ? `/alerts?hours=168&price=${p}&volume=${vol}` : "/alerts?hours=168"); } catch { $("#alerts").innerHTML = `<div class="empty">Alerts unavailable offline.</div>`; return; }
    if (!S.live) { a.market = a.market.filter((m) => Math.abs(m.chg_pct) >= p || (m.vol_x || 0) >= vol); }
    const cats = $$("#cats button.on").map((b) => b.dataset.c);
    const fil = a.filings.filter((f) => cats.includes(f.category));
    $("#watchBadge").hidden = !a.market.length; $("#watchBadge").textContent = a.market.length;
    $("#alerts").innerHTML = `<h2 class="sec">Unusual moves · last 7 days <span class="n">${a.market.length}</span><span class="rule"></span></h2>` +
      (a.market.length ? `<div class="list">` + a.market.map((m) => `<div class="li" data-research="${esc(m.symbol)}" style="grid-template-columns:1fr auto auto;align-items:center">
        <div class="a"><b>${esc(m.company)}</b> ${m.held ? "" : '<span class="chip">watchlist</span>'} <span class="mute num" style="font-size:12px">${esc(m.date.slice(5))}</span>
          <span class="sub"><span class="${cls(m.chg_pct)} num">${pct(m.chg_pct)}</span> · <span class="num">${f1(m.vol_x)}×</span> vol ·
          ${m.explained_by.length ? "explained by: " + esc(m.explained_by[0].label + " — " + m.explained_by[0].title) : `<b style="color:var(--amber)">no filing explains it</b>${m.call && m.call[0] ? ` — ask <span class="pii">${esc(m.call[0].name)}</span>` : ""}`}</span></div>
        <div>${spark(m.spark)}</div><div class="z">${ICON.arrow}</div></div>`).join("") + `</div>` : `<div class="empty">Nothing crossed your thresholds.</div>`) +
      `<h2 class="sec">Material filings you subscribed to <span class="n">${fil.length}</span><span class="rule"></span></h2>` +
      (fil.length ? `<div class="list">` + fil.map((f) => `<div class="li" data-research="${esc(f.symbol)}"><div class="a"><b>${esc(f.company)}</b> <span class="tag ${esc(f.category)}">${esc(f.label)}</span><span class="sub">${esc(f.title)}</span></div><div class="z">${ago(f.ts)}</div></div>`).join("") + `</div>` : `<div class="empty">None in the last 7 days.</div>`) +
      `<p class="foot">EOD data · last session ${esc(a.last_session || "")} · volume vs ${a.thresholds.lookback}-day average</p>`;
    bindCommon($("#alerts"));
  }
  loadAlerts();
}

/* ------------------------------------------------------------------ RESEARCH (pull) */
async function viewResearch(sym) {
  const v = $("#view");
  const pf = await api("/portfolio").catch(() => null);
  const chips = pf ? [...pf.research, ...pf.holdings.slice(0, 8).map((h) => h.symbol)] : ["KAYNES", "NAVINFLUOR", "CREDITACC", "CDSL"];
  v.innerHTML = `<form class="search" id="rs"><input id="rq" placeholder="Any NSE symbol — e.g. KAYNES" value="${esc(sym || "")}" autocapitalize="characters" spellcheck="false"><button class="btn primary">Research</button></form>
    <div class="chips" style="margin-bottom:8px">${chips.map((c) => `<button class="chip" data-research="${esc(c)}">${esc(c)}</button>`).join("")}</div><div id="rbody"></div>`;
  $("#rs").onsubmit = (e) => { e.preventDefault(); const q = $("#rq").value.trim().toUpperCase(); if (q) location.hash = "#/research/" + q; };
  bindCommon(v);
  if (!sym) {
    if (pf) {
      $("#rbody").innerHTML = `<h2 class="sec">Your book <span class="rule"></span></h2><div class="list holdings">` + pf.holdings.map((h) => `<div class="li" data-research="${esc(h.symbol)}">
        <div class="a"><b>${esc(h.name)}</b><span class="sub">${esc(h.sector || "")} · ${f1(h.weight)}%</span></div><div>${spark(h.spark)}</div>
        <div class="z" style="text-align:right"><span class="num">${f0(h.close)}</span><br><span class="num ${cls(h.chg_pct)}">${pct(h.chg_pct)}</span></div></div>`).join("") + `</div>`;
      bindCommon($("#rbody"));
    }
    return;
  }
  $("#rbody").innerHTML = `<div class="skel" style="height:90px"></div><div class="skel"></div><div class="skel"></div><p class="foot">Reading the statements and the last four concall transcripts…</p>`;
  let r;
  try { r = await api("/research/" + encodeURIComponent(sym)); }
  catch (e) { $("#rbody").innerHTML = `<div class="empty">${e.offline ? "This snapshot only covers the demo companies — run the backend to research any NSE ticker." : "No fundamentals found for " + esc(sym) + "."}</div>`; return; }
  renderResearch(r);
}

function renderResearch(r) {
  const s = r.snapshot, f = r.financials, t = r.trajectory || {}, b = r.balance || {}, c = r.cash || {}, p = r.promises || {}, bb = r.bull_bear || {};
  const q = f.quarters || [], lq = q[q.length - 1] || {};
  const secs = [["snap", "Business"], ["num", "Numbers"], ["traj", "Trajectory"], ["bal", "Balance sheet"], ["cash", "Cash"], ["prom", "Promises vs delivery"], ["case", "Bull / bear"], ["call", "Who to call"]];
  let h = `<div class="rhead"><div class="kicker">${esc(r.symbol)} · ${esc(r.basis)} · ${esc(s.industry?.[s.industry.length - 1] || "")}</div><h1>${esc(r.name)}</h1>
    <div class="px"><span class="p">₹${f1(r.price.close)}</span><span class="num ${cls(r.price.chg_pct)}">${pct(r.price.chg_pct)}</span><span class="mute num" style="font-size:12px">1y ${pct(r.price.r252, 0)} · ${f1(r.price.off_hi_pct)}% from high</span></div></div>
    <nav class="secnav" id="secnav">${secs.map(([k, l]) => `<a href="#r-${k}" data-sec="${k}">${l}</a>`).join("")}</nav>`;

  // network first-class: who to call sits right under the header, not at the bottom
  const calls = r.network?.calls || [];
  if (calls[0]) h += callRow(calls[0], "Before you read — the person to call");

  h += `<section id="r-snap"><h2 class="sec">Business snapshot <span class="rule"></span></h2><div class="card"><p style="margin-top:0">${esc(s.about)}</p>
    ${s.mix?.length ? `<div class="kicker" style="margin:10px 0 4px">${esc(s.mix_label || "Revenue mix")}</div><div class="mix">${s.mix.map((m) => `<div class="m"><div>${esc(m.segment)}<div class="track"><i style="width:${Math.min(100, m.pct)}%"></i></div></div><div class="num" style="text-align:right">${f1(m.pct)}%</div></div>`).join("")}</div>` : ""}
    <a class="src" href="${esc(r.sources.screener)}" target="_blank" rel="noopener">company filings via screener.in</a></div></section>`;

  h += `<section id="r-num"><h2 class="sec">Financial snapshot <span class="rule"></span></h2><div class="kpis">
    <div class="kpi"><div class="k">Revenue TTM</div><div class="v">${f0(f.ttm.revenue)}</div><div class="s">₹ cr</div></div>
    <div class="kpi"><div class="k">${f.is_financial ? "Fin. margin" : "OPM"} TTM</div><div class="v">${f1(f.ttm.opm)}%</div><div class="s">${esc(f.op_label)}</div></div>
    <div class="kpi"><div class="k">PAT TTM</div><div class="v">${f0(f.ttm.pat)}</div><div class="s">₹ cr</div></div>
    ${["Stock P/E", "ROCE", "ROE"].map((k) => `<div class="kpi"><div class="k">${esc(k.replace("Stock ", ""))}</div><div class="v">${esc((s.ratios[k] || "—").replace(" %", "%"))}</div></div>`).join("")}</div>
    <div class="card"><div class="kicker">Last 8 quarters · revenue (₹ cr)</div>${bars(q.map((x) => x.revenue), q.map((x) => x.q))}
    <div class="scroll-x"><table class="tbl"><tr><th></th>${q.slice(-5).map((x) => `<th>${esc(x.q.replace(" 20", " '"))}</th>`).join("")}</tr>
      <tr><td>Revenue YoY</td>${q.slice(-5).map((x) => `<td class="${cls(x.rev_yoy)}">${pct(x.rev_yoy, 0)}</td>`).join("")}</tr>
      <tr><td>${f.is_financial ? "Fin. margin" : "OPM"}</td>${q.slice(-5).map((x) => `<td>${x.opm == null ? "—" : x.opm + "%"}</td>`).join("")}</tr>
      <tr><td>PAT YoY</td>${q.slice(-5).map((x) => `<td class="${cls(x.pat_yoy)}">${pct(x.pat_yoy, 0)}</td>`).join("")}</tr></table></div></div></section>`;

  if (t.metrics) {
    h += `<section id="r-traj"><h2 class="sec">Trajectory <span class="rule"></span></h2><div class="card">
      <p style="margin-top:0">${t.growing.length ? `<b>Growing:</b> ${esc(t.growing.join(", "))}. ` : ""}${t.lagging.length ? `<b>Lagging:</b> ${esc(t.lagging.join(", "))}. ` : ""}${t.margin ? `Margins <b>${esc(t.margin.verdict)}</b> (${f1(t.margin.avg3)}% last 3 yrs vs ${f1(t.margin.avg_prior3)}%).` : ""}</p>
      <div class="scroll-x"><table class="tbl"><tr><th>CAGR</th><th>3y</th><th>5y</th><th>10y</th><th>Trend</th></tr>
      ${t.metrics.map((m) => `<tr><td>${esc(m.label)}</td><td class="${cls(m.cagr3)}">${pct(m.cagr3, 0)}</td><td>${pct(m.cagr5, 0)}</td><td>${pct(m.cagr10, 0)}</td><td style="font-family:var(--sans)">${esc(m.trend || "—")}</td></tr>`).join("")}</table></div>
      ${t.margin ? `<div class="kicker" style="margin-top:12px">Operating margin, annual</div>${bars(t.margin.series, t.years.slice(-t.margin.series.length), { fmt: (x) => x + "%" })}` : ""}</div></section>`;
  }

  if (b.years) {
    const wc = b.wc || {};
    h += `<section id="r-bal"><h2 class="sec">Balance sheet <span class="rule"></span></h2><div class="card">
      ${b.is_lender ? `<p style="margin-top:0">Lender — leverage is the business model; watch deposits/borrowings growth and asset quality in the investor deck.</p>` :
      `<div class="kpis" style="margin-bottom:8px"><div class="kpi"><div class="k">Debt / equity</div><div class="v">${f1((b.de || []).slice(-1)[0])}×</div></div>
        <div class="kpi"><div class="k">Debt / EBITDA</div><div class="v">${b.debt_to_ebitda == null ? "—" : f1(b.debt_to_ebitda) + "×"}</div></div>
        <div class="kpi"><div class="k">Cash cycle</div><div class="v">${f0((wc["Cash Conversion Cycle"] || wc["Working Capital Days"] || []).slice(-1)[0])}d</div><div class="s">${esc(b.ccc_trend || "")}</div></div></div>`}
      <div class="scroll-x"><table class="tbl"><tr><th></th>${b.years.map((y) => `<th>${esc(y.replace("Mar 20", "'"))}</th>`).join("")}</tr>
      <tr><td>Borrowings</td>${(b.debt || []).map((x) => `<td>${f0(x)}</td>`).join("")}</tr><tr><td>Net worth</td>${(b.net_worth || []).map((x) => `<td>${f0(x)}</td>`).join("")}</tr>
      ${Object.entries(wc).filter(([k]) => /Days|Cycle/.test(k)).map(([k, vs]) => `<tr><td>${esc(k)}</td>${vs.map((x) => `<td>${f0(x)}</td>`).join("")}</tr>`).join("")}</table></div></div></section>`;
  }

  if (c.years) {
    h += `<section id="r-cash"><h2 class="sec">Cash quality <span class="rule"></span></h2><div class="card">
      <p style="margin-top:0">Over five years, operating cash flow was <b class="num">${c.cum5_pct ?? "—"}%</b> of reported profit — <b>${esc(c.verdict || "n/a")}</b> conversion.</p>
      <div class="kicker">CFO as % of PAT</div>${bars(c.conversion, c.years, { fmt: (x) => x + "%", hiLast: false })}
      <div class="scroll-x"><table class="tbl"><tr><th>₹ cr</th>${c.years.map((y) => `<th>${esc(y.replace("Mar 20", "'"))}</th>`).join("")}</tr>
      <tr><td>Cash from ops</td>${c.cfo.map((x) => `<td>${f0(x)}</td>`).join("")}</tr><tr><td>Net profit</td>${c.pat.map((x) => `<td>${f0(x)}</td>`).join("")}</tr>
      <tr><td>After investing</td>${(c.post_invest || []).map((x) => `<td class="${cls(x)}">${f0(x)}</td>`).join("")}</tr></table></div>
      <p class="mute" style="font-size:11.5px">${esc(c.note)}</p></div></section>`;
  }

  // ---- the hero: promised vs delivered
  const sc = p.scorecard || {};
  h += `<section id="r-prom"><h2 class="sec">Narrative vs numbers <span class="rule"></span></h2><div class="card">
    <div class="score">${sc.checked ? ring(sc.delivered, sc.checked) : ""}<div><div style="font:500 17px/1.3 var(--serif)">${esc(p.read || "")}</div>
      <div class="mute" style="font-size:12px;margin-top:4px">${p.n_calls || 0} concall transcripts read · ${esc(p.method || "")}</div></div></div>
    ${p.pressed?.length ? `<div class="chips" style="margin-top:10px"><span class="mute" style="font-size:12px;align-self:center">Analysts pressed on</span>${p.pressed.map((x) => `<span class="chip">${esc(x.topic)} <span class="num mute">${x.n}</span></span>`).join("")}</div>` : ""}
    <div style="margin-top:10px">${(p.items || []).map((it) => `<div class="promise"><div class="row between" style="margin-bottom:6px"><span class="kicker">${esc(it.call)} call · ${esc(it.metric)}</span><span class="verdict ${esc(it.verdict.replace(/ /g, "-"))}">${esc(it.verdict)}</span></div>
      <q>${esc(it.quote.length > 260 ? it.quote.slice(0, 257) + "…" : it.quote)}</q>
      <div class="did"><span class="lbl">Said</span><span>${it.lo != null ? `<span class="num">${it.lo}${it.hi && it.hi !== it.lo ? "–" + it.hi : ""}%</span>` : "—"} · <a class="src" href="${esc(it.url)}#page=${it.page}" target="_blank" rel="noopener">transcript p.${it.page}</a></span></div>
      <div class="did"><span class="lbl">Did</span><span>${esc(it.why)}</span></div>${it.caveat ? `<div class="cav">⚑ ${esc(it.caveat)}</div>` : ""}</div>`).join("") || `<div class="empty">No quantified guidance found on recent calls.</div>`}</div></div></section>`;

  if (bb.bull) {
    h += `<section id="r-case"><h2 class="sec">Bull vs bear <span class="rule"></span></h2><div class="bb"><div class="side bull"><h4>Bull</h4><ul>${(bb.bull.length ? bb.bull : ["—"]).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      <div class="side bear"><h4>Bear</h4><ul>${(bb.bear.length ? bb.bear : ["Nothing in the numbers flags a red flag — dig into the AR notes (contingent liabilities, RPTs)."]).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
      <p class="mute" style="font-size:11.5px">Built only from the numbers and transcripts above${bb.source === "model" ? " · phrased by the reasoning model" : " · rule-based"}.</p></section>`;
  }

  h += `<section id="r-call"><h2 class="sec">Who to call <span class="rule"></span></h2>${calls.length ? calls.map((x) => callRow({ ...x, why: x.why + (x.ask ? " — " + x.ask : "") }, x.kind === "inside" ? "Inside view" : x.kind === "alumni" ? "Has worked there" : x.kind === "competitor" ? "Sees them from a competitor" : x.kind === "expert" ? "Sector expert" : x.kind)).join("") : `<div class="empty">No one in your network maps here yet.</div>`}
    ${(r.network?.meetings || []).map((m) => `<div class="li" style="background:var(--paper);border:1px solid var(--line);border-radius:12px;margin-top:8px"><div class="a"><b>You met ${esc(m.with)}</b><span class="sub">${esc(m.date)} · ${esc(m.note)}</span></div></div>`).join("")}</section>
    <p class="foot">Primary sources only: company filings, annual reports, concall transcripts. No news, no broker notes, no model memory.<br>
    ${r.sources.concalls.map((c) => `<a class="src" href="${esc(c.transcript)}" target="_blank" rel="noopener">${esc(c.date)} transcript</a>`).join(" · ")}</p>`;
  $("#rbody").innerHTML = h;
  bindCommon($("#rbody"));
  // scroll-spy for the section nav
  const links = $$("#secnav a");
  links.forEach((a) => (a.onclick = (e) => { e.preventDefault(); document.getElementById("r-" + a.dataset.sec)?.scrollIntoView({ behavior: "smooth", block: "start" }); }));
  const io = new IntersectionObserver((es) => es.forEach((en) => { if (en.isIntersecting) { links.forEach((l) => l.classList.toggle("on", "r-" + l.dataset.sec === en.target.id)); } }), { rootMargin: "-40% 0px -55% 0px" });
  $$("#rbody section").forEach((s) => io.observe(s));
}

/* ------------------------------------------------------------------ NETWORK */
async function viewNetwork() {
  const v = $("#view");
  v.innerHTML = `<div class="kicker">Your network</div><div class="lede">What your network knows that you haven't asked it yet.</div><div id="nbody"><div class="skel"></div></div>`;
  const [pf, ex, warm, gr] = await Promise.all([
    api("/portfolio").catch(() => null), api("/network/expertise").catch(() => ({ themes: [] })),
    api("/network/warm").catch(() => ({ warm: [] })), api("/network/graph").catch(() => null),
  ]);
  const cover = pf ? await Promise.all(pf.holdings.map((h) => api("/network/company/" + h.symbol).then((r) => ({ h, c: r.calls[0] })).catch(() => ({ h, c: null })))) : [];
  const level = (c) => (!c ? 0 : c.score >= 0.82 ? 3 : c.score >= 0.62 ? 2 : 1);
  const blind = cover.filter((x) => level(x.c) <= 1);
  const deep = ex.themes.filter((t) => t.depth >= 2).slice(0, 3);
  const cold = warm.warm.filter((w) => w.days_since > 300);
  const notices = [];
  if (cover.length) notices.push(["◎", `<b>${cover.length - blind.length} of ${cover.length}</b> holdings have someone well placed to explain them.${blind.length ? ` Thin on <b>${blind.map((x) => esc(x.h.name)).join(", ")}</b>.` : ""}`]);
  if (deep.length) notices.push(["★", `Your deepest benches: <b>${deep.map((t) => esc(t.theme)).join(", ")}</b>.`]);
  if (warm.warm.length) notices.push(["↺", `<b>${warm.warm.length}</b> people you've drifted from have a timely reason to hear from you${cold.length ? ` — ${cold.length} of them over a year quiet` : ""}.`]);
  const kn = gr ? gr.nodes.filter((n) => n.type === "person").length : 0;
  if (kn) notices.push(["⇄", `Your notes link you, one hop away, to <b>${kn}</b> people who've appeared in your portfolio's filings.`]);
  let h = `<div class="list">${notices.map(([i, t]) => `<div class="notice"><div class="ic">${i}</div><div>${t}</div></div>`).join("")}</div>`;
  if (cover.length) {
    h += `<h2 class="sec">Coverage map <span class="rule"></span></h2><div class="cover">${cover.map(({ h: x, c }) => `<div class="cell c${level(c)}" data-company="${esc(x.symbol)}"><b>${esc((x.symbol === "TCS" ? "TCS" : x.name.replace(/ (Industries|Technologies|Company|Investment & Finance|Systems|India)$/, "")))}</b><span class="who pii">${c ? esc(c.name.replace(/^Dr\.?\s+/, "").split(" ")[0]) + " · " + esc(c.kind) : "nobody"}</span></div>`).join("")}</div>
      <p class="mute" style="font-size:11.5px">Stronger colour = someone better placed (inside › alumni › competitor › customer › theme expert), discounted for how cold the tie is.</p>`;
  }
  if (warm.warm.length) {
    h += `<h2 class="sec">Keep warm <span class="n">${warm.warm.length}</span><span class="rule"></span></h2>` + warm.warm.slice(0, 5).map((w) => `<div class="card" style="padding:12px 14px">
      <div class="row between"><div><b class="pii">${esc(w.name)}</b> <span class="warmth ${w.warmth}">${days(w.days_since)} quiet</span></div><button class="btn ghost" data-copy="${esc(w.opener)}">${ICON.copy} Copy note</button></div>
      <div class="mute" style="font-size:12.5px;margin-top:4px">${esc(w.why)}</div><div class="opener pii">${esc(w.opener)}</div></div>`).join("");
  }
  h += `<h2 class="sec">Expertise map <span class="rule"></span></h2><div class="themes" id="themes">${ex.themes.slice(0, 22).map((t, i) => `<button class="theme ${i === 0 ? "on" : ""}" data-theme="${i}">${esc(t.theme)}<span class="n">${t.depth || t.count}</span></button>`).join("")}</div><div id="themePeople"></div>`;
  if (gr) h += `<h2 class="sec">The graph <span class="rule"></span></h2>${graphSvg(gr)}<p class="mute" style="font-size:11.5px">Inner ring: your contacts, sized by tie strength. Outer ring: organisations and people in the news they connect you to. Tap a contact.</p>`;
  h += `<p class="foot">Contacts are synthetic demo data. Real people appear only via their own exchange filings.<br>Your network is stored with your instance and never sent to a model in identifiable form.</p>`;
  $("#nbody").innerHTML = h;
  const showTheme = (i) => {
    const t = ex.themes[i]; if (!t) return;
    $$("#themes .theme").forEach((b) => b.classList.toggle("on", +b.dataset.theme === i));
    $("#themePeople").innerHTML = `<div class="list" style="margin-top:10px">` + t.people.map((p) => `<div class="li" data-contact="${esc(p.id)}"><div class="a"><b class="pii">${esc(p.name)}</b> ${p.implied ? '<span class="chip">via employer</span>' : ""}<span class="sub">${esc(p.now)} · ${esc(p.relation)}</span></div><div class="z">${strength(p.strength)}</div></div>`).join("") + `</div>`;
    bindCommon($("#themePeople"));
  };
  $$("#themes .theme").forEach((b) => (b.onclick = () => showTheme(+b.dataset.theme)));
  showTheme(0);
  bindCommon($("#nbody"));
  $$("[data-copy]").forEach((b) => (b.onclick = () => { navigator.clipboard?.writeText(b.dataset.copy); toast("Copied — personalise before sending"); }));
}

function graphSvg(g) {
  const W = 360, C = W / 2;
  const contacts = g.nodes.filter((n) => n.type === "contact");
  const others = g.nodes.filter((n) => n.type === "org" || n.type === "person");
  const pos = { me: [C, C] };
  contacts.forEach((n, i) => { const a = (i / contacts.length) * Math.PI * 2 - Math.PI / 2; const r = 70 + (1 - n.strength) * 40; pos[n.id] = [C + r * Math.cos(a), C + r * Math.sin(a)]; });
  // outer ring placed by the mean angle of their neighbours, so clusters stay together
  const ang = {};
  others.forEach((o) => {
    const nb = g.edges.filter((e) => e.b === o.id).map((e) => pos[e.a]).filter(Boolean);
    ang[o.id] = nb.length ? Math.atan2(nb.reduce((s, p) => s + p[1] - C, 0), nb.reduce((s, p) => s + p[0] - C, 0)) : 0;
  });
  others.sort((a, b) => ang[a.id] - ang[b.id]).forEach((o, i) => { const a = (i / others.length) * Math.PI * 2 + ang[others[0].id]; const r = o.type === "person" ? 150 : 168; pos[o.id] = [C + r * Math.cos(a), C + r * Math.sin(a)]; });
  const col = { strong: "var(--up)", warm: "var(--accent)", cooling: "var(--amber)", cold: "var(--mute)" };
  let s = `<svg class="graph" viewBox="-74 -8 ${W + 148} ${W + 16}" id="graph">`;
  g.edges.forEach((e) => { const a = pos[e.a], b = pos[e.b]; if (a && b) s += `<line class="edge" data-a="${esc(e.a)}" data-b="${esc(e.b)}" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke-width="${0.4 + e.w}"/>`; });
  others.forEach((o) => { const [x, y] = pos[o.id]; s += o.type === "person" ? `<circle cx="${x}" cy="${y}" r="4" fill="var(--amber)"/>` : `<rect x="${x - 3}" y="${y - 3}" width="6" height="6" rx="1.5" fill="var(--ink-2)" opacity=".6"/>`;
    const lx = x + (x > C ? 6 : -6); s += `<text x="${lx}" y="${y + 3}" text-anchor="${x > C ? "start" : "end"}" class="${o.type === "person" ? "" : ""}">${esc(o.label.length > 16 ? o.label.slice(0, 15) + "…" : o.label)}</text>`; });
  contacts.forEach((n) => { const [x, y] = pos[n.id]; s += `<circle cx="${x}" cy="${y}" r="${4 + n.strength * 6}" fill="${col[n.warmth]}" data-contact="${esc(n.id)}" style="cursor:pointer"/>`; });
  s += `<circle cx="${C}" cy="${C}" r="13" fill="var(--ink)"/><text x="${C}" y="${C + 3}" text-anchor="middle" style="fill:var(--paper);font-weight:600">You</text></svg>`;
  return s;
}

/* ------------------------------------------------------------------ sheets */
function openSheet(html) { $("#sheetBody").innerHTML = html; $("#sheet").classList.add("on"); $("#scrim").classList.add("on"); $("#sheet").scrollTop = 0; bindCommon($("#sheetBody")); }
function closeSheet() { $("#sheet").classList.remove("on"); $("#scrim").classList.remove("on"); }
$("#scrim").onclick = closeSheet;

let CONTACTS = null;
async function contactSheet(id) {
  CONTACTS = CONTACTS || (await api("/network/contacts").catch(() => ({ contacts: [] })));
  const c = CONTACTS.contacts.find((x) => x.id === id);
  if (!c) return;
  openSheet(`<div class="row" style="gap:12px"><div class="avatar pii">${initials(c.name)}</div><div><div style="font:600 18px/1.2 var(--sans)" class="pii">${esc(c.name)}</div><div class="mute" style="font-size:13px">${esc(c.now)}</div></div></div>
    <div class="row wrap" style="margin:10px 0"><span class="chip">${esc(c.relation)}</span><span class="warmth ${c.warmth}">${c.warmth}</span><span class="mute" style="font-size:12px">last spoke ${days(c.days_since)} ago · ${strength(c.strength)}</span></div>
    ${c.notes ? `<div class="opener">${esc(c.notes)}</div>` : ""}
    <h2 class="sec">Career <span class="rule"></span></h2><div class="list">${c.employers.slice().reverse().map((e) => `<div class="li"><div class="a"><b>${esc(e.role)}</b><span class="sub">${esc(e.org_name || e.org)}</span></div><div class="z num">${e.from}–${e.to || "now"}</div></div>`).join("")}</div>
    ${c.knows?.length ? `<h2 class="sec">Knows <span class="rule"></span></h2>${c.knows.map((k) => `<div class="li" style="background:var(--paper);border-radius:12px"><div class="a"><b>${esc(k.name)}</b><span class="sub">${esc(k.how)}</span></div></div>`).join("")}` : ""}
    <h2 class="sec">Go-to for <span class="rule"></span></h2><div class="chips">${(c.expertise || []).map((x) => `<span class="chip acc">${esc(x)}</span>`).join("")}</div>`);
}

async function companyCallSheet(sym) {
  const r = await api("/network/company/" + sym).catch(() => null);
  if (!r) return;
  openSheet(`<div class="say">Who can explain <b>${esc(r.name)}</b>?</div>${r.calls.map((c) => callRow({ ...c, why: c.why + " — " + c.ask }, c.kind)).join("") || '<div class="empty">Nobody yet.</div>'}
    <div class="actions"><button class="btn primary" data-research="${esc(sym)}">${ICON.search} Research ${esc(sym)}</button></div>`);
}

async function personSheet(name) { ask(`who knows ${name}`); }

/* ------------------------------------------------------------------ ask */
const SUGGEST = {
  brief: ["What changed this week?", "Who knows Rajiv Dube?", "Who can I call on HDFC Bank?", "Who should I reconnect with?"],
  watch: ["Who can I call on CDSL?", "What changed in the last 3 days?"],
  research: ["Dig into KAYNES", "Prep me for Dixon", "Did CreditAccess deliver on guidance?"],
  network: ["Experts on specialty chemicals", "Who knows V. N. Srivatsan?", "Who understands rural lending?", "Who should I reconnect with?"],
};

async function ask(q) {
  if (!q) return;
  $("#askInput").value = "";
  openSheet(`<div class="kicker">You asked</div><div class="say">${esc(q)}</div><div class="skel"></div>`);
  let a;
  try { a = await api("/ask", { method: "POST", body: JSON.stringify({ q }) }); }
  catch { a = await offlineAsk(q); }
  renderAnswer(q, a);
}

async function offlineAsk(q) {
  // Static mode: answer the common intents from snapshot files.
  const ql = q.toLowerCase();
  const m = ql.match(/\b(kaynes|navinfluor|creditacc|cdsl|dixon|trent|hdfcbank|tcs)\b/);
  if (/research|dig|prep|guidance|deliver/.test(ql) && m) return { intent: "research", symbol: m[1].toUpperCase(), say: "Opening research…" };
  try { return await api("/ask_" + snapKey(q.toLowerCase())); } catch {}
  return { intent: "help", say: "The snapshot answers the suggested questions; run the backend to ask anything." };
}

function renderAnswer(q, a) {
  let h = `<div class="kicker">You asked</div><div class="mute" style="font-size:14px">${esc(q)}</div><div class="say">${esc(a.say || "")}</div>`;
  const d = a.data || {};
  if (a.intent === "research") { closeSheet(); location.hash = "#/research/" + a.symbol; return; }
  if (a.intent === "digest") {
    h += d.headline.slice(0, 6).map(card).join("") + `<div class="actions"><button class="btn" data-goto="brief" data-h="${a.hours}">Open in Brief</button></div>`;
  } else if (a.intent === "who_company") {
    h += d.calls.map((c) => callRow({ ...c, why: c.why + (c.ask ? " — " + c.ask : "") }, c.kind)).join("") + `<div class="actions"><button class="btn" data-research="${esc(d.symbol)}">${ICON.search} Research ${esc(d.symbol)}</button></div>`;
  } else if (a.intent === "who_person") {
    const p = d.person || {};
    if (p.bio) h += `<div class="card"><div class="kicker">${esc(p.company || "")} · ${esc(p.role || "")}</div><div class="bio open">${esc(p.bio)}</div><div class="chips">${(p.career?.orgs || []).map((o) => `<span class="chip">${esc(o.org)}</span>`).join("")}</div></div>`;
    h += (d.you || []).map((y) => `<span class="chip acc">${esc(y)}</span>`).join("");
    h += (d.paths || []).map((x) => pathRow(x, p.name)).join("") || `<div class="empty">No route yet. Add how you know people to your notes — FINN learns the edges.</div>`;
    if (a.diligence && d.questions?.length) h += `<h2 class="sec">Worth asking <span class="rule"></span></h2><div class="list">${d.questions.map((x) => `<div class="li"><div class="a">${esc(x)}</div></div>`).join("")}</div>`;
  } else if (a.intent === "expertise") {
    h += (d.themes || []).map((t) => `<h2 class="sec">${esc(t.theme)} <span class="rule"></span></h2><div class="list">${t.people.map((p) => `<div class="li" data-contact="${esc(p.id)}"><div class="a"><b class="pii">${esc(p.name)}</b> ${p.implied ? '<span class="chip">via employer</span>' : ""}<span class="sub">${esc(p.now)} · ${esc(p.relation)}</span></div><div class="z">${strength(p.strength)}</div></div>`).join("")}</div>`).join("");
  } else if (a.intent === "reconnect") {
    h += (d.warm || []).map((w) => `<div class="card" style="padding:12px 14px"><div class="row between"><b class="pii">${esc(w.name)}</b><span class="warmth ${w.warmth}">${days(w.days_since)} quiet</span></div><div class="mute" style="font-size:12.5px;margin-top:4px">${esc(w.why)}</div><div class="opener pii">${esc(w.opener)}</div></div>`).join("");
  } else if (a.intent === "prep") {
    h += `<h2 class="sec">Ask them <span class="rule"></span></h2><div class="list">${(d.questions || []).map((x) => `<div class="li"><div class="a">${esc(x)}</div></div>`).join("")}</div>
      <h2 class="sec">Call first <span class="rule"></span></h2>${(d.calls || []).map((c) => callRow(c, c.kind)).join("")}
      <div class="actions"><button class="btn primary" data-research="${esc(d.research?.symbol)}">${ICON.search} Full research</button></div>`;
  }
  if (a.via === "model") h += `<p class="foot">Routed by the model</p>`;
  openSheet(h);
}

$("#askForm").onsubmit = (e) => { e.preventDefault(); ask($("#askInput").value.trim()); };
(function voice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const b = $("#micBtn");
  if (!SR) { b.title = "Voice input isn't supported in this browser"; b.style.opacity = .4; return; }
  const rec = new SR(); rec.lang = "en-IN"; rec.interimResults = true;
  rec.onresult = (e) => { const t = [...e.results].map((r) => r[0].transcript).join(""); $("#askInput").value = t; if (e.results[e.results.length - 1].isFinal) { b.classList.remove("live"); ask(t); } };
  rec.onend = () => b.classList.remove("live");
  b.onclick = () => { b.classList.add("live"); try { rec.start(); } catch {} };
})();

/* ------------------------------------------------------------------ shared bindings */
function bindCommon(root, d) {
  $$("[data-research]", root).forEach((el) => (el.onclick = (e) => { e.preventDefault(); e.stopPropagation(); closeSheet(); location.hash = "#/research/" + el.dataset.research; }));
  $$("[data-contact]", root).forEach((el) => (el.onclick = (e) => { e.stopPropagation(); contactSheet(el.dataset.contact); }));
  $$("[data-company]", root).forEach((el) => (el.onclick = () => companyCallSheet(el.dataset.company)));
  $$("[data-person]", root).forEach((el) => (el.onclick = () => personSheet(el.dataset.person)));
  $$(".bio", root).forEach((el) => (el.onclick = () => el.classList.toggle("open")));
  $$("[data-goto]", root).forEach((el) => (el.onclick = () => { closeSheet(); if (el.dataset.h) { S.hours = +el.dataset.h; ls.set("hours", S.hours); } location.hash = "#/" + el.dataset.goto; }));
  $$("[data-read]", root).forEach((el) => (el.onclick = (e) => {
    e.stopPropagation(); S.read.add(el.dataset.read); ls.set("read", [...S.read]);
    const c = el.closest(".card"); c.style.transition = "opacity .2s, transform .2s"; c.style.opacity = 0; c.style.transform = "translateX(30px)";
    setTimeout(() => d ? renderBrief(d) : c.remove(), 200);
  }));
  if (d) $$(".li[data-item]", root).forEach((el) => (el.onclick = () => {
    const it = [...d.brief, ...d.headline].find((x) => x.id === el.dataset.item);
    if (it) openSheet(card({ ...it, tier: "headline" }));
  }));
}

/* ------------------------------------------------------------------ router & chrome */
function route() {
  const [, tab = "brief", arg] = location.hash.split("/");
  $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  $("#suggest").innerHTML = (SUGGEST[tab] || SUGGEST.brief).map((s) => `<button>${esc(s)}</button>`).join("");
  $$("#suggest button").forEach((b) => (b.onclick = () => ask(b.textContent)));
  closeSheet();
  window.scrollTo(0, 0);
  if (tab === "brief" && arg && +arg) { S.hours = +arg; }
  ({ brief: viewBrief, watch: viewWatch, research: () => viewResearch(arg && decodeURIComponent(arg)), network: viewNetwork }[tab] || viewBrief)();
}
window.addEventListener("hashchange", route);
window.addEventListener("scroll", () => $("#top").classList.toggle("scrolled", scrollY > 4), { passive: true });
$("#blurBtn").onclick = () => { S.blur = !S.blur; ls.set("blur", S.blur); applyBlur(); toast(S.blur ? "Names hidden — safe to screen-share" : "Names visible"); };
function applyBlur() { document.body.classList.toggle("blur", S.blur); $("#blurBtn").classList.toggle("on", S.blur); }
$("#themeBtn").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const nx = cur === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = nx; ls.set("theme", nx);
};
if (ls.get("theme")) document.documentElement.dataset.theme = ls.get("theme");
applyBlur();
if (!location.hash) location.hash = "#/brief"; else route();
