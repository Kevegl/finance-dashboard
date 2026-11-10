"use strict";

const $ = (s, el = document) => el.querySelector(s);
const state = { newsOn: true, pid: null, portfolios: [], sel: new Set(), summary: null, txs: [], range: "MAX", txLimit: 50, importText: null, importRes: null, editSymbol: null };

/* ---------- Formatierung ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function money(v, ccy, opts = {}) {
  if (v == null || isNaN(v)) return "–";
  if (v < 0) return "−" + money(-v, ccy, opts);
  try {
    return new Intl.NumberFormat(LOCALE, { style: "currency", currency: ccy || state.summary?.base || "CHF",
      maximumFractionDigits: opts.dec ?? 2, minimumFractionDigits: opts.dec ?? 2 }).format(v);
  } catch { return num(v, 2) + " " + (ccy || ""); }
}
function num(v, dec = 2) {
  if (v == null || isNaN(v)) return "–";
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: dec, minimumFractionDigits: 0 }).format(v);
}
function qty(v) { return num(v, Math.abs(v) < 1 ? 8 : 4); }
const HIDDEN = "•••••";
const amt = (v, ccy, opts) => (state.hide ? `${ccy || state.summary?.base || "CHF"} ${HIDDEN}` : money(v, ccy, opts));
const qtyS = (v) => (state.hide ? HIDDEN : qty(v));
function pct(v) { return v == null || isNaN(v) ? "–" : (v > 0 ? "+" : "") + num(v, 2) + " %"; }
function signed(v) { return (v > 0 ? "+" : v < 0 ? "−" : "") + (state.hide ? HIDDEN : money(Math.abs(v))); }
function dateCH(d) { const [y, m, dd] = d.split("-"); return LANG === "en" ? `${dd}/${m}/${y}` : `${dd}.${m}.${y}`; }
function compact(v) {
  return new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 }).format(v);
}
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const slotColor = (slot) => (slot != null && slot < 8 ? cssVar(`--s${slot + 1}`) : cssVar("--muted"));

/* Positionen mit ISIN als Symbol (Fonds, 3a): verständliches Kürzel und Name statt "CH0…" */
const isIsin = (s) => /^[A-Z]{2}[A-Z0-9]{9}\d$/.test(s || "");
const FUND_TAGS = [
  [/emerging|schwellen/i, "EM"], [/small|mid ?cap|klein/i, "SC"], [/real estate|immobil|reit/i, "RE"],
  [/bond|oblig|anleihe|fixed income/i, "BD"], [/\bgold\b/i, "AU"], [/japan/i, "JP"], [/pacific|asia/i, "AP"],
  [/canada|kanada/i, "CA"], [/\busa?\b|america|s&p ?500/i, "US"], [/europ/i, "EU"], [/\buk\b|united kingdom|britain/i, "UK"],
  [/world|welt|global|all country|acwi/i, "WLD"], [/switzerland|schweiz|\bswiss\b|\bspi\b|\bsmi\b|\bslp?i\b/i, "CH"],
];
function badgeText(h) {
  if (!isIsin(h.symbol)) return h.symbol.slice(0, 3);
  const name = h.name || "";
  for (const [rx, tag] of FUND_TAGS) if (rx.test(name)) return tag;
  const words = name.replace(/\(.*?\)/g, " ").split(/\s+/).filter((w) => /^[A-Za-z]/.test(w) && w.length > 2);
  return (words.slice(-3).map((w) => w[0]).join("") || h.symbol.slice(0, 2)).toUpperCase();
}
// "Swisscanto (CH) IPF I Index Equity Fund World ex CH NT CHF" -> "Swisscanto World ex CH"
function shortName(n) {
  const s = (n || "")
    .replace(/\((CH|LU|IE)\)|\b(Institutional )?Pension Fund( I+)?\b|\bIPF ?I*\b|\bIndex( Fund)?( [IVX]+| \d)?\b|\b(Equity|Equities|Bond) Fund\b/gi, " ")
    .replace(/\bEquities\b|\bEF\b|\bSWC\b/g, " ")
    .replace(/\b(NT|NMT|NSL|NA|ST|DA|I-[A-Z]-acc|I-[A-Z]|Multi Investor|acc|dis|CHF|USD|EUR)\b/gi, " ")
    .replace(/\s+-\s+|\s+-$|\(\s*\)/g, " ").replace(/\s{2,}/g, " ").trim();
  return s.length >= 4 ? s : n;
}
const titleOf = (h) => (isIsin(h.symbol) && h.name ? shortName(h.name) : h.symbol);
const subOf = (h) => (isIsin(h.symbol) ? h.symbol : h.name !== h.symbol ? h.name : (h.sources || []).join(", "));

/* Farben: offene Positionen bekommen (nach Wert) möglichst je eine eigene Farbe, auch bei vielen Fonds */
let COLOR = {};
function assignColors(holdings) {
  COLOR = {};
  const used = new Set();
  const open = holdings.filter((h) => h.quantity > 0 && h.value > 0).sort((a, b) => b.value - a.value);
  const rest = [];
  for (const h of open) {
    if (h.color_slot != null && h.color_slot < 8 && !used.has(h.color_slot)) { COLOR[h.symbol] = h.color_slot; used.add(h.color_slot); }
    else rest.push(h);
  }
  for (const h of rest) {
    const free = [0, 1, 2, 3, 4, 5, 6, 7].find((i) => !used.has(i));
    COLOR[h.symbol] = free ?? 8;
    if (free != null) used.add(free);
  }
  for (const h of holdings) if (!(h.symbol in COLOR)) COLOR[h.symbol] = h.color_slot;
}
const colorOf = (h) => slotColor(COLOR[h.symbol] ?? h.color_slot);
const typeName = (t) => ({ buy: L("Kauf", "Buy"), sell: L("Verkauf", "Sell"), dividend: L("Dividende", "Dividend"),
  tax: L("Quellensteuer", "Withholding tax"), interest: L("Zins", "Interest"), fee: L("Gebühr", "Fee"),
  deposit: L("Einzahlung", "Deposit"), withdrawal: L("Auszahlung", "Withdrawal"), transfer: "Transfer" }[t] || t);
const className = (c) => ({ etf: L("ETF / Fonds", "ETF / Funds"), stock: L("Aktien", "Stocks"), crypto: L("Krypto", "Crypto"),
  bond: L("Anleihen", "Bonds"), cash: "Cash", other: L("Andere", "Other") }[c] || c);
const priceSource = (s) => ({ manuell: L("manuell", "manual"), "letzter Kauf": L("letzter Kauf", "last buy"), Konto: L("Konto", "account") }[s] || s);

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && ["setup_required", "login_required"].includes(data.error)) {
    showAuth(data.error === "setup_required" ? "setup" : "login");
    throw new Error(L("Bitte anmelden", "Please sign in"));
  }
  if (!r.ok) throw new Error(trErr(data.error || `Fehler ${r.status}`));
  return data;
}
function toast(msg, ms = 2800) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

/* ---------- Laden ---------- */
async function load() {
  await loadPortfolios();
  const q = `?p=${state.pid}`;
  const [summary, txs] = await Promise.all([api("/api/summary" + q), api("/api/transactions" + q)]);
  state.summary = summary; state.txs = txs;
  render();
  loadNews();
}

/* ---------- News-Ticker ---------- */
const news = { key: "", at: 0, items: [] };
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  const rtf = new Intl.RelativeTimeFormat(LANG, { numeric: "auto", style: "short" });
  if (s < 3600) return rtf.format(-Math.max(1, Math.round(s / 60)), "minute");
  if (s < 86400) return rtf.format(-Math.round(s / 3600), "hour");
  return rtf.format(-Math.round(s / 86400), "day");
}
async function loadNews(force = false) {
  if (state.newsOn === false || !state.pid) { $("#news").hidden = true; return; }
  const key = `${state.pid}|${LANG}`;
  if (!force && news.key === key && Date.now() - news.at < 10 * 60e3) return renderNews();
  try {
    const r = await api(`/api/news?p=${state.pid}${force ? "&force=1" : ""}`);
    if (!r.enabled) { state.newsOn = false; $("#news").hidden = true; return; }
    news.key = key; news.at = Date.now(); news.items = r.items;
    renderNews();
  } catch { $("#news").hidden = true; }
}
function renderNews() {
  const box = $("#news"), track = $("#newsTrack");
  const items = news.items;
  if (!items.length || !(state.summary && state.summary.kpis.count)) { box.hidden = true; return; }
  const one = items.map((n) => `<a class="news-item" href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">`
    + `<span class="news-sym">${esc(n.asset)}</span><span class="news-title">${esc(n.title)}</span>`
    + `<span class="news-meta">${esc(n.source)}${n.published ? " · " + esc(ago(n.published)) : ""}</span></a>`).join("");
  // Inhalt doppelt, damit die Laufschrift nahtlos weiterläuft
  track.innerHTML = `<div class="news-set">${one}</div><div class="news-set" aria-hidden="true">${one.replace(/<a /g, '<a tabindex="-1" ')}</div>`;
  box.hidden = false;
  box.setAttribute("aria-label", L("Neueste Nachrichten zu deinen Positionen", "Latest news on your holdings"));
  newsRO.disconnect();
  newsRO.observe(track.firstElementChild);
}
// Tempo immer aus der tatsächlichen Textbreite (ca. 45 px/s). Der ResizeObserver misst neu, sobald der
// Ticker sichtbar wird – bei verstecktem Dashboard (z.B. in den Einstellungen) wäre die Breite sonst 0.
const newsRO = new ResizeObserver((entries) => {
  const w = entries[0].contentRect.width;
  if (w > 0) $("#newsTrack").style.setProperty("--news-dur", `${Math.max(30, Math.round(w / 45))}s`);
});

/* ---------- Sprache ---------- */
function applyLanguage(lang) {
  setLang(lang);
  applyHide();
  if (!$("#authView").hidden) showAuth(authMode);
  if (state.portfolios.length) renderTabs();
  if (state.summary) render();
  renderNews();
  if (!$("#settingsView").hidden) loadSettings();
}

function render() {
  const s = state.summary;
  const hasData = s.kpis.count > 0;
  $("#empty").hidden = hasData;
  for (const el of document.querySelectorAll("#dashView > .kpis, #dashView > .grid, #dashView > .card")) el.hidden = !hasData;
  $("#lastUpdate").textContent = s.last_refresh
    ? L("Kurse aktualisiert ", "Prices updated ") + new Date(s.last_refresh * 1000).toLocaleString(LOCALE, { dateStyle: "short", timeStyle: "short" })
    : L("Kurse noch nicht geladen", "Prices not loaded yet");
  renderBanner(s);
  if (!state.editingTitle) { $("#title").textContent = s.title || "Portfolio"; document.title = s.title || "Portfolio"; }
  if (!hasData) return;
  renderKpis(s);
  renderChart();
  renderAlloc(s);
  renderHoldings(s);
  renderSources(s);
  renderTxs();
}

function renderBanner(s) {
  const b = $("#banner");
  const msgs = [];
  const noTicker = s.holdings.filter((h) => h.quantity > 0 && h.price_source === "letzter Kauf");
  const syms = `<b>${noTicker.map((h) => esc(h.symbol)).join(", ")}</b>`;
  const set = `<a href="#/einstellungen">${L("Einstellungen → Portfolios", "Settings → Portfolios")}</a>`;
  if (s.kind === "3a" && !s.flow_txs && s.kpis.count)
    msgs.push(L(`Für „Selbst einbezahlt“ und „Gewinn seit Beginn“ fehlen noch die Einzahlungen – Finpension-CSV bzw. VIAC-Belege importieren oder Einzahlungen erfassen.`,
      `“Paid in” and “gain since start” need the deposits – import the Finpension CSV / VIAC statements or add deposits.`));
  if (s.kind !== "3a" && s.flow_txs)
    msgs.push(L(`Dieses Portfolio enthält Einzahlungen bzw. Transfers. Ist es eine Säule 3a? Dann unter ${set} als „Säule 3a“ markieren.`,
      `This portfolio contains deposits or transfers. Is it a pillar 3a account? Then mark it as “Pillar 3a” under ${set}.`));
  // 3a-Fonds haben meist keinen Börsenkurs – Bewertung zum letzten Kauf ist dort normal, kein Hinweis nötig
  if (noTicker.length && s.kind !== "3a") msgs.push(L(`Für ${syms} gibt es keinen Live-Kurs – bewertet wird mit dem letzten Kaufkurs. Klicke auf die Position, um einen Ticker zu setzen.`,
    `No live price for ${syms} – valued at the last purchase price. Click the holding to set a ticker.`));
  const fx = `<b>${s.fx_missing.map(esc).join(", ")}</b> → ${esc(s.base)}`;
  if (s.fx_missing.length) msgs.push(L(`Wechselkurse für ${fx} fehlen noch (Kurs-Aktualisierung abwarten oder Internetzugang des Servers prüfen).`,
    `Exchange rates for ${fx} are still missing (wait for the price update or check the server's internet access).`));
  b.innerHTML = msgs.join("<br>");
  b.hidden = !msgs.length;
}

function setDelta(el, v, text) {
  el.className = "delta " + (v > 0 ? "up" : v < 0 ? "down" : "");
  el.innerHTML = `${v > 0 ? "▲" : v < 0 ? "▼" : ""} ${text}`;
}

function renderKpis(s) {
  const k = s.kpis;
  const f = k.flows;
  document.querySelector("#kCost").previousElementSibling.textContent = f ? L("Selbst einbezahlt", "Paid in") : L("Investiert", "Invested");
  $("#legCost").textContent = f ? L("Einbezahlt", "Paid in") : L("Investiert", "Invested");
  document.querySelector("#kGain").previousElementSibling.textContent = f ? L("Gewinn seit Beginn", "Gain since start") : L("Gewinn / Verlust", "Profit / loss");
  $("#kValue").textContent = amt(k.value);
  $("#kCost").textContent = amt(k.cost);
  const feePct = k.cost ? ` (${num((k.fees / k.cost) * 100, 2)} %)` : "";
  const acct = k.account_fees || 0;
  const trade = k.fees - acct;
  $("#kCount").textContent = acct && trade > 0.005
    ? L(`Gebühren ${amt(k.fees)}${feePct}, davon Konto ${amt(acct)} · ${k.count} Transaktionen`, `Fees ${amt(k.fees)}${feePct}, account ${amt(acct)} · ${k.count} transactions`)
    : acct ? L(`Kontogebühren ${amt(acct)}${feePct} · ${k.count} Transaktionen`, `Account fees ${amt(acct)}${feePct} · ${k.count} transactions`)
    : L(`davon Gebühren ${amt(k.fees)}${feePct} · ${k.count} Transaktionen`, `incl. fees ${amt(k.fees)}${feePct} · ${k.count} transactions`);
  if (f) {
    // Säule 3a & Co.: alles gegen das selbst eingezahlte Geld gerechnet – Umschichtungen und Transfers sind neutral
    $("#kCost").textContent = amt(f.own);
    const tr = [];
    if (f.transfer_in) tr.push(L("Transfer ein ", "transfer in ") + amt(f.transfer_in));
    if (f.transfer_out) tr.push(L("Transfer aus ", "transfer out ") + amt(f.transfer_out));
    if (f.withdrawals) tr.push(L("Auszahlungen ", "withdrawals ") + amt(f.withdrawals));
    if (acct || trade > 0.005) tr.push(L("Gebühren ", "fees ") + amt(k.fees));
    $("#kCount").textContent = tr.join(" · ");
    $("#kGain").textContent = signed(k.gain_total);
    setDelta($("#kGainPct"), k.gain_total, pct(k.gain_total_pct));
  } else {
    $("#kGain").textContent = signed(k.gain);
    setDelta($("#kGainPct"), k.gain, pct(k.gain_pct));
  }
  // Höchster Gewinn (Wert − investiertes Kapital) im gesamten Verlauf
  let peak = null;
  for (const p of s.history) if (!peak || p[1] - p[2] > peak[1] - peak[2]) peak = p;
  $("#kGainPeak").textContent = peak && peak[1] - peak[2] > 0
    ? L(`Höchster Gewinn ${signed(peak[1] - peak[2])} am ${dateCH(peak[0])}`, `Highest gain ${signed(peak[1] - peak[2])} on ${dateCH(peak[0])}`) : "";
  const interest = k.interest || 0;
  $("#kIncome").textContent = amt(k.dividends + interest + k.taxes + k.realized - acct);
  const parts = [L("Dividenden ", "Dividends ") + amt(k.dividends)];
  if (interest) parts.push(L("Zinsen ", "Interest ") + amt(interest));
  if (k.taxes) parts.push(L("Quellensteuer ", "Withholding tax ") + signed(k.taxes));
  if (acct) parts.push(L("Kontogebühren ", "Account fees ") + signed(-acct));
  parts.push(L("realisiert ", "realised ") + signed(k.realized));
  $("#kIncomeHint").textContent = parts.join(" · ");
  // Veränderung seit Vortag
  const h = s.history;
  let top = null;
  for (const p of h) if (!top || p[1] > top[1]) top = p;
  $("#kValuePeak").textContent = (f && Math.abs(f.cash) >= 0.005 ? L("inkl. Konto ", "incl. cash ") + amt(f.cash) + " · " : "")
    + (top && top[1] > 0 ? L(`Höchster Wert ${amt(top[1])} am ${dateCH(top[0])}`, `Peak value ${amt(top[1])} on ${dateCH(top[0])}`) : "");
  const dv = $("#kValueDelta");
  if (h.length > 1) {
    const prev = h[h.length - 2], cur = h[h.length - 1];
    const flow = cur[2] - prev[2];
    const d = cur[1] - prev[1] - flow;
    const p = prev[1] ? (d / prev[1]) * 100 : 0;
    setDelta(dv, d, `${signed(d)} (${pct(p)}) ${L("heute", "today")}`);
  } else dv.textContent = "";
}

/* ---------- Linienchart ---------- */
function rangeData() {
  const h = state.summary.history;
  if (!h.length) return [];
  const last = new Date(h[h.length - 1][0]);
  let from = null;
  const r = state.range;
  const d = new Date(last);
  if (r === "1M") d.setMonth(d.getMonth() - 1), (from = d);
  if (r === "3M") d.setMonth(d.getMonth() - 3), (from = d);
  if (r === "6M") d.setMonth(d.getMonth() - 6), (from = d);
  if (r === "1Y") d.setFullYear(d.getFullYear() - 1), (from = d);
  if (r === "YTD") from = new Date(last.getFullYear(), 0, 1);
  const iso = from ? from.toISOString().slice(0, 10) : "";
  let data = from ? h.filter((p) => p[0] >= iso) : h;
  if (data.length < 2) data = h.slice(-2);
  // Ausdünnen für Performance (max. ~600 Punkte), letzter Punkt bleibt
  if (data.length > 600) {
    const step = Math.ceil(data.length / 600);
    data = data.filter((_, i) => i % step === 0 || i === data.length - 1);
  }
  return data;
}

function niceTicks(min, max, n = 4) {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || mag * 10;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const t = [];
  for (let v = lo; v <= hi + step / 2; v += step) t.push(v);
  return t;
}

function renderChart() {
  const el = $("#chart");
  const data = rangeData();
  const W = el.clientWidth || 700, H = el.clientHeight || 300;
  const m = { t: 10, r: 12, b: 26, l: 56 };
  if (data.length < 2) {
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}"><text class="empty-msg" x="${W / 2}" y="${H / 2}" text-anchor="middle">${L("Zu wenig Daten für einen Verlauf", "Not enough data for a chart")}</text></svg>`;
    return;
  }
  const vals = data.flatMap((p) => [p[1], p[2]]);
  let min = Math.min(...vals), max = Math.max(...vals);
  const pad = (max - min) * 0.08 || max * 0.1 || 1;
  min = Math.max(0, min - pad); max += pad;
  const ticks = niceTicks(min, max, 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const t0 = +new Date(data[0][0]), t1 = +new Date(data[data.length - 1][0]);
  const X = (d) => m.l + ((+new Date(d) - t0) / (t1 - t0 || 1)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
  const path = (i) => data.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[i]).toFixed(1)}`).join("");
  const area = path(1) + `L${X(data[data.length - 1][0]).toFixed(1)},${Y(y0)}L${X(data[0][0]).toFixed(1)},${Y(y0)}Z`;

  // X-Achse: ~5 Datumslabels
  const spanDays = (t1 - t0) / 864e5;
  const fmt = spanDays > 400 ? { month: "short", year: "2-digit" } : spanDays > 60 ? { month: "short" } : { day: "numeric", month: "short" };
  const xl = [];
  const nX = Math.max(2, Math.min(6, Math.floor((W - m.l) / 110)));
  for (let i = 0; i < nX; i++) {
    const t = t0 + ((t1 - t0) * i) / (nX - 1);
    const anchor = i === 0 ? "start" : i === nX - 1 ? "end" : "middle";
    xl.push(`<text class="axis-label" x="${X(new Date(t)).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${new Date(t).toLocaleDateString(LOCALE, fmt)}</text>`);
  }
  const s1 = cssVar("--s1");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${L("Wertentwicklung des Portfolios", "Portfolio performance")}">
    <defs><linearGradient id="gradValue" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${s1}" stop-opacity=".16"/><stop offset="1" stop-color="${s1}" stop-opacity="0"/>
    </linearGradient></defs>
    ${ticks.map((t) => `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>
      <text class="axis-label" x="${m.l - 10}" y="${Y(t) + 4}" text-anchor="end">${state.hide ? "" : compact(t)}</text>`).join("")}
    ${xl.join("")}
    <path class="area" d="${area}"/>
    <path class="l-cost" d="${path(2)}"/>
    <path class="l-value" d="${path(1)}"/>
    <g id="hover" style="display:none">
      <line class="xhair" y1="${m.t}" y2="${H - m.b}"/>
      <circle class="dot" r="5" fill="${s1}" id="dotV"/>
      <circle class="dot" r="4.5" fill="${cssVar("--ink-2")}" id="dotC"/>
    </g>
    <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" id="hit"/>
  </svg>`;

  const hit = $("#hit", el), g = $("#hover", el), tip = $("#tip");
  const xs = data.map((p) => X(p[0]));
  const move = (ev) => {
    const rect = el.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; xs[mid] < px ? (lo = mid) : (hi = mid); }
    const i = Math.abs(xs[lo] - px) < Math.abs(xs[hi] - px) ? lo : hi;
    const p = data[i];
    g.style.display = "";
    $(".xhair", g).setAttribute("x1", xs[i]); $(".xhair", g).setAttribute("x2", xs[i]);
    $("#dotV", g).setAttribute("cx", xs[i]); $("#dotV", g).setAttribute("cy", Y(p[1]));
    $("#dotC", g).setAttribute("cx", xs[i]); $("#dotC", g).setAttribute("cy", Y(p[2]));
    const gain = p[1] - p[2];
    tip.innerHTML = `<div class="d">${dateCH(p[0])}</div>
      <div class="r"><span><i class="sw"></i>${L("Wert", "Value")}</span><b>${amt(p[1])}</b></div>
      <div class="r"><span><i class="sw sw-cost"></i>${state.summary.kpis.flows ? L("Einbezahlt", "Paid in") : L("Investiert", "Invested")}</span><b>${amt(p[2])}</b></div>
      <div class="r"><span>${L("G/V", "P/L")}</span><b class="${gain >= 0 ? "up" : "down"}">${signed(gain)}</b></div>`;
    tip.hidden = false;
    const sx = rect.left + (xs[i] / W) * rect.width;
    const tw = tip.offsetWidth;
    tip.style.left = (sx + 16 + tw > window.innerWidth ? sx - tw - 16 : sx + 16) + "px";
    tip.style.top = Math.max(8, ev.clientY - 40) + "px";
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => { g.style.display = "none"; tip.hidden = true; });
}

/* ---------- Aufteilung ---------- */
function renderAlloc(s) {
  assignColors(s.holdings);
  const hs = s.holdings.filter((h) => h.quantity > 0 && h.value > 0);
  if (!hs.length) { $("#alloc").innerHTML = `<p class="hint">${L("Keine offenen Positionen.", "No open positions.")}</p>`; return; }
  const stack = hs.map((h) => `<div style="flex:${h.weight};background:${colorOf(h)}" title="${esc(h.is_cash ? L("Kontoguthaben", "Cash balance") : titleOf(h))} ${num(h.weight, 1)} %"></div>`).join("");
  const rows = hs.map((h) => `<div class="alloc-row">
      <i style="background:${colorOf(h)}"></i>
      <span class="nm" title="${esc(h.name || h.symbol)}">${h.is_cash ? esc(L("Kontoguthaben", "Cash balance"))
        : isIsin(h.symbol) ? esc(titleOf(h)) : `${esc(h.symbol)}<small>${esc(h.name !== h.symbol ? h.name : "")}</small>`}</span>
      <span class="pc">${num(h.weight, 1)} %<small>${amt(h.value, null, { dec: 0 })}</small></span>
    </div>`).join("");
  const byClass = {};
  hs.forEach((h) => (byClass[h.asset_class] = (byClass[h.asset_class] || 0) + h.weight));
  const chips = Object.entries(byClass).sort((a, b) => b[1] - a[1])
    .map(([c, w]) => `<span class="chip">${esc(className(c))} <b>${num(w, 1)} %</b></span>`).join("");
  $("#alloc").innerHTML = `<div class="stack" role="img" aria-label="${L("Aufteilung nach Position", "Allocation by holding")}">${stack}</div>
    <div class="alloc-list">${rows}</div><div class="class-split">${chips}</div>`;
}

/* ---------- Positionen ---------- */
function renderHoldings(s) {
  const rows = s.holdings.map((h) => {
    const closed = h.quantity <= 0;
    if (h.is_cash) return `<tr data-sym="CASH" style="${closed ? "opacity:.55" : ""}">
      <td><div class="asset"><span class="badge" style="background:${colorOf(h)}">CHF</span>
        <span class="t"><b>${L("Kontoguthaben", "Cash balance")}</b><small>${L("Liquidität auf dem Konto", "Cash on the account")}</small></span></div></td>
      <td class="num">–</td><td class="num">–</td><td class="num">–</td>
      <td class="num"><b>${amt(h.value)}</b></td><td class="num">–</td>
      <td class="num">${h.value > 0 ? num(h.weight, 1) + " %" : "–"}</td></tr>`;
    const gcls = h.gain > 0 ? "up" : h.gain < 0 ? "down" : "";
    const src = h.price_source === "live" ? "" : `<div class="src-tag ${h.price_source === "letzter Kauf" ? "warn" : ""}">${esc(priceSource(h.price_source))}</div>`;
    return `<tr data-sym="${esc(h.symbol)}" style="${closed ? "opacity:.55" : ""}">
      <td><div class="asset"><span class="badge" style="background:${colorOf(h)}" title="${esc(titleOf(h))}">${esc(badgeText(h))}</span>
        <span class="t" title="${esc(h.name || h.symbol)}"><b>${esc(titleOf(h))}</b><small>${esc(subOf(h))}</small></span></div></td>
      <td class="num">${closed ? L("geschlossen", "closed") : qtyS(h.quantity)}</td>
      <td class="num">${h.avg_price ? money(h.avg_price, h.avg_ccy) : "–"}</td>
      <td class="num">${h.price ? money(h.price, h.price_ccy) : "–"}${src}</td>
      <td class="num"><b>${amt(h.value)}</b></td>
      <td class="num ${gcls}">${closed ? "–" : signed(h.gain)}<div class="src-tag">${closed ? "" : pct(h.gain_pct)}</div></td>
      <td class="num">${closed ? "–" : num(h.weight, 1) + " %"}</td>
    </tr>`;
  }).join("");
  $("#holdings tbody").innerHTML = rows;
  for (const tr of document.querySelectorAll("#holdings tbody tr")) tr.onclick = () => openAsset(tr.dataset.sym);
}

/* ---------- Transaktionen ---------- */
function renderSources(s) {
  const sel = $("#txSource"), cur = sel.value;
  sel.innerHTML = `<option value="">${L("Alle Quellen", "All sources")}</option>` + s.sources.map((x) => `<option>${esc(x)}</option>`).join("");
  sel.value = s.sources.includes(cur) ? cur : "";
  $("#btnDelSource").hidden = !sel.value;
}

function renderTxs() {
  const q = $("#txSearch").value.trim().toLowerCase();
  const src = $("#txSource").value;
  const list = state.txs.filter((t) => (!src || t.source === src) &&
    (!q || `${t.asset} ${t.source} ${t.note} ${typeName(t.type)} ${t.date}`.toLowerCase().includes(q)));
  state.filtered = list;
  $("#txTable tbody").innerHTML = list.slice(0, state.txLimit).map((t) => `<tr class="${state.sel.has(t.id) ? "selected" : ""}">
      <td class="chk"><input type="checkbox" data-sel="${t.id}" ${state.sel.has(t.id) ? "checked" : ""} aria-label="${L("Auswählen", "Select")}"></td>
      <td>${dateCH(t.date)}</td>
      <td><span class="pill ${t.type}">${esc(typeName(t.type))}</span></td>
      <td><b>${esc(t.asset)}</b></td>
      <td class="num">${["buy", "sell"].includes(t.type) ? qtyS(t.quantity) : "–"}</td>
      <td class="num">${t.price ? money(t.price, t.currency) : "–"}</td>
      <td class="num">${t.fee ? amt(t.fee, t.currency) : "–"}</td>
      <td class="num">${amt(t.total, t.currency)}</td>
      <td><span class="src-tag">${esc(t.source)}</span></td>
      <td class="num nowrap"><button class="del edit" data-edit="${t.id}" title="${L("Bearbeiten", "Edit")}" aria-label="${L("Transaktion bearbeiten", "Edit transaction")}">✎</button><button class="del" data-id="${t.id}" title="${L("Löschen", "Delete")}" aria-label="${L("Transaktion löschen", "Delete transaction")}">✕</button></td>
    </tr>`).join("") || `<tr><td colspan="10" class="hint">${L("Keine Treffer", "No results")}</td></tr>`;
  $("#txMore").hidden = list.length <= state.txLimit;
  for (const c of document.querySelectorAll("#txTable [data-sel]")) {
    c.onchange = () => {
      const id = Number(c.dataset.sel);
      c.checked ? state.sel.add(id) : state.sel.delete(id);
      c.closest("tr").classList.toggle("selected", c.checked);
      updateBulk();
    };
  }
  updateBulk();
  for (const b of document.querySelectorAll("#txTable [data-edit]")) {
    b.onclick = () => openTxEdit(state.txs.find((x) => String(x.id) === b.dataset.edit));
  }
  for (const b of document.querySelectorAll("#txTable [data-id]")) {
    b.onclick = async () => {
      if (!(await confirmDialog(L("Transaktion löschen?", "Delete transaction?"),
        L("Diese Transaktion wird endgültig gelöscht.", "This transaction will be deleted permanently.")))) return;
      await api(`/api/transactions/${b.dataset.id}`, { method: "DELETE" });
      toast(L("Transaktion gelöscht", "Transaction deleted")); load();
    };
  }
}

function updateBulk() {
  const ids = new Set(state.txs.map((t) => t.id));
  for (const id of [...state.sel]) if (!ids.has(id)) state.sel.delete(id);
  const n = state.sel.size;
  const b = $("#btnBulkDel");
  b.hidden = !n;
  b.textContent = L(`${n} ${n === 1 ? "Transaktion" : "Transaktionen"} löschen`, `Delete ${n} ${n === 1 ? "transaction" : "transactions"}`);
  const vis = state.filtered || [];
  const selVis = vis.filter((t) => state.sel.has(t.id)).length;
  const all = $("#txAll");
  all.checked = vis.length > 0 && selVis === vis.length;
  all.indeterminate = selVis > 0 && selVis < vis.length;
}

/* ---------- Titel ---------- */
function editTitle() {
  if (state.editingTitle) return;
  state.editingTitle = true;
  const h = $("#title");
  const input = document.createElement("input");
  input.className = "title-input"; input.maxLength = 60; input.value = h.textContent;
  input.setAttribute("aria-label", L("Titel", "Title"));
  h.replaceWith(input); input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const val = input.value.trim() || "Portfolio";
    input.replaceWith(h); state.editingTitle = false;
    if (save && val !== h.textContent) {
      h.textContent = val; document.title = val;
      try { const r = await api("/api/settings", { method: "POST", body: { title: val } }); state.summary.title = r.title; toast(L("Titel gespeichert", "Title saved")); }
      catch (e) { toast(e.message); }
    }
  };
  input.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
  input.onblur = () => finish(true);
}

/* ---------- Import ---------- */
function openImport() {
  state.importText = null; state.importFiles = null; state.importRes = null;
  $("#impFile").value = ""; $("#dropText").textContent = L("CSV-, PDF- oder ZIP-Dateien hierher ziehen oder klicken", "Drop CSV, PDF or ZIP files here or click");
  $("#impResult").innerHTML = ""; $("#impMapping").hidden = true; $("#impCommit").disabled = true;
  $("#dlgImport").showModal();
}
window.openImport = openImport;

async function readFile(file) {
  const buf = await file.arrayBuffer();
  let text = new TextDecoder("utf-8").decode(buf);
  if (text.includes("�")) text = new TextDecoder("windows-1252").decode(buf);
  return text;
}

async function toBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
async function handleFiles(list) {
  const files = [...(list || [])];
  if (!files.length) return;
  state.mapping = null;
  const binary = files.length > 1 || files.some((f) => /\.(pdf|zip)$/i.test(f.name) || /pdf|zip/.test(f.type));
  $("#dropText").textContent = files.length === 1 ? files[0].name
    : L(`${files.length} Dateien ausgewählt`, `${files.length} files selected`);
  if (binary) {
    $("#impResult").innerHTML = `<p class="hint">${L("Dateien werden gelesen…", "Reading files…")}</p>`;
    state.importText = null;
    state.importFiles = await Promise.all(files.map(async (f) => ({ name: f.name, data: await toBase64(f) })));
  } else {
    state.importFiles = null;
    state.importText = await readFile(files[0]);
  }
  preview();
}
function importBody(extra) {
  return state.importFiles ? { files: state.importFiles, portfolio: state.pid }
    : Object.assign({ text: state.importText, portfolio: state.pid }, extra);
}

function mappingPayload() {
  if ($("#impMapping").hidden) return {};
  const mapping = {}, defaults = {};
  document.querySelectorAll("#impMapping [data-map]").forEach((s) => (mapping[s.dataset.map] = s.value));
  document.querySelectorAll("#impMapping [data-def]").forEach((s) => (defaults[s.dataset.def] = s.value.trim()));
  return { mapping, defaults };
}

async function preview() {
  if (!state.importText && !state.importFiles) return;
  const res = $("#impResult");
  res.innerHTML = `<p class="hint">${L("Datei wird analysiert…", "Analysing file…")}</p>`;
  $("#impCommit").disabled = true;
  try {
    const provider = $("#impProvider").value;
    const { mapping, defaults } = mappingPayload();
    const r = await api("/api/import/preview", { method: "POST",
      body: importBody({ provider: mapping ? "generic" : provider, mapping, defaults }) });
    state.importRes = r;
    if (r.needs_mapping) { renderMapping(r); res.innerHTML = `<p class="hint">${L("Format nicht automatisch erkannt – bitte Spalten zuordnen und „Vorschau“ klicken.", "Format not detected automatically – please map the columns and click “Preview”.")}</p>`; return; }
    renderPreview(r);
  } catch (e) {
    res.innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}

function renderMapping(r) {
  const box = $("#impMapping");
  const opts = (sel) => `<option value="">—</option>` + r.headers.map((h) => `<option ${h === sel ? "selected" : ""}>${esc(h)}</option>`).join("");
  const F = { date: L("Datum *", "Date *"), type: L("Typ (Kauf/Verkauf)", "Type (buy/sell)"), asset: "Asset / Symbol",
    quantity: L("Menge *", "Quantity *"), price: L("Kurs", "Price"), total: L("Betrag", "Amount"), fee: L("Gebühr", "Fee"), currency: L("Währung", "Currency") };
  box.innerHTML = `<div class="map-grid">
      ${Object.entries(F).map(([k, l]) => `<label class="field">${l}<select data-map="${k}">${opts(r.mapping[k])}</select></label>`).join("")}
      <label class="field">${L("Quelle / Anbieter", "Source / provider")}<input data-def="source" placeholder="${L("z.B. Swissquote", "e.g. Swissquote")}"></label>
      <label class="field">${L("Festes Asset (falls keine Spalte)", "Fixed asset (if no column)")}<input data-def="asset" placeholder="${L("z.B. VWRL", "e.g. VWRL")}"></label>
      <label class="field">${L("Standard-Währung", "Default currency")}<input data-def="currency" placeholder="CHF" maxlength="3"></label>
      <label class="field">${L("Kategorie", "Category")}<select data-def="asset_class"><option value="etf">${L("ETF / Fonds", "ETF / Fund")}</option><option value="stock">${L("Aktie", "Stock")}</option><option value="crypto">${L("Krypto", "Crypto")}</option><option value="bond">${L("Anleihe", "Bond")}</option><option value="other">${L("Andere", "Other")}</option></select></label>
    </div>
    <div class="preview" style="margin-top:12px"><table class="table"><thead><tr>${r.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${r.sample.map((row) => `<tr>${row.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <div style="margin-top:10px"><button class="btn small" id="mapApply">${L("Vorschau", "Preview")}</button></div>`;
  box.hidden = false;
  $("#mapApply").onclick = preview;
}

function renderPreview(r) {
  const tx = r.transactions;
  const pname = { relai: "Relai", ibkr: "Interactive Brokers", finpension: "Finpension 3a", viac: L("VIAC 3a (PDF-Belege)", "VIAC pillar 3a (PDF statements)"),
    pdf: L("PDF-Belege", "PDF statements"), mixed: L("Mehrere Formate", "Several formats"), generic: L("Eigenes Format", "Custom format"), standard: L("Standard-Format", "Standard format") }[r.provider] || r.provider;
  const rows = tx.slice(0, 200).map((t) => `<tr class="${t.duplicate ? "dup" : ""}">
      <td>${dateCH(t.date)}</td><td>${esc(typeName(t.type))}</td><td title="${esc(t.name || "")}">${esc(t.asset)}${t.name && t.name !== t.asset ? `<div class="hint">${esc(t.name)}</div>` : ""}</td>
      <td class="num">${["buy", "sell"].includes(t.type) ? qty(t.quantity) : "–"}</td>
      <td class="num">${t.price ? money(t.price, t.currency) : "–"}</td>
      <td class="num">${money(t.fee, t.currency)}</td>
      <td class="num">${money(t.total, t.currency)}</td></tr>`).join("");
  $("#impResult").innerHTML = `
    <div class="summary">
      <span class="chip">${L("Erkannt", "Detected")}: <b>${esc(pname)}</b></span>
      <span class="chip"><b>${r.count_new}</b> ${L("neu", "new")}</span>
      ${r.count_dup ? `<span class="chip"><b>${r.count_dup}</b> ${L("bereits vorhanden", "already present")}</span>` : ""}
      ${r.skipped.length ? `<span class="chip"><b>${r.skipped.length}</b> ${L("übersprungen", "skipped")}</span>` : ""}
    </div>
    ${tx.length ? `<div class="preview"><table class="table"><thead><tr><th>${L("Datum", "Date")}</th><th>${L("Typ", "Type")}</th><th>Asset</th>
      <th class="num">${L("Menge", "Quantity")}</th><th class="num">${L("Kurs", "Price")}</th><th class="num">${L("Gebühr", "Fee")}</th><th class="num">${L("Betrag", "Amount")}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>` : `<p class="hint">${L("Keine importierbaren Transaktionen gefunden.", "No importable transactions found.")}</p>`}
    ${r.skipped.length ? `<details><summary>${L("Übersprungene Zeilen anzeigen", "Show skipped rows")}</summary><ul>${r.skipped.slice(0, 200).map((x) => `<li>${esc(trErr(x))}</li>`).join("")}</ul></details>` : ""}`;
  $("#impCommit").disabled = !r.count_new;
  $("#impCommit").textContent = r.count_new ? L(`${r.count_new} Transaktionen importieren`, `Import ${r.count_new} transactions`) : L("Importieren", "Import");
}

async function commitImport() {
  const btn = $("#impCommit"); btn.disabled = true;
  try {
    const { mapping, defaults } = mappingPayload();
    const r = await api("/api/import/commit", { method: "POST",
      body: importBody({ provider: mapping ? "generic" : $("#impProvider").value, mapping, defaults }) });
    $("#dlgImport").close();
    toast(L(`${r.imported} Transaktionen importiert${r.duplicates ? ` · ${r.duplicates} Duplikate ignoriert` : ""}`,
      `${r.imported} transactions imported${r.duplicates ? ` · ${r.duplicates} duplicates ignored` : ""}`));
    await load();
    setTimeout(load, 6000); // Kurse werden im Hintergrund geladen
  } catch (e) {
    $("#impResult").insertAdjacentHTML("afterbegin", `<p class="err">${esc(e.message)}</p>`);
    btn.disabled = false;
  }
}

/* ---------- Asset bearbeiten ---------- */
function openAsset(sym) {
  const h = state.summary.holdings.find((x) => x.symbol === sym);
  if (!h) return;
  state.editSymbol = sym;
  $("#assetTitle").textContent = sym;
  $("#aName").value = h.name === sym ? "" : h.name;
  $("#aTicker").value = h.ticker || "";
  $("#aManual").value = h.manual_price ?? "";
  $("#aClass").value = h.asset_class || "etf";
  $("#aNews").value = h.news_query || "";
  $("#aErr").hidden = !h.price_error;
  $("#aErr").textContent = h.price_error ? L("Kursabruf fehlgeschlagen: ", "Price lookup failed: ") + h.price_error : "";
  $("#dlgAsset").showModal();
}
async function saveAsset() {
  await api(`/api/assets/${encodeURIComponent(state.editSymbol)}`, { method: "PUT", body: {
    name: $("#aName").value.trim(), ticker: $("#aTicker").value.trim(),
    manual_price: $("#aManual").value, asset_class: $("#aClass").value, news_query: $("#aNews").value.trim() } });
  $("#dlgAsset").close();
  toast(L("Gespeichert – Kurse werden geladen…", "Saved – loading prices…"));
  loadNews(true);
  await load(); setTimeout(load, 4000);
}

/* ---------- Manuelle Transaktion ---------- */
function openTxNew() {
  const f = $("#txForm"); f.reset();
  f.elements.id.value = "";
  f.elements.date.value = new Date().toISOString().slice(0, 10);
  $("#txTitle").textContent = L("Transaktion erfassen", "Add transaction");
  $("#dlgTx").showModal();
}
function openTxEdit(t) {
  if (!t) return;
  const f = $("#txForm"); f.reset();
  const h = state.summary.holdings.find((x) => x.symbol === t.asset);
  for (const k of ["id", "date", "type", "asset", "quantity", "price", "fee", "total", "currency", "source", "note"]) {
    if (f.elements[k]) f.elements[k].value = t[k] ?? "";
  }
  f.elements.asset_class.value = h?.asset_class || "etf";
  $("#txTitle").textContent = L("Transaktion bearbeiten", "Edit transaction");
  $("#dlgTx").showModal();
}
async function saveTx() {
  const f = $("#txForm");
  if (!f.reportValidity()) return;
  const body = Object.fromEntries(new FormData(f));
  body.portfolio = state.pid;
  const id = body.id; delete body.id;
  try {
    await api(id ? `/api/transactions/${id}` : "/api/transactions", { method: id ? "PUT" : "POST", body });
    $("#dlgTx").close(); f.reset(); toast(id ? L("Transaktion aktualisiert", "Transaction updated") : L("Transaktion gespeichert", "Transaction saved"));
    await load(); setTimeout(load, 4000);
  } catch (e) { toast(e.message); }
}

/* ---------- Theme ---------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $('meta[name="theme-color"]').content = t === "dark" ? "#0d0d0d" : "#f9f9f7";
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem("fd-theme"); } catch {}
  applyTheme(t || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
}

/* ---------- Beträge verbergen ---------- */
const EYE = `<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/></svg>`;
const EYE_OFF = `<svg viewBox="0 0 24 24"><path d="M3 3l18 18M10.6 5.1A10.9 10.9 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.6 7 10 7a10 10 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
function applyHide() {
  const b = $("#btnHide");
  b.innerHTML = state.hide ? EYE_OFF : EYE;
  b.title = state.hide ? L("Beträge anzeigen", "Show amounts") : L("Beträge verbergen", "Hide amounts");
  b.setAttribute("aria-label", b.title);
  b.setAttribute("aria-pressed", String(state.hide));
  b.classList.toggle("active", state.hide);
}
try { state.hide = localStorage.getItem("fd-hide") === "1"; } catch {}
applyHide();
$("#btnHide").onclick = () => {
  state.hide = !state.hide;
  try { localStorage.setItem("fd-hide", state.hide ? "1" : "0"); } catch {}
  applyHide();
  if (state.summary) render();
};

/* ---------- Events ---------- */
initTheme();
$("#title").onclick = editTitle;
$("#title").onkeydown = (e) => { if (e.key === "Enter") editTitle(); };
$("#btnEmptyImport").onclick = openImport;
for (const b of document.querySelectorAll("[data-close]")) b.onclick = () => $("#" + b.dataset.close).close();
// Importierte Dateien nach dem Schliessen sofort aus dem Browser-Speicher entfernen
$("#dlgImport").addEventListener("close", () => {
  state.importText = null; state.importFiles = null; state.importRes = null;
  $("#impFile").value = ""; $("#impResult").innerHTML = "";
});
$("#txAll").onchange = (e) => {
  for (const t of state.filtered || []) e.target.checked ? state.sel.add(t.id) : state.sel.delete(t.id);
  renderTxs();
};
$("#btnBulkDel").onclick = async () => {
  const ids = [...state.sel];
  if (!ids.length || !(await confirmDialog(L(`${ids.length} Transaktionen löschen?`, `Delete ${ids.length} transactions?`),
    L("Die ausgewählten Transaktionen werden endgültig gelöscht. Das kann nicht rückgängig gemacht werden.", "The selected transactions will be deleted permanently. This cannot be undone.")))) return;
  try {
    const r = await api("/api/transactions/delete", { method: "POST", body: { ids } });
    state.sel.clear(); toast(L(`${r.deleted} Transaktionen gelöscht`, `${r.deleted} transactions deleted`)); load();
  } catch (e) { toast(e.message); }
};
$("#btnTheme").onclick = () => {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(t); try { localStorage.setItem("fd-theme", t); } catch {}
  if (state.summary) render();
};
$("#btnImport").onclick = openImport;
$("#btnAdd").onclick = openTxNew;
$("#txSave").onclick = saveTx;
$("#aSave").onclick = () => saveAsset().catch((e) => toast(e.message));
$("#impCommit").onclick = commitImport;
$("#impFile").onchange = (e) => handleFiles(e.target.files);
$("#impProvider").onchange = () => { $("#impMapping").hidden = true; preview(); };
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => handleFiles(e.dataTransfer.files));
$("#rangeSeg").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.range = b.dataset.r;
  document.querySelectorAll("#rangeSeg button").forEach((x) => x.classList.toggle("on", x === b));
  renderChart();
};
$("#txSearch").oninput = () => { state.txLimit = 50; renderTxs(); };
$("#txSource").onchange = () => { state.txLimit = 50; $("#btnDelSource").hidden = !$("#txSource").value; renderTxs(); };
$("#txMore").onclick = () => { state.txLimit += 100; renderTxs(); };
$("#btnDelSource").onclick = async () => {
  const src = $("#txSource").value;
  if (!src || !(await confirmDialog(L(`Quelle „${src}“ löschen?`, `Delete source “${src}”?`),
    L(`Alle Transaktionen der Quelle „${src}“ in diesem Portfolio werden endgültig gelöscht.`, `All transactions from source “${src}” in this portfolio will be deleted permanently.`)))) return;
  const r = await api("/api/transactions/delete-source", { method: "POST", body: { source: src, portfolio: state.pid } });
  $("#txSource").value = ""; toast(L(`${r.deleted} Transaktionen gelöscht`, `${r.deleted} transactions deleted`)); load();
};
$("#btnRefresh").onclick = async () => {
  const b = $("#btnRefresh"); b.classList.add("loading"); b.disabled = true;
  try {
    const r = await api("/api/refresh", { method: "POST" });
    const n = Object.keys(r.errors || {}).length;
    toast(n ? L(`Kurse aktualisiert – ${n} Ticker fehlgeschlagen`, `Prices updated – ${n} tickers failed`) : L("Kurse aktualisiert", "Prices updated"));
    await load();
  } catch (e) { toast(e.message); }
  b.classList.remove("loading"); b.disabled = false;
};
let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => state.summary && renderChart(), 120); });

/* ---------- Anmeldung ---------- */
let authMode = "login";
function showAuth(mode) {
  authMode = mode;
  $("#topbar").hidden = true; $("#app").hidden = true; $("#authView").hidden = false;
  for (const d of document.querySelectorAll("dialog[open]")) d.close();
  const setup = mode === "setup";
  $("#authTitle").textContent = setup ? L("Willkommen – Zugang einrichten", "Welcome – set up your login") : L("Anmelden", "Sign in");
  $("#authSub").textContent = setup
    ? L("Lege Benutzername und Passwort für dein Finanz-Dashboard fest. Mindestens 8 Zeichen.", "Choose a username and password for your finance dashboard. At least 8 characters.")
    : L("Melde dich an, um dein Portfolio zu sehen.", "Sign in to see your portfolio.");
  $("#authRepeat").hidden = !setup;
  const f = $("#authForm");
  f.elements.password.autocomplete = setup ? "new-password" : "current-password";
  if (setup && !f.elements.username.value) f.elements.username.value = "admin";
  $("#authSubmit").textContent = setup ? L("Zugang einrichten", "Set up login") : L("Anmelden", "Sign in");
  $("#authErr").hidden = true;
  (setup || !f.elements.username.value ? f.elements.username : f.elements.password).focus();
}
function showApp() {
  $("#authView").hidden = true; $("#topbar").hidden = false; $("#app").hidden = false;
  applyRoute();
  load().catch((e) => toast(L("Laden fehlgeschlagen: ", "Loading failed: ") + e.message, 6000));
  loadUpdate();
}
$("#authForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target, err = $("#authErr");
  const username = f.elements.username.value.trim(), password = f.elements.password.value;
  const fail = (m) => { err.textContent = m; err.hidden = false; };
  if (!username || !password) return fail(L("Bitte Benutzername und Passwort eingeben.", "Please enter username and password."));
  if (authMode === "setup") {
    if (password.length < 8) return fail(L("Das Passwort muss mindestens 8 Zeichen lang sein.", "The password must be at least 8 characters long."));
    if (password !== f.elements.password2.value) return fail(L("Die Passwörter stimmen nicht überein.", "The passwords do not match."));
  }
  $("#authSubmit").disabled = true;
  try {
    await api(authMode === "setup" ? "/api/setup" : "/api/login", { method: "POST", body: { username, password, lang: LANG } });
    f.elements.password.value = ""; f.elements.password2.value = "";
    showApp();
  } catch (ex) { fail(ex.message); }
  $("#authSubmit").disabled = false;
};

/* ---------- Einstellungen ---------- */
function applyRoute() {
  const settings = location.hash === "#/einstellungen";
  $("#dashView").hidden = settings; $("#settingsView").hidden = !settings;
  $("#btnSettings").classList.toggle("active", settings);
  if (settings) loadSettings();
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", applyRoute);
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    if (!state.portfolios.length) await loadPortfolios();
    renderPfList();
    const g = $("#formGeneral"), a = $("#formAccount");
    g.elements.title.value = s.title;
    $("#setBase").innerHTML = s.currencies.map((c) => `<option ${c === s.base ? "selected" : ""}>${c}</option>`).join("");
    $("#setTtl").textContent = L(`automatisch alle ${num(s.price_ttl_h, 1)} Stunden`, `automatically every ${num(s.price_ttl_h, 1)} hours`);
    $("#setLang").value = LANG;
    $("#setNews").checked = s.news !== false;
    a.elements.username.value = s.user || "";
    $("#setSessions").textContent = L(`${s.sessions} aktive ${s.sessions === 1 ? "Sitzung" : "Sitzungen"} (angemeldete Browser/Geräte). Sitzungen laufen nach 30 Tagen ab.`,
      `${s.sessions} active ${s.sessions === 1 ? "session" : "sessions"} (signed-in browsers/devices). Sessions expire after 30 days.`);
    loadUpdate(true).then(() => { if (updWaiting() || (upd.st && upd.st.busy)) { upd.since = upd.since || Date.now(); updPoll(); } });
    $("#setData").textContent = L(`${s.transactions} Transaktionen gespeichert · Verbindung ${s.https ? "verschlüsselt (HTTPS)" : "unverschlüsselt (HTTP)"}.`,
      `${s.transactions} transactions stored · connection ${s.https ? "encrypted (HTTPS)" : "unencrypted (HTTP)"}.`);
  } catch (e) { toast(e.message); }
}
$("#btnSettings").onclick = () => { location.hash = location.hash === "#/einstellungen" ? "" : "#/einstellungen"; };
$("#btnBack").onclick = () => { location.hash = ""; };
$("#formGeneral").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const el = e.target.elements;
    const r = await api("/api/settings", { method: "POST", body: { title: el.title.value, base: el.base.value, lang: el.lang.value, news: el.news.checked } });
    $("#title").textContent = r.title; document.title = r.title;
    if (state.summary) state.summary.title = r.title;
    state.newsOn = r.news;
    if (r.lang !== LANG) { applyLanguage(r.lang); loadNews(true); }
    else if (!r.news) $("#news").hidden = true;
    else loadNews(true);
    toast(r.base_changed ? L(`Basiswährung ${r.base} – Wechselkurse werden geladen …`, `Base currency ${r.base} – loading exchange rates …`) : L("Gespeichert", "Saved"), 4000);
    if (r.base_changed) { await load(); setTimeout(() => load().catch(() => {}), 8000); }
  } catch (ex) { toast(ex.message); }
};
$("#formAccount").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target, err = $("#accErr");
  const fail = (m) => { err.textContent = m; err.hidden = false; };
  err.hidden = true;
  const body = { username: f.elements.username.value.trim(), current: f.elements.current.value, new: f.elements.new.value };
  if (!body.current) return fail(L("Bitte das aktuelle Passwort eingeben.", "Please enter your current password."));
  if (body.new && body.new.length < 8) return fail(L("Das neue Passwort muss mindestens 8 Zeichen lang sein.", "The new password must be at least 8 characters long."));
  if (body.new !== f.elements.new2.value) return fail(L("Die neuen Passwörter stimmen nicht überein.", "The new passwords do not match."));
  try {
    await api("/api/account/password", { method: "POST", body });
    for (const k of ["current", "new", "new2"]) f.elements[k].value = "";
    toast(body.new ? L("Passwort geändert – andere Geräte wurden abgemeldet", "Password changed – other devices were signed out") : L("Gespeichert", "Saved"));
    loadSettings();
  } catch (ex) { fail(ex.message); }
};
$("#btnExport").onclick = () => download(`/api/export.csv?p=${state.pid}`, "transaktionen.csv").catch((e) => toast(e.message));
$("#btnLogoutOthers").onclick = async () => {
  try { await api("/api/account/logout-others", { method: "POST" }); toast(L("Andere Geräte abgemeldet", "Other devices signed out")); loadSettings(); }
  catch (e) { toast(e.message); }
};
$("#btnLogout").onclick = async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  location.hash = ""; state.summary = null; state.txs = [];
  showAuth("login");
};

/* ---------- Portfolios ---------- */
async function loadPortfolios() {
  state.portfolios = await api("/api/portfolios");
  let pid = state.pid;
  if (pid == null) { try { pid = Number(localStorage.getItem("fd-pid")); } catch {} }
  if (!state.portfolios.some((p) => p.id === pid)) pid = state.portfolios[0]?.id;
  state.pid = pid;
  renderTabs();
}
function renderTabs() {
  const nav = $("#pfTabs");
  nav.innerHTML = state.portfolios.map((p) => `<span class="pf-tab-wrap ${p.id === state.pid ? "on" : ""}"><button class="pf-tab ${p.id === state.pid ? "on" : ""}" role="tab"
      aria-selected="${p.id === state.pid}" data-pid="${p.id}" title="${L("Doppelklick zum Umbenennen", "Double-click to rename")}">${esc(p.name)}${p.kind === "3a" ? '<span class="pf-kind">3a</span>' : ""}</button><button class="pf-del" data-del="${p.id}" title="${L("Portfolio löschen", "Delete portfolio")}" aria-label="${L("Portfolio löschen", "Delete portfolio")}: ${esc(p.name)}">✕</button></span>`).join("")
    + `<button class="pf-tab add" id="pfAdd" title="${L("Portfolio hinzufügen", "Add portfolio")}" aria-label="${L("Portfolio hinzufügen", "Add portfolio")}"><svg viewBox="0 0 14 14" aria-hidden="true"><path d="M7 1.5v11M1.5 7h11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>`;
  for (const b of nav.querySelectorAll("[data-pid]")) {
    b.onclick = () => switchPortfolio(Number(b.dataset.pid));
    b.ondblclick = () => renamePortfolioInline(b, Number(b.dataset.pid));
  }
  for (const b of nav.querySelectorAll("[data-del]")) b.onclick = () => deletePortfolio(Number(b.dataset.del));
  $("#pfAdd").onclick = () => createPortfolio();
}
/* Neues Portfolio: Name + Art (normal / Säule 3a) abfragen */
function newPortfolioDialog(name = "") {
  return new Promise((resolve) => {
    const d = $("#dlgPfNew");
    $("#pfNewName").value = name;
    d.querySelector('input[value="standard"]').checked = true;
    const done = (v) => { d.close(); resolve(v); };
    $("#pfNewOk").onclick = () => {
      const n = $("#pfNewName").value.trim();
      if (!n) return $("#pfNewName").focus();
      done({ name: n, kind: d.querySelector('input[name="pfKind"]:checked').value });
    };
    $("#pfNewCancel").onclick = () => done(null);
    $("#pfNewName").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("#pfNewOk").click(); } };
    d.onclose = () => resolve(null);
    d.showModal(); $("#pfNewName").focus();
  });
}
async function createPortfolio(name = "", { open = true } = {}) {
  const v = await newPortfolioDialog(name);
  if (!v) return null;
  try {
    const r = await api("/api/portfolios", { method: "POST", body: v });
    if (open) { state.pid = r.id; savePid(); state.sel.clear(); }
    if (!$("#settingsView").hidden) { await loadPortfolios(); renderPfList(); } else await load();
    toast(L(`Portfolio „${r.name}“ angelegt`, `Portfolio “${r.name}” created`));
    return r;
  } catch (e) { toast(e.message); return null; }
}
async function setPortfolioKind(pid, kind) {
  await api("/api/portfolios/kind", { method: "POST", body: { id: pid, kind } });
  await loadPortfolios(); renderPfList();
  toast(kind === "3a" ? L("Als Säule 3a markiert", "Marked as pillar 3a") : L("Als normales Portfolio markiert", "Marked as standard portfolio"));
}
function savePid() { try { localStorage.setItem("fd-pid", String(state.pid)); } catch {} }
async function switchPortfolio(pid) {
  if (pid === state.pid) return;
  state.pid = pid; savePid();
  state.sel.clear(); state.txLimit = 50;
  $("#txSearch").value = ""; $("#txSource").value = "";
  renderTabs();
  await load().catch((e) => toast(e.message));
}
function renamePortfolioInline(btn, pid) {
  if (!btn) return;
  const p = state.portfolios.find((x) => x.id === pid);
  const input = document.createElement("input");
  input.className = "pf-input"; input.value = p.name; input.maxLength = 40;
  input.setAttribute("aria-label", L("Portfolio umbenennen", "Rename portfolio"));
  btn.replaceWith(input); input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const name = input.value.trim();
    if (save && name && name !== p.name) {
      await api("/api/portfolios/rename", { method: "POST", body: { id: pid, name } }).catch((e) => toast(e.message));
      p.name = name;
    }
    renderTabs();
    if (!$("#settingsView").hidden) renderPfList();
  };
  input.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
  input.onblur = () => finish(true);
}
function confirmDialog(title, text, okLabel = L("Löschen", "Delete")) {
  return new Promise((resolve) => {
    const d = $("#dlgConfirm");
    $("#cfTitle").textContent = title; $("#cfText").textContent = text; $("#cfOk").textContent = okLabel;
    $("#cfCancel").textContent = L("Abbrechen", "Cancel");
    const done = (v) => { d.close(); resolve(v); };
    $("#cfOk").onclick = () => done(true);
    $("#cfCancel").onclick = () => done(false);
    d.oncancel = (e) => { e.preventDefault(); done(false); };
    d.showModal(); $("#cfCancel").focus();
  });
}
async function deletePortfolio(pid) {
  const p = state.portfolios.find((x) => x.id === pid);
  if (!p) return;
  const last = state.portfolios.length <= 1;
  const ok = await confirmDialog(L(`Portfolio „${p.name}“ löschen?`, `Delete portfolio “${p.name}”?`),
    L(`Das Portfolio und alle ${p.count} Transaktionen darin werden endgültig gelöscht. Das kann nicht rückgängig gemacht werden – lade vorher bei Bedarf eine Sicherung herunter.`,
      `The portfolio and all ${p.count} transactions in it will be deleted permanently. This cannot be undone – download a backup first if needed.`)
    + (last ? L(" Da es dein letztes Portfolio ist, startest du danach mit einem leeren „Hauptportfolio“ und musst neu importieren.",
      " As this is your last portfolio, you will start over with an empty main portfolio and need to import again.") : ""),
    L("Portfolio löschen", "Delete portfolio"));
  if (!ok) return;
  try {
    await api("/api/portfolios/delete", { method: "POST", body: { id: pid } });
    const wasActive = state.pid === pid;
    if (wasActive) state.pid = null;
    if (!$("#settingsView").hidden) { await loadPortfolios(); renderPfList(); }
    else await load();
    toast(L(`Portfolio „${p.name}“ gelöscht`, `Portfolio “${p.name}” deleted`));
  } catch (e) { toast(e.message); }
}
function renderPfList() {
  const list = $("#pfList");
  const cur = state.portfolios.find((p) => p.id === state.pid);
  $("#setActivePf").innerHTML = cur ? `${L("Geöffnetes Portfolio", "Open portfolio")}: <span class="active-chip">${esc(cur.name)}</span>` : "";
  $("#exportPf").innerHTML = cur ? `Portfolio: <b>${esc(cur.name)}</b>` : "";
  $("#btnBack").textContent = cur ? L(`← Zurück zu „${cur.name}“`, `← Back to “${cur.name}”`) : L("← Zurück zum Dashboard", "← Back to dashboard");
  list.innerHTML = state.portfolios.map((p) => `<div class="pf-row ${p.id === state.pid ? "active" : ""}" data-pid="${p.id}">
      <span class="pf-name">${esc(p.name)}${p.id === state.pid ? ` <span class="active-chip">${L("geöffnet", "open")}</span>` : ""}</span>
      <span class="hint">${p.count} ${L("Transaktionen", "transactions")}</span>
      <select class="inline" data-act="kind" aria-label="${L("Art des Portfolios", "Portfolio type")}">
        <option value="standard" ${p.kind !== "3a" ? "selected" : ""}>${L("Normal", "Standard")}</option>
        <option value="3a" ${p.kind === "3a" ? "selected" : ""}>${L("Säule 3a", "Pillar 3a")}</option></select>
      <span class="pf-actions">
        ${p.id === state.pid ? "" : `<button class="btn small ghost" data-act="open">${L("Öffnen", "Open")}</button>`}
        <button class="btn small ghost" data-act="rename">${L("Umbenennen", "Rename")}</button>
        <button class="btn small ghost danger" data-act="delete">${L("Löschen", "Delete")}</button>
      </span></div>`).join("");
  for (const row of list.querySelectorAll(".pf-row")) {
    const pid = Number(row.dataset.pid);
    row.querySelector('[data-act="rename"]').onclick = () => renamePortfolioInline(row.querySelector(".pf-name"), pid);
    row.querySelector('[data-act="delete"]').onclick = () => deletePortfolio(pid);
    row.querySelector('[data-act="kind"]').onchange = (e) => setPortfolioKind(pid, e.target.value).catch((ex) => toast(ex.message));
    const open = row.querySelector('[data-act="open"]');
    if (open) open.onclick = async () => { await switchPortfolio(pid); renderPfList(); toast(L("Portfolio gewechselt", "Portfolio switched")); };
  }
}
$("#formPfAdd").onsubmit = async (e) => {
  e.preventDefault();
  const r = await createPortfolio(e.target.elements.name.value.trim(), { open: false });
  if (r) e.target.reset();
};

/* ---------- Sicherung ---------- */
async function download(url, fallback) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(L(`Fehler ${r.status}`, `Error ${r.status}`));
  const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || fallback;
  const href = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 5000);
}
$("#btnBackup").onclick = () => download("/api/backup", "finanz-dashboard-sicherung.json").then(() => toast(L("Sicherung heruntergeladen", "Backup downloaded"))).catch((e) => toast(e.message));
$("#restoreFile").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  let data;
  try { data = JSON.parse(await file.text()); } catch { return toast(L("Die Datei ist keine gültige Sicherung (JSON).", "The file is not a valid backup (JSON).")); }
  if (data.app !== "finance-dashboard") return toast(L("Die Datei ist keine Sicherung des Finanz-Dashboards.", "The file is not a backup of the finance dashboard."));
  const nTx = (data.transactions || []).length, nPf = (data.portfolios || []).length;
  const when = data.created ? new Date(data.created).toLocaleString(LOCALE) : L("unbekannt", "unknown");
  if (!(await confirmDialog(L(`Sicherung vom ${when} wiederherstellen?`, `Restore backup from ${when}?`),
    L(`${nPf} Portfolios, ${nTx} Transaktionen. Alle aktuellen Daten werden dabei ersetzt.`, `${nPf} portfolios, ${nTx} transactions. All current data will be replaced.`),
    L("Wiederherstellen", "Restore")))) return;
  try {
    const r = await api("/api/restore", { method: "POST", body: { backup: data } });
    state.pid = null; state.sel.clear();
    toast(L(`Wiederhergestellt: ${r.portfolios} Portfolios, ${r.transactions} Transaktionen`, `Restored: ${r.portfolios} portfolios, ${r.transactions} transactions`), 5000);
    await load(); loadSettings();
  } catch (ex) { toast(ex.message, 6000); }
};

/* ---------- Updates ---------- */
const upd = { st: null, rid: null, action: null, timer: null, fails: 0, since: 0 };
const updDate = (iso) => (iso ? new Date(iso).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" }) : "–");
async function loadUpdate(withLog = false) {
  try {
    const st = await api("/api/update/status" + (withLog ? "?log=1" : ""));
    upd.st = st; upd.fails = 0;
    renderUpdate();
    return st;
  } catch (e) { upd.fails++; return null; }
}
function updWaiting() {
  const st = upd.st;
  if (!upd.rid) return false;
  return !st || st.pending || st.busy || st.request_id !== upd.rid;
}
function renderUpdate() {
  const st = upd.st || {};
  $("#updDot").hidden = !(st.available && st.behind > 0);
  if (!$("#settingsView") || $("#settingsView").hidden) return;
  $("#updUnavailable").hidden = st.available !== false;
  $("#updBody").hidden = !st.available;
  if (!st.available) return;
  const cur = st.current;
  $("#updCurrent").innerHTML = cur
    ? `<code>${esc(cur.short)}</code> ${esc(cur.subject)}<small>${esc(updDate(cur.date))}${st.branch ? " · " + esc(st.branch) : ""}</small>`
    : "–";
  const waiting = updWaiting(), busy = waiting || st.busy;
  const busyText = { check: L("Suche nach Updates …", "Checking for updates …"), update: L("Update wird installiert – das Dashboard startet gleich neu …", "Installing update – the dashboard will restart shortly …"),
    rollback: L("Vorherige Version wird wiederhergestellt …", "Restoring previous version …") }[upd.action || st.action] || L("Bitte warten …", "Please wait …");
  let html, cls = "";
  if (busy) html = `<span class="spin"></span>${esc(busyText)}`;
  else if (st.state === "error") { cls = "err"; html = esc(L("Fehler: ", "Error: ") + trErr(st.message || "")); }
  else if (st.behind > 0) { cls = "new"; html = esc(L(`${st.behind} ${st.behind === 1 ? "neue Version" : "neue Versionen"} verfügbar`, `${st.behind} new ${st.behind === 1 ? "version" : "versions"} available`)); }
  else if (st.current) { cls = "ok"; html = esc(L("Auf dem neuesten Stand", "Up to date")); }
  else html = esc(L("Noch nicht geprüft", "Not checked yet"));
  if (!busy && st.last_check) html += `<small>${esc(L("Zuletzt geprüft: ", "Last checked: ") + updDate(st.last_check))}</small>`;
  const el = $("#updState"); el.innerHTML = html; el.className = "readonly upd-state " + cls;
  const commits = st.behind > 0 ? (st.commits || []) : [];
  $("#updCommits").hidden = !commits.length;
  $("#updCommits").innerHTML = commits.map((c) => `<li>${esc(c.subject)} <small>${esc(updDate(c.date))} · <code>${esc(c.short)}</code></small></li>`).join("");
  $("#btnUpdInstall").hidden = !(st.behind > 0);
  $("#btnUpdInstall").disabled = busy || st.dirty || st.ahead > 0;
  $("#btnUpdCheck").disabled = busy;
  $("#btnUpdRollback").hidden = !st.previous;
  $("#btnUpdRollback").disabled = busy;
  if (st.previous) $("#btnUpdRollback").title = `${st.previous.short} · ${st.previous.subject}`;
  const log = st.log || [];
  $("#updLogBox").hidden = !log.length || busy;
  $("#updLog").textContent = log.join("\n");
  if (st.state === "error" && log.length) $("#updLogBox").open = true;
}
async function updPoll() {
  clearTimeout(upd.timer);
  await loadUpdate(true);
  // Während eines Updates ist der Server kurz weg – einfach weiter versuchen (max. 30 Min.)
  if ((updWaiting() || (upd.st && upd.st.busy)) && Date.now() - upd.since < 30 * 60e3) { upd.timer = setTimeout(updPoll, 2000); return; }
  if (!upd.rid) return;
  const st = upd.st || {}, action = upd.action;
  upd.rid = null; upd.action = null;
  renderUpdate();
  if (st.state === "error") return toast(trErr(st.message || "") || L("Fehlgeschlagen", "Failed"), 8000);
  if (action === "check") return toast(st.behind > 0 ? L("Neue Version verfügbar", "New version available") : L("Das Dashboard ist auf dem neuesten Stand", "The dashboard is up to date"));
  toast(action === "update" ? L("Update installiert – Seite wird neu geladen …", "Update installed – reloading …") : L("Vorherige Version wiederhergestellt – Seite wird neu geladen …", "Previous version restored – reloading …"), 4000);
  setTimeout(() => location.reload(), 1500);
}
async function updRequest(action) {
  try {
    const r = await api("/api/update/request", { method: "POST", body: { action } });
    upd.rid = r.request_id; upd.action = action; upd.since = Date.now();
    renderUpdate();
    upd.timer = setTimeout(updPoll, 1200);
  } catch (e) { toast(e.message, 6000); }
}
$("#btnUpdCheck").onclick = () => updRequest("check");
$("#btnUpdInstall").onclick = async () => {
  const st = upd.st || {};
  if (!(await confirmDialog(L("Update jetzt installieren?", "Install update now?"),
    L(`${st.behind} neue ${st.behind === 1 ? "Version" : "Versionen"}. Das Dashboard ist während des Updates kurz nicht erreichbar (meist unter einer Minute). Deine Daten bleiben unverändert.`,
      `${st.behind} new ${st.behind === 1 ? "version" : "versions"}. The dashboard will be briefly unavailable during the update (usually under a minute). Your data stays unchanged.`),
    L("Aktualisieren", "Update")))) return;
  updRequest("update");
};
$("#btnUpdRollback").onclick = async () => {
  const p = (upd.st || {}).previous || {};
  if (!(await confirmDialog(L("Vorherige Version wiederherstellen?", "Restore previous version?"),
    L(`Zurück zu ${p.short}: ${p.subject}. Deine Daten bleiben unverändert.`, `Back to ${p.short}: ${p.subject}. Your data stays unchanged.`),
    L("Wiederherstellen", "Restore")))) return;
  updRequest("rollback");
};

/* ---------- Start ---------- */
(async () => {
  try {
    const st = await fetch("/api/auth/status").then((r) => r.json());
    if (st.news === false) state.newsOn = false;
    if (st.lang && st.lang !== LANG) applyLanguage(st.lang);
    if (st.setup_required) showAuth("setup");
    else if (!st.authenticated) showAuth("login");
    else showApp();
  } catch (e) { toast(L("Server nicht erreichbar: ", "Server not reachable: ") + e.message, 6000); }
})();
