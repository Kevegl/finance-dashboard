"use strict";
/* Spartipps & Vorsorge: Steuer-Schätzung, 3a-Stand, FIRE-/Pensionsrechner, Fristen und Tipp-Katalog.
   Alles wird im Browser gerechnet, gespeichert wird nur das Profil (POST /api/plan).
   Kantonsdaten stehen in CANTONS – weitere Kantone lassen sich dort ergänzen.
   Alle Tarife sind Näherungen (Stand 2026, ohne Gewähr) und ersetzen keine Steuerberatung. */

const PLAN_YEAR = 2026;

/* Bundesweite Eckwerte 2026 */
const CHV = {
  a3Small: 7258,        // 3a mit Pensionskasse
  a3Big: 36288,         // 3a ohne Pensionskasse (max. 20 % des Erwerbseinkommens)
  ahvMax: 30240,        // AHV-Maximalrente Einzelperson pro Jahr (12 × 2'520)
  refAge: 65,           // AHV-Referenzalter
  a3From: 60,           // 3a-Bezug frühestens 5 Jahre vor dem Referenzalter
  ahvNe: [[350000, 530], [8850000, 26500]], // AHV-Beiträge Nichterwerbstätige (Vermögen → Beitrag), dazwischen linear genähert
};

/* Direkte Bundessteuer 2026 (ESTV-Tabelle, Formular 58c): [ab Einkommen, Steuer an dieser Stelle, je weitere 100 CHF].
   Restbeträge unter 100 CHF fallen weg; darüber hinaus höchstens 11.5 % des Einkommens. */
const FED = {
  single: [[15200, 0, 0.77], [33200, 138.60, 0.88], [43500, 229.20, 2.64], [58000, 612.00, 2.97], [76200, 1152.50, 5.94],
    [82100, 1502.95, 6.6], [108900, 3271.75, 8.8], [141500, 6140.55, 11], [185100, 10936.55, 13.2]],
  married: [[29700, 0, 1], [53400, 237, 2], [61300, 395, 3], [79100, 929, 4], [94900, 1561, 5], [108700, 2251, 6],
    [120600, 2965, 7], [130500, 3658, 8], [138400, 4290, 9], [144300, 4821, 10], [148300, 5221, 11], [150400, 5452, 12],
    [152400, 5692, 13]],
  flat: 11.5,
  child: 263,           // Elterntarif: Abzug vom Steuerbetrag pro Kind
};

/* Kantone: Tarif der einfachen Staatssteuer als [Stufenbreite, Satz in %] */
const CANTONS = {
  ZH: {
    name: ["Zürich", "Zurich"],
    rate: 95,           // Staatssteuerfuss 2026/2027
    muni: 119,          // Vorgabe: Stadt Zürich
    muniRange: [70, 130],
    single: [[6700, 0], [4700, 2], [4700, 3], [7600, 4], [9300, 5], [10700, 6], [12400, 7], [17000, 8],
      [33600, 9], [33200, 10], [52700, 11], [68400, 12], [Infinity, 13]],
    married: [[13500, 0], [6300, 2], [8000, 3], [9700, 4], [11000, 5], [14000, 6], [16300, 7], [18400, 8],
      [22400, 9], [33600, 10], [48500, 11], [117500, 12], [Infinity, 13]],
  },
};

const PLAN_DEFAULTS = {
  canton: "ZH", civil: "single", kids: 0, income: 85000, muni: 119, church: 0, marg: "",
  age: 35, pk: true, earned: 100000, buyIn: 0, buyYears: 3,
  spend: 60000, save: 20000, a3full: true, ret: 4, swr: 3.5, fireAge: 55, ahv: 28000, pkRente: 18000,
  wealthFree: "", wealth3a: "", pf3a: "cmp", cfg3a: {}, gemeinde: "", gemVar: 0, konf: "none", taxYear: "", done: {}, cat: "all", hideDone: false,
};

const plan = { P: null, dash: null, rates: {}, saveT: null, loading: false, open3a: new Set() };

/* ---------- Hilfen ---------- */
const chf = (v) => (state.hide ? `CHF ${HIDDEN}` : money(Math.round(v || 0), "CHF", { dec: 0 }));
const pctS = (v, d = 1) => `${num(v, d)} %`;
const nv = (v, d = 0) => { const x = Number(v); return v === "" || v == null || !isFinite(x) ? d : x; };
function canton() { return CANTONS[plan.P.canton] || CANTONS.ZH; }

/* ---------- Steuern ---------- */
function cantonSimple(inc, married) {
  let tax = 0, rest = Math.max(0, inc);
  for (const [w, r] of canton()[married ? "married" : "single"]) {
    const part = Math.min(rest, w);
    tax += part * r / 100; rest -= part;
    if (rest <= 0) break;
  }
  return tax;
}
function fedTax(inc, married, kids) {
  inc = Math.floor(Math.max(0, inc) / 100) * 100;
  let tax = 0;
  for (const [from, base, per100] of FED[married ? "married" : "single"]) if (inc >= from) tax = base + (inc - from) * per100 / 100;
  tax = Math.min(tax, inc * FED.flat / 100);
  return Math.max(0, tax - kids * FED.child);
}
/* Hochgeladene Gemeindesteuerfüsse: gewähltes Steuerjahr oder das neueste */
const KONF = [["none", "Keine / andere", "None / other"], ["ref", "Reformiert", "Reformed"], ["kath", "Römisch-katholisch", "Roman Catholic"], ["christkath", "Christkatholisch", "Christ Catholic"]];
function muniTable() {
  const t = (plan.rates || {})[plan.P.canton] || {}, years = Object.keys(t).sort();
  if (!years.length) return null;
  const year = years.includes(String(plan.P.taxYear)) ? String(plan.P.taxYear) : years[years.length - 1];
  return { year, years, rows: t[year] };
}
function muniVariants(name = plan.P.gemeinde) { const t = muniTable(); return (t && name && t.rows[name]) || null; }
/* Gemeinde- und Kirchensteuerfuss: aus der Tabelle, falls eine Gemeinde gewählt ist, sonst aus dem Profil */
function rates(P = plan.P, name = P.gemeinde, variant = P.gemVar) {
  const vs = muniVariants(name);
  if (vs) {
    const v = vs[Math.min(Math.max(0, nv(variant)), vs.length - 1)], k = KONF.findIndex((x) => x[0] === P.konf);
    const base = v[0], withC = k > 0 && v[k] != null ? v[k] : base;
    return { muni: base, church: Math.max(0, withC - base), csv: true };
  }
  return { muni: nv(P.muni, canton().muni), church: nv(P.church), csv: false };
}
/* Verheiratete und Alleinerziehende rechnen mit dem Verheiratetentarif */
function taxes(inc, P = plan.P, r = rates(P)) {
  const married = P.civil !== "single";
  const simple = cantonSimple(inc, married);
  const cant = simple * (canton().rate + r.muni + r.church) / 100;
  const fed = fedTax(inc, married, nv(P.kids));
  return { simple, cant, fed, total: cant + fed };
}
function marginal(inc) {
  if (plan.P.marg !== "" && isFinite(Number(plan.P.marg))) return Number(plan.P.marg) / 100;
  return Math.max(0, (taxes(inc).total - taxes(Math.max(0, inc - 1000)).total) / 1000);
}
/* Ersparnis eines Abzugs: mit eigenem Grenzsteuersatz (falls gesetzt) linear, sonst exakt über den Tarif */
function saving(inc, ded, own = true) {
  ded = Math.min(ded, inc);
  if (ded <= 0) return 0;
  if (own && plan.P.marg !== "" && isFinite(Number(plan.P.marg))) return ded * Number(plan.P.marg) / 100;
  return taxes(inc).total - taxes(inc - ded).total;
}
function a3Limit(P = plan.P) {
  return P.pk ? CHV.a3Small : Math.min(CHV.a3Big, Math.max(0, nv(P.earned)) * 0.2);
}

/* ---------- FIRE / Pensionierung ---------- */
function ahvNonEmployed(w) {
  const [[w0, c0], [w1, c1]] = CHV.ahvNe;
  if (w <= w0) return c0;
  return Math.min(c1, c0 + (w - w0) * (c1 - c0) / (w1 - w0));
}
function wealthNow() {
  const P = plan.P, d = plan.dash || {};
  return {
    free: P.wealthFree !== "" ? nv(P.wealthFree) : (d.free || 0),
    p3: P.wealth3a !== "" ? nv(P.wealth3a) : sel3a().reduce((a, x) => a + x.value, 0),
  };
}
/* Einstellungen pro 3a-Konto (z.B. Partner/in), Vorgabe aus dem Profil */
function cfg3a(id) {
  const c = (plan.P.cfg3a || {})[id] || {};
  return { pk: c.pk ?? plan.P.pk, earned: c.earned ?? plan.P.earned, income: c.income ?? "" };
}
function save3aFor(id, ded) {
  const cf = cfg3a(id);
  return cf.income === "" ? saving(nv(plan.P.income), ded) : saving(nv(cf.income), ded, false);
}
/* Gewählte 3a-Portfolios: eines oder (Vergleich) alle */
function sel3a() {
  const list = (plan.dash && plan.dash.pf3a) || [];
  const one = list.find((x) => String(x.id) === String(plan.P.pf3a));
  return one ? [one] : list;
}
/* Jahresweise Simulation in heutiger Kaufkraft (reale Rendite), Steuern vereinfacht ignoriert */
function simulate(fireAge) {
  const P = plan.P, r = nv(P.ret) / 100, a0 = Math.round(nv(P.age, 35));
  let { free, p3 } = wealthNow();
  const marks = {}, ahvCost = [];
  for (let age = a0; age <= 95; age++) {
    marks[age] = free + p3;
    if (age < fireAge) {
      free = free * (1 + r) + nv(P.save);
      p3 = p3 * (1 + r) + (P.a3full && age < CHV.refAge ? a3Limit() : 0);
      continue;
    }
    p3 *= 1 + r;
    if (age >= CHV.a3From && p3 > 0) { free += p3; p3 = 0; }
    let need = nv(P.spend);
    if (age >= CHV.refAge) need -= nv(P.ahv) + nv(P.pkRente);
    else { const c = ahvNonEmployed(free + p3); need += c; ahvCost.push(c); }
    free = free * (1 + r) - need;
    if (free + p3 < 0) return { ok: false, broke: age, marks, ahvCost };
  }
  return { ok: true, marks, ahvCost, end: free + p3 };
}
function earliestFire() {
  const a0 = Math.round(nv(plan.P.age, 35));
  for (let fa = a0; fa <= 70; fa++) if (simulate(fa).ok) return fa;
  return null;
}

/* ---------- Fristen ---------- */
const DEADLINES = [
  { m: 11, d: 30, de: "Krankenkasse: Grundversicherung kündigen oder Franchise senken", en: "Health insurance: cancel basic cover or lower the deductible",
    tde: "Die Kündigung muss bis 30. November bei der Kasse eingetroffen sein (eingeschrieben). Prämien auf priminfo.admin.ch vergleichen. Eine höhere Franchise kann noch bis 31. Dezember gewählt werden.",
    ten: "The notice must reach the insurer by 30 November (registered mail). Compare premiums on priminfo.admin.ch. A higher deductible can still be chosen until 31 December." },
  { m: 12, d: 20, de: "Säule 3a einzahlen, PK-Einkauf, Spenden", en: "Pay into pillar 3a, pension fund buy-in, donations",
    tde: "Massgebend ist die Gutschrift bis 31. Dezember – Banken empfehlen Zahlungen bis etwa 20. Dezember. Viele Pensionskassen haben für Einkäufe einen Stichtag Mitte Dezember.",
    ten: "What counts is the credit by 31 December – banks recommend paying by about 20 December. Many pension funds have a mid-December cut-off for buy-ins." },
  { m: 2, d: 15, de: "Steuerunterlagen sammeln", en: "Collect tax documents",
    tde: "Lohnausweis, 3a- und PK-Einkaufsbescheinigungen, Steuerauszüge der Broker (z.B. IBKR-Jahresauszug), Krypto-Bestand per 31.12. (ESTV-Kursliste) und Belege für Weiterbildung, Spenden und Krankheitskosten.",
    ten: "Salary certificate, pillar 3a and buy-in certificates, broker tax statements (e.g. IBKR annual statement), crypto holdings as of 31 Dec (ESTV price list) and receipts for training, donations and medical costs." },
  { m: 3, d: 31, canton: "ZH", de: "Steuererklärung Kanton Zürich einreichen", en: "File the Canton of Zurich tax return",
    tde: "Frist 31. März. Eine Fristerstreckung lässt sich online beim Kantonalen Steueramt beantragen (bis 30. September kostenlos).",
    ten: "Due 31 March. An extension can be requested online from the cantonal tax office (free of charge until 30 September)." },
  { m: 9, d: 30, de: "Zusatzversicherungen prüfen · provisorische Steuern", en: "Review supplementary insurance · provisional taxes",
    tde: "Zusatzversicherungen haben oft eine Kündigungsfrist von 3 Monaten auf Ende Jahr. Im Kanton Zürich ist die provisorische Steuerrechnung am 30. September fällig – frühere Zahlungen werden verzinst.",
    ten: "Supplementary insurance often has a 3-month notice period to year end. In the Canton of Zurich the provisional tax bill is due on 30 September – earlier payments earn interest." },
];
function nextDeadlines(today = new Date()) {
  const t0 = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return DEADLINES.filter((x) => !x.canton || x.canton === plan.P.canton).map((x) => {
    let dt = new Date(t0.getFullYear(), x.m - 1, x.d);
    if (dt < t0) dt = new Date(t0.getFullYear() + 1, x.m - 1, x.d);
    return { ...x, dt, days: Math.round((dt - t0) / 864e5) };
  }).sort((a, b) => a.dt - b.dt);
}

/* ---------- Tipp-Katalog ---------- */
const TIP_CATS = [
  ["all", "Alle", "All"], ["tax", "Steuern", "Taxes"], ["pension", "Vorsorge", "Pension"], ["invest", "Anlegen", "Investing"],
  ["life", "Alltag", "Everyday"], ["fire", "FIRE", "FIRE"], ["retire", "Pensionierung", "Retirement"], ["future", "Zukunft", "Outlook"],
];
/* impact: 3 = gross, 2 = mittel, 1 = klein; calc(c) liefert einen persönlichen Satz aus dem Profil */
const TIPS = [
  { id: "3a-max", cat: "tax", impact: 3,
    de: ["Säule 3a jedes Jahr voll einzahlen", `Einzahlungen sind vom Einkommen abziehbar: ${num(CHV.a3Small, 0)} CHF mit Pensionskasse, ohne PK 20 % des Erwerbseinkommens (max. ${num(CHV.a3Big, 0)} CHF). Das Guthaben ist bis zum Bezug von Einkommens- und Vermögenssteuer befreit.`],
    en: ["Max out pillar 3a every year", `Contributions are deductible: CHF ${num(CHV.a3Small, 0)} with a pension fund, without one 20 % of earned income (max. CHF ${num(CHV.a3Big, 0)}). The balance is free of income and wealth tax until withdrawal.`],
    calc: (c) => c.inc > 0 && L(`Bei dir: ${chf(c.a3)} einzahlen spart ca. ${chf(c.save3a)} Steuern pro Jahr.`, `For you: paying in ${chf(c.a3)} saves about ${chf(c.save3a)} in tax per year.`) },
  { id: "3a-back", cat: "tax", impact: 3,
    de: ["3a-Lücken nachzahlen (neu ab 2026)", "Seit 2026 lassen sich verpasste 3a-Beiträge für bis zu 10 Jahre nachzahlen – erstmals für Lücken ab 2025. Pro Jahr ist eine Nachzahlung bis zum kleinen 3a-Maximum möglich, zusätzlich zum laufenden Beitrag, der zuerst voll einbezahlt sein muss. Voraussetzung: AHV-pflichtiges Erwerbseinkommen im Lückenjahr und im Einzahlungsjahr. Die Nachzahlung ist voll abziehbar."],
    en: ["Catch up on pillar 3a gaps (new from 2026)", "Since 2026, missed 3a contributions can be paid in retroactively for up to 10 years – starting with gaps from 2025. One catch-up payment per year up to the small 3a maximum is allowed on top of the regular contribution, which must be paid in full first. Requires AHV-liable earned income in the gap year and the payment year. The catch-up is fully deductible."],
    calc: (c) => c.inc > 0 && L(`Bei dir: eine Nachzahlung von ${chf(CHV.a3Small)} spart zusätzlich ca. ${chf(c.saveBack)}.`, `For you: a catch-up of ${chf(CHV.a3Small)} saves about ${chf(c.saveBack)} more.`) },
  { id: "pk-buy", cat: "tax", impact: 3,
    de: ["Pensionskassen-Einkauf gestaffelt planen", "Einkäufe in die PK sind voll abziehbar. Wegen der Progression lohnt es sich, grosse Einkäufe auf mehrere Jahre mit hohem Einkommen zu verteilen. Achtung: Nach einem Einkauf ist 3 Jahre lang kein Kapitalbezug möglich, WEF-Vorbezüge müssen zuerst zurückbezahlt werden. Deckungsgrad und Verzinsung der Kasse prüfen."],
    en: ["Plan pension fund buy-ins in stages", "Buy-ins are fully deductible. Because of progression, large buy-ins are best spread over several high-income years. Note: no lump-sum withdrawal for 3 years after a buy-in, and home-ownership withdrawals must be repaid first. Check the fund's funding ratio and interest rate."],
    calc: (c) => c.buy > 0 && c.inc > 0 && L(`Bei dir: ${chf(c.buy)} auf einmal spart ca. ${chf(c.buyOnce)}, verteilt auf ${c.years} Jahre ca. ${chf(c.buySplit)}.`, `For you: ${chf(c.buy)} at once saves about ${chf(c.buyOnce)}, spread over ${c.years} years about ${chf(c.buySplit)}.`) },
  { id: "deductions", cat: "tax", impact: 2,
    de: ["Alle Abzüge ausschöpfen", "Effektive Berufskosten statt Pauschale, wenn höher (Arbeitsweg, auswärtige Verpflegung, Homeoffice nur mit Auflagen). Weiterbildungskosten, Kinderdrittbetreuung, Krankheits- und Zahnarztkosten über dem Selbstbehalt von 5 % des Nettoeinkommens, Schuldzinsen und Liegenschaftsunterhalt. Belege über das Jahr sammeln."],
    en: ["Use every deduction", "Actual work expenses instead of the flat rate when higher (commute, meals away from home). Further training, third-party childcare, medical and dental costs above 5 % of net income, debt interest and property maintenance. Collect receipts throughout the year."] },
  { id: "donations", cat: "tax", impact: 1,
    de: ["Spenden bündeln", "Spenden an steuerbefreite gemeinnützige Organisationen sind bis 20 % des Nettoeinkommens abziehbar (ab 100 CHF pro Jahr). Wer regelmässig spendet, kann mehrere Jahresbeträge in einem Jahr zusammenfassen."],
    en: ["Bundle donations", "Donations to tax-exempt charities are deductible up to 20 % of net income (from CHF 100 per year). Regular donors can combine several years' worth into one year."] },
  { id: "withholding", cat: "tax", impact: 2,
    de: ["Verrechnungs- und Quellensteuern zurückholen", "Die 35 % Verrechnungssteuer auf Schweizer Dividenden und Zinsen gibt es über das Wertschriftenverzeichnis zurück – alle Konten und Depots deklarieren. Bei US-Aktien im IBKR-Depot das Formular W-8BEN ausfüllen (15 % statt 30 % US-Quellensteuer); die 15 % lassen sich über das Formular DA-1 anrechnen."],
    en: ["Reclaim withholding taxes", "The 35 % Swiss withholding tax on dividends and interest is refunded via the securities list – declare all accounts. For US shares at IBKR file the W-8BEN form (15 % instead of 30 % US withholding); the 15 % can be credited using form DA-1."] },
  { id: "accumulating", cat: "tax", impact: 1,
    de: ["Thesaurierend spart keine Steuern", "In der Schweiz werden auch reinvestierte Erträge thesaurierender Fonds und ETFs als Einkommen besteuert. Entscheidend ist die Ertragsrendite, nicht die Ausschüttungsart. Private Kapitalgewinne sind dagegen steuerfrei."],
    en: ["Accumulating funds don't save tax", "In Switzerland reinvested income of accumulating funds and ETFs is taxed as income too. What matters is the income yield, not the distribution type. Private capital gains, on the other hand, are tax-free."] },
  { id: "pro-trader", cat: "tax", impact: 2,
    de: ["Nicht als gewerbsmässiger Wertschriftenhändler gelten", "Kapitalgewinne bleiben steuerfrei, solange du privat anlegst. Risikofaktoren: Haltedauer unter 6 Monaten, Jahresvolumen über dem Fünffachen des Depots, Kapitalgewinne über 50 % des Einkommens, Fremdfinanzierung oder Derivate über Absicherung hinaus. Langfristiges Buy-and-Hold ist unproblematisch."],
    en: ["Avoid being classed as a professional trader", "Capital gains stay tax-free as long as you invest privately. Risk factors: holding periods under 6 months, annual volume above five times the portfolio, gains above 50 % of income, leverage or derivatives beyond hedging. Long-term buy-and-hold is fine."] },
  { id: "crypto", cat: "tax", impact: 1,
    de: ["Krypto richtig deklarieren", "Bitcoin gehört ins Wertschriftenverzeichnis und unterliegt der Vermögenssteuer zum ESTV-Jahresendkurs. Private Kursgewinne sind steuerfrei, Staking- oder Mining-Erträge dagegen Einkommen. Kaufbelege (z.B. Relai-Export) aufbewahren."],
    en: ["Declare crypto correctly", "Bitcoin belongs in the securities list and is subject to wealth tax at the ESTV year-end rate. Private price gains are tax-free, staking or mining income is taxable. Keep purchase records (e.g. the Relai export)."] },
  { id: "zh-muni", cat: "tax", impact: 2, canton: "ZH",
    de: ["Wohngemeinde zählt", "Die Gemeindesteuerfüsse im Kanton Zürich reichen von rund 70 % bis 130 % der einfachen Staatssteuer. Bei einem Umzug lohnt sich ein Vergleich – auch Kirchensteuer, Mieten und Pendelkosten einbeziehen. Mit der hochgeladenen Steuerfuss-Tabelle vergleicht das Dashboard alle Gemeinden."],
    en: ["Your municipality matters", "Municipal tax rates in the Canton of Zurich range from about 70 % to 130 % of the simple cantonal tax. Compare when you move – including church tax, rents and commuting costs. With the uploaded rate table the dashboard compares all municipalities."],
    calc: (c) => c.inc > 0 && L(`Bei dir: 10 Prozentpunkte Gemeindesteuerfuss entsprechen ca. ${chf(c.muni10)} pro Jahr.`, `For you: 10 percentage points of municipal rate equal about ${chf(c.muni10)} per year.`) },
  { id: "zh-prepay", cat: "tax", impact: 1, canton: "ZH",
    de: ["Steuern im Kanton Zürich früh bezahlen", "Vorauszahlungen vor dem Fälligkeitstermin (30. September) werden mit einem Vergütungszins belohnt, verspätete Zahlungen kosten Verzugszins. Wer Liquidität hat, zahlt die provisorische Rechnung früh."],
    en: ["Pay Canton of Zurich taxes early", "Payments before the due date (30 September) earn interest, late payments cost default interest. If you have the cash, pay the provisional bill early."] },
  { id: "wealth-tax", cat: "tax", impact: 1, canton: "ZH",
    de: ["Vermögenssteuer im Blick", "Im Kanton Zürich ist Vermögen bis rund 80'000 CHF (Verheiratete etwa das Doppelte) steuerfrei. Guthaben in Säule 3a, Pensionskasse und Freizügigkeit sind von der Vermögenssteuer befreit – ein weiterer Grund, sie zu füllen."],
    en: ["Keep wealth tax in mind", "In the Canton of Zurich wealth up to about CHF 80,000 (roughly double for married couples) is tax-free. Balances in pillar 3a, pension fund and vested benefits are exempt from wealth tax – another reason to fill them."] },

  { id: "3a-accounts", cat: "pension", impact: 3,
    de: ["Mehrere 3a-Konten führen", "Ein 3a-Konto kann nur als Ganzes bezogen werden. Mit bis zu fünf Konten (z.B. bei Finpension oder VIAC) lassen sich die Bezüge ab 60 auf mehrere Steuerjahre verteilen und die Progression brechen. Neue Einzahlungen jeweils auf ein neues Konto lenken, bis fünf erreicht sind."],
    en: ["Hold several 3a accounts", "A 3a account can only be withdrawn in full. With up to five accounts (e.g. at Finpension or VIAC) withdrawals from age 60 can be spread over several tax years, breaking the progression. Direct new contributions to a new account until you have five."] },
  { id: "3a-invest", cat: "pension", impact: 3,
    de: ["3a in Wertschriften statt auf dem Zinskonto", "Bei einem Anlagehorizont von über 10 Jahren bringt eine 3a-Lösung mit hohem Aktienanteil und tiefen Kosten (TER unter 0.5 %) erfahrungsgemäss deutlich mehr als ein 3a-Zinskonto. Kurz vor dem Bezug schrittweise Risiko reduzieren."],
    en: ["Invest pillar 3a in securities", "With a horizon of more than 10 years, a low-cost 3a solution with a high equity share (TER under 0.5 %) has historically returned far more than a 3a savings account. Reduce risk gradually shortly before withdrawal."] },
  { id: "ik", cat: "pension", impact: 2,
    de: ["AHV-Kontoauszug bestellen", "Den individuellen Kontoauszug (IK) gratis bei der Ausgleichskasse bzw. auf ahv-iv.ch bestellen. Beitragslücken lassen sich nur 5 Jahre rückwirkend schliessen – jedes fehlende Jahr kürzt die AHV-Rente lebenslang um etwa 1/44."],
    en: ["Order your AHV account statement", "Order the individual account statement (IK) free of charge from your compensation office or via ahv-iv.ch. Contribution gaps can only be closed 5 years back – each missing year cuts the AHV pension by about 1/44 for life."] },
  { id: "pk-cert", cat: "pension", impact: 2,
    de: ["Vorsorgeausweis lesen", "Jährlich prüfen: Altersguthaben, Verzinsung, Umwandlungssatz (überobligatorisch oft deutlich unter 6.8 %), Einkaufspotenzial, Risikoleistungen und ob ein Kapitalbezug möglich ist und bis wann er angemeldet werden muss."],
    en: ["Read your pension certificate", "Check yearly: retirement assets, interest, conversion rate (often well below 6.8 % for extra-mandatory parts), buy-in potential, risk benefits and whether and by when a lump sum must be requested."] },
  { id: "vested", cat: "pension", impact: 2,
    de: ["Freizügigkeitsgeld clever parkieren", "Bei Stellenlücke, Weltreise, Selbstständigkeit oder FIRE das PK-Guthaben auf zwei Freizügigkeitsstiftungen aufteilen (später gestaffelter Bezug) und bei langem Horizont in Wertschriften anlegen. Vergessene Guthaben findet die Zentralstelle 2. Säule."],
    en: ["Park vested benefits wisely", "On a career break, sabbatical, self-employment or FIRE, split your pension assets across two vested-benefits foundations (staggered withdrawal later) and invest them in securities for long horizons. The Central Office 2nd Pillar finds forgotten balances."] },
  { id: "risk", cat: "pension", impact: 2,
    de: ["Risiken vor Rendite absichern", "Vor dem Optimieren prüfen: Erwerbsunfähigkeit und Todesfall (Partner/Kinder) ausreichend gedeckt? Im Konkubinat Begünstigung in PK und 3a schriftlich melden und ein Testament aufsetzen – sonst geht der Partner leer aus."],
    en: ["Cover risks before chasing returns", "Before optimising, check: are disability and death (partner/children) adequately covered? Unmarried couples should register the partner as beneficiary for pension fund and 3a and write a will – otherwise the partner gets nothing."] },

  { id: "costs", cat: "invest", impact: 3,
    de: ["Kosten sind die sicherste Rendite", "Ein Prozent jährliche Gebühren kostet über 30 Jahre rund einen Viertel des Endvermögens. Breite Index-ETFs mit tiefer TER, ein günstiger Broker und günstiger Währungswechsel (bei IBKR direkt im Konto) wirken garantiert."],
    en: ["Costs are the surest return", "One percent in annual fees costs roughly a quarter of the final wealth over 30 years. Broad index ETFs with low TER, a cheap broker and cheap currency conversion (directly in the IBKR account) pay off for certain."],
    calc: (c) => c.wealth > 0 && L(`Bei deinem Vermögen von ${chf(c.wealth)}: 0.5 % weniger Kosten sind ca. ${chf(c.wealth * 0.005)} pro Jahr.`, `With your wealth of ${chf(c.wealth)}: 0.5 % lower costs are about ${chf(c.wealth * 0.005)} per year.`) },
  { id: "stamp", cat: "invest", impact: 1,
    de: ["Stempelabgabe und Depotgebühren vermeiden", "Ausländische Broker wie IBKR erheben keine Schweizer Umsatzabgabe und keine Depotgebühr. Bei Schweizer Banken auf Pauschalen und Depotgebühren achten."],
    en: ["Avoid stamp duty and custody fees", "Foreign brokers such as IBKR charge no Swiss stamp duty and no custody fee. With Swiss banks watch out for flat and custody fees."] },
  { id: "emergency", cat: "invest", impact: 2,
    de: ["Notgroschen zuerst", "Drei bis sechs Monatsausgaben liquide halten (z.B. Sparkonto mit gutem Zins oder Geldmarktfonds). So musst du in einem Börsencrash nicht verkaufen."],
    en: ["Emergency fund first", "Keep three to six months of expenses liquid (e.g. a savings account with a decent rate or a money-market fund). That way you never have to sell in a crash."],
    calc: (c) => c.spend > 0 && L(`Bei dir: ${chf(c.spend / 4)} bis ${chf(c.spend / 2)}.`, `For you: ${chf(c.spend / 4)} to ${chf(c.spend / 2)}.`) },
  { id: "diversify", cat: "invest", impact: 2,
    de: ["Breit streuen, jährlich rebalancieren", "Weltweit diversifizieren statt Einzeltitel, den Schweiz-Anteil bewusst wählen und den Krypto-Anteil auf ein Mass begrenzen, dessen Totalverlust du verkraften würdest. Einmal pro Jahr zur Zielaufteilung zurückkehren."],
    en: ["Diversify broadly, rebalance yearly", "Diversify globally rather than picking single stocks, choose the Swiss share deliberately and cap crypto at a level whose total loss you could bear. Return to the target allocation once a year."] },
  { id: "us-etf", cat: "invest", impact: 1,
    de: ["ETF-Domizil beachten", "Irische ETFs verlieren 15 % US-Quellensteuer auf Fondsebene, die nicht zurückgeholt werden kann. US-domizilierte ETFs (z.B. via IBKR) erlauben die Anrechnung über DA-1, haben aber ein US-Erbschaftssteuerrisiko; das Abkommen Schweiz–USA mildert dieses. Erst ab grösseren Beträgen relevant."],
    en: ["Mind the ETF domicile", "Irish ETFs lose 15 % US withholding tax at fund level that can't be reclaimed. US-domiciled ETFs (e.g. via IBKR) allow crediting via DA-1 but carry US estate tax risk, mitigated by the Swiss–US treaty. Only relevant for larger amounts."] },

  { id: "health", cat: "life", impact: 3,
    de: ["Krankenkasse jedes Jahr optimieren", "Grundversicherung ist überall identisch – nur der Preis unterscheidet sich. Hausarzt- oder Telmed-Modelle sparen oft 10–20 %. Die höchste Franchise (2'500 CHF) lohnt sich meist, wenn deine jährlichen Gesundheitskosten unter etwa 2'000 CHF liegen – die Differenz dann als Reserve beiseitelegen."],
    en: ["Optimise health insurance every year", "Basic cover is identical everywhere – only the price differs. GP or telemedicine models often save 10–20 %. The highest deductible (CHF 2,500) usually pays off if your annual health costs are below about CHF 2,000 – set the difference aside as a reserve."] },
  { id: "accident", cat: "life", impact: 1,
    de: ["Unfalldeckung ausschliessen", "Wer mindestens 8 Stunden pro Woche beim gleichen Arbeitgeber angestellt ist, ist über UVG gegen Unfall versichert. Die Unfalldeckung in der Krankenkasse lässt sich dann ausschliessen – das spart einige Prozent Prämie."],
    en: ["Exclude accident cover", "Employees working at least 8 hours a week for one employer are covered by UVG accident insurance. The accident cover in the health insurance can then be excluded – saving a few percent of premium."] },
  { id: "insurance", cat: "life", impact: 1,
    de: ["Versicherungen entrümpeln", "Doppelte Deckungen suchen (Reise-, Annullierungs- und Mietwagenschutz über Kreditkarte, Privathaftpflicht im Haushalt), Hausrat-Summe an den echten Wert anpassen und Kombi-Rabatte vergleichen."],
    en: ["Declutter insurance", "Look for duplicate cover (travel, cancellation and rental car cover via credit card, liability within the household), match contents cover to actual value and compare bundle discounts."] },
  { id: "pay-first", cat: "life", impact: 2,
    de: ["Zuerst dich selbst bezahlen", "Am Lohntag automatisch per Dauerauftrag sparen und investieren (3a, ETF-Sparplan), statt am Monatsende den Rest. Die Sparquote ist der wichtigste Hebel für jedes Ziel."],
    en: ["Pay yourself first", "Save and invest automatically on payday by standing order (3a, ETF savings plan) instead of saving what's left at the end of the month. The savings rate is the biggest lever for any goal."],
    calc: (c) => c.inc > 0 && c.saveRate > 0 && L(`Deine Sparquote laut Profil: ca. ${pctS(c.saveRate, 0)} des steuerbaren Einkommens.`, `Your savings rate per profile: about ${pctS(c.saveRate, 0)} of taxable income.`) },
  { id: "fx-cards", cat: "life", impact: 1,
    de: ["Fremdwährungsgebühren vermeiden", "Viele Schweizer Karten schlagen 1.5–2.5 % auf Fremdwährungszahlungen. Für Ferien und Online-Einkäufe eine Karte ohne oder mit tiefem Aufschlag nutzen."],
    en: ["Avoid foreign-currency fees", "Many Swiss cards add 1.5–2.5 % on foreign-currency payments. Use a card with no or low markup for travel and online shopping."] },

  { id: "fire-number", cat: "fire", impact: 3,
    de: ["Deine FIRE-Zahl", "Benötigtes Vermögen = jährliche Ausgaben ÷ Entnahmerate. Die bekannte 4-%-Regel stammt aus US-Daten für 30 Jahre; für sehr frühe Pensionierungen und CHF-Anleger sind 3–3.5 % vorsichtiger."],
    en: ["Your FIRE number", "Required wealth = annual expenses ÷ withdrawal rate. The well-known 4 % rule comes from 30-year US data; for very early retirement and CHF investors 3–3.5 % is more cautious."],
    calc: (c) => c.spend > 0 && L(`Bei dir: ${chf(c.fireNum)} bei ${pctS(c.swr)} Entnahme.`, `For you: ${chf(c.fireNum)} at ${pctS(c.swr)} withdrawal.`) },
  { id: "fire-ahv", cat: "fire", impact: 3,
    de: ["AHV-Beiträge als Nichterwerbstätige zahlen", "Wer vor 65 aufhört zu arbeiten, bleibt bis zum Referenzalter beitragspflichtig – berechnet nach Vermögen (2026 von rund 530 bis 26'500 CHF pro Jahr). Selbst bei der Ausgleichskasse anmelden, sonst drohen Lücken und Nachforderungen. Verheiratete sind befreit, wenn der Partner mindestens den doppelten Mindestbeitrag einzahlt."],
    en: ["Pay AHV contributions as non-employed", "Anyone who stops working before 65 must keep paying contributions until reference age – based on wealth (2026 from about CHF 530 to 26,500 a year). Register with the compensation office yourself, or gaps and back claims follow. Married people are exempt if the partner pays at least twice the minimum."],
    calc: (c) => c.wealth > 0 && L(`Bei deinem heutigen Vermögen ca. ${chf(ahvNonEmployed(c.wealth))} pro Jahr (Näherung).`, `With your current wealth about ${chf(ahvNonEmployed(c.wealth))} per year (estimate).`) },
  { id: "fire-bridge", cat: "fire", impact: 3,
    de: ["Brücke bis 60 bzw. 65 planen", "3a ist frühestens 5 Jahre vor dem Referenzalter beziehbar, die PK je nach Reglement ab 58, die AHV ab 63 (mit Kürzung). Bis dahin muss freies Vermögen reichen. Dafür eignen sich Anlagen mit tiefer Ausschüttung: Kursgewinne sind steuerfrei, Dividenden und Zinsen nicht."],
    en: ["Plan the bridge to 60 or 65", "Pillar 3a can be withdrawn at the earliest 5 years before reference age, the pension fund from 58 depending on its rules, AHV from 63 (reduced). Until then free assets must suffice. Low-distribution investments suit this: price gains are tax-free, dividends and interest are not."] },
  { id: "fire-sequence", cat: "fire", impact: 2,
    de: ["Gegen schlechte Startjahre absichern", "Ein Crash in den ersten Jahren nach dem Ausstieg schadet am meisten. Puffer von 1–2 Jahresausgaben in Cash oder Obligationen halten und Ausgaben in schlechten Jahren flexibel senken."],
    en: ["Guard against bad first years", "A crash in the first years after quitting hurts most. Hold a buffer of 1–2 years of expenses in cash or bonds and cut spending flexibly in bad years."] },
  { id: "fire-part", cat: "fire", impact: 2,
    de: ["Teilzeit und Coast-FIRE", "Wer reduziert, verliert oft überproportional PK-Sparen (Koordinationsabzug). Bei Teilzeit nach einer PK fragen, die den Abzug anteilig kürzt, und Lücken mit 3a und Einkäufen schliessen."],
    en: ["Part-time and Coast FIRE", "Reducing hours often cuts pension savings disproportionately (coordination deduction). When working part-time ask for a pension fund that reduces the deduction proportionally and close gaps with 3a and buy-ins."] },
  { id: "fire-ipv", cat: "fire", impact: 1,
    de: ["Prämienverbilligung prüfen", "Nach dem Ausstieg ist das steuerbare Einkommen oft tief. Ob ein Anspruch auf Prämienverbilligung besteht, hängt im Kanton auch vom Vermögen ab – einmal prüfen lohnt sich."],
    en: ["Check premium subsidies", "After quitting, taxable income is often low. Whether you qualify for health premium subsidies also depends on wealth in your canton – worth a check."] },

  { id: "stagger", cat: "retire", impact: 3,
    de: ["Kapitalbezüge staffeln", "Kapitalleistungen aus PK, 3a und Freizügigkeit werden getrennt vom Einkommen, aber progressiv besteuert – und alle Bezüge eines Jahres (auch die des Ehepartners) zusammengezählt. Bezüge auf mehrere Jahre zwischen 60 und 70 verteilen spart oft fünfstellige Beträge."],
    en: ["Stagger lump-sum withdrawals", "Lump sums from pension fund, 3a and vested benefits are taxed separately from income but progressively – and all withdrawals in one year (including your spouse's) are added up. Spreading them over several years between 60 and 70 often saves five-figure amounts."] },
  { id: "lump-annuity", cat: "retire", impact: 3,
    de: ["Rente, Kapital oder Mischung", "Die Rente sichert lebenslang ab (Langlebigkeit) und ist als Einkommen voll steuerbar. Kapital ist flexibel und vererbbar, trägt aber Anlage- und Langlebigkeitsrisiko. Eine Mischung ist oft sinnvoll. Die Anmeldefrist für den Kapitalbezug steht im PK-Reglement (oft 1–3 Jahre vorher)."],
    en: ["Annuity, lump sum or a mix", "The annuity insures you for life (longevity) and is fully taxable as income. A lump sum is flexible and inheritable but carries investment and longevity risk. A mix often makes sense. The deadline to request a lump sum is in the fund's rules (often 1–3 years ahead)."] },
  { id: "ahv-flex", cat: "retire", impact: 2,
    de: ["AHV flexibel beziehen", "Bezug zwischen 63 und 70 möglich: Vorbezug kürzt die Rente lebenslang (6.8 % pro Jahr), Aufschub erhöht sie (5.2 % nach einem bis 31.5 % nach fünf Jahren). Wer nach 65 arbeitet, hat einen Freibetrag von 16'800 CHF und kann bis 70 weiter in die Säule 3a einzahlen. Rente 3–6 Monate vorher anmelden."],
    en: ["Draw AHV flexibly", "Possible between 63 and 70: drawing early cuts the pension for life (6.8 % per year), deferring raises it (5.2 % after one to 31.5 % after five years). Working past 65 gives an allowance of CHF 16,800 and allows 3a contributions until 70. Register 3–6 months ahead."] },
  { id: "mortgage-old", cat: "retire", impact: 1,
    de: ["Hypothek vor der Pensionierung prüfen", "Banken rechnen die Tragbarkeit im Alter mit dem tieferen Renteneinkommen. Amortisation und Verlängerung frühzeitig, noch mit Erwerbseinkommen, planen."],
    en: ["Review the mortgage before retiring", "Banks assess affordability in retirement against the lower pension income. Plan amortisation and renewal early, while still earning."] },
  { id: "move-retire", cat: "retire", impact: 1,
    de: ["Wohnort im Alter vergleichen", "Kapitalbezugs- und Vermögenssteuer unterscheiden sich zwischen Kantonen und Gemeinden stark. Massgebend ist der Wohnsitz im Zeitpunkt des Bezugs."],
    en: ["Compare where to live in retirement", "Lump-sum and wealth taxes differ widely between cantons and municipalities. What counts is your residence at the time of withdrawal."] },

  { id: "imputed-rent", cat: "future", impact: 2,
    de: ["Eigenmietwert fällt weg", "Das Stimmvolk hat am 28. September 2025 die Abschaffung des Eigenmietwerts angenommen; das Inkrafttreten ist noch festzulegen (frühestens 2028). Danach sind Schuldzinsen und Unterhaltskosten selbst bewohnter Liegenschaften nicht mehr abziehbar – grössere Renovationen eher vorziehen und die Amortisationsstrategie neu beurteilen."],
    en: ["Imputed rental value is going", "Voters approved abolishing the imputed rental value on 28 September 2025; the effective date is still to be set (2028 at the earliest). Afterwards, mortgage interest and maintenance on owner-occupied homes are no longer deductible – consider bringing larger renovations forward and rethink amortisation."] },
  { id: "individual", cat: "future", impact: 1,
    de: ["Individualbesteuerung kommt", "Am 8. März 2026 wurde die Individualbesteuerung angenommen; der Zeitpunkt der Umsetzung ist noch offen. Doppelverdiener-Ehepaare dürften entlastet, Einverdiener-Haushalte eher belastet werden. Bei der Aufteilung von Arbeitspensen langfristig mitdenken."],
    en: ["Individual taxation is coming", "Individual taxation was approved on 8 March 2026; the implementation date is still open. Dual-income couples should benefit, single-earner households may pay more. Factor this into long-term work-split decisions."] },
  { id: "ahv13", cat: "future", impact: 1,
    de: ["13. AHV-Rente ab Dezember 2026", "Die 13. AHV-Rente wird ab 2026 jeweils im Dezember ausbezahlt (ein Zwölftel der Jahresrente zusätzlich). Die Finanzierung ist politisch noch umstritten."],
    en: ["13th AHV pension from December 2026", "The 13th AHV pension is paid out every December from 2026 (an extra twelfth of the annual pension). Its financing is still politically disputed."] },
  { id: "pk-rates", cat: "future", impact: 2,
    de: ["Sinkende Umwandlungssätze einplanen", "Nach dem Nein zur BVG-Reform 2024 senken viele Kassen den überobligatorischen Umwandlungssatz weiter. Die erwartete PK-Rente lieber vorsichtig ansetzen und die private Vorsorge stärken."],
    en: ["Expect falling conversion rates", "After the BVG reform was rejected in 2024, many funds keep lowering the extra-mandatory conversion rate. Be conservative with the expected pension and strengthen private savings."] },
  { id: "lump-tax", cat: "future", impact: 1,
    de: ["Politik rund um Kapitalbezüge verfolgen", "Eine höhere Besteuerung von Kapitalbezügen aus der Vorsorge wird im Bund immer wieder diskutiert. Wer in den nächsten Jahren bezieht, sollte die Entwicklung verfolgen und flexibel bleiben."],
    en: ["Follow the politics of lump sums", "Higher taxation of pension lump sums keeps coming up at federal level. Anyone withdrawing in the coming years should follow developments and stay flexible."] },
  { id: "women65", cat: "future", impact: 1,
    de: ["Referenzalter Frauen", "Mit der AHV 21 steigt das Referenzalter der Frauen schrittweise auf 65 (Jahrgänge 1961–1963 als Übergang, mit Rentenzuschlag). Für Paare beeinflusst das die Planung von Bezügen und Brücke."],
    en: ["Women's reference age", "Under AHV 21 the women's reference age rises gradually to 65 (birth years 1961–1963 transitional, with a pension supplement). For couples this affects the timing of withdrawals and the bridge."] },
];

/* ---------- Daten laden & speichern ---------- */
async function loadPlan() {
  if (plan.loading) return;
  plan.loading = true;
  try {
    if (!plan.P) {
      const saved = await api("/api/plan").catch(() => ({}));
      plan.P = { ...PLAN_DEFAULTS, ...saved, done: { ...(saved.done || {}) }, cfg3a: { ...(saved.cfg3a || {}) } };
      plan.rates = await api("/api/muni-rates").catch(() => ({}));
    }
    renderPlan();
    plan.dash = await loadPlanDash();
    renderPlanResults();
  } catch (e) { toast(e.message); }
  plan.loading = false;
}
/* Vermögen aus allen Portfolios und 3a-Einzahlungen im laufenden Jahr */
async function loadPlanDash() {
  const pfs = await api("/api/portfolios");
  const year = String(new Date().getFullYear());
  const out = { free: 0, pf3a: [], base: "CHF", count: pfs.length };
  await Promise.all(pfs.map(async (p) => {
    const s = await api(`/api/summary?p=${p.id}`).catch(() => null);
    if (!s) return;
    out.base = s.base;
    const v = s.kpis.value || 0;
    if (p.kind === "3a") {
      const x = { id: p.id, name: p.name, sort: pfs.indexOf(p), value: v, dep: 0 };
      const txs = await api(`/api/transactions?p=${p.id}`).catch(() => []);
      for (const t of txs) if (t.type === "deposit" && (t.date || "").startsWith(year))
        x.dep += Math.abs(t.total || (t.quantity || 0) * (t.price || 0));
      out.pf3a.push(x);
    } else out.free += v;
  }));
  out.pf3a.sort((a, b) => a.sort - b.sort);
  return out;
}
function savePlan() {
  clearTimeout(plan.saveT);
  plan.saveT = setTimeout(() => api("/api/plan", { method: "POST", body: plan.P }).catch((e) => toast(e.message)), 700);
}

/* ---------- Darstellung ---------- */
function field(k, label, opts = {}) {
  const v = plan.P[k];
  const hint = opts.hint ? `<span class="hint">${opts.hint}</span>` : "";
  if (opts.select) return `<label class="field">${label}<select data-k="${k}">${opts.select.map(([val, t]) =>
    `<option value="${val}" ${String(v) === String(val) ? "selected" : ""}>${t}</option>`).join("")}</select>${hint}</label>`;
  if (opts.check) return `<label class="check full"><input type="checkbox" data-k="${k}" ${v ? "checked" : ""}> <span>${label}</span></label>`;
  return `<label class="field">${label}<input type="number" inputmode="decimal" step="any" data-k="${k}" value="${esc(v)}"
    ${opts.ph != null ? `placeholder="${esc(opts.ph)}"` : ""} class="${opts.amt ? "amt" : ""}">${hint}</label>`;
}
/* Gemeinde aus hochgeladener Steuerfuss-Tabelle oder manuelle Steuerfüsse */
function muniFields(c) {
  const P = plan.P, t = muniTable(), vs = muniVariants(), r = rates();
  let h = "";
  if (t) {
    if (t.years.length > 1) h += field("taxYear", L("Steuerjahr der Steuerfüsse", "Tax year of the rates"), { select: t.years.map((y) => [y, y]) });
    h += `<label class="field">${L("Wohngemeinde", "Municipality")}<select data-k="gemeinde">
      <option value="">${L("– manuell eingeben –", "– enter manually –")}</option>
      ${Object.keys(t.rows).sort((a, b) => a.localeCompare(b, "de")).map((n) => `<option ${n === P.gemeinde ? "selected" : ""}>${esc(n)}</option>`).join("")}
      </select><span class="hint">${vs ? L(`Steuerfuss ${num(r.muni, 0)} %${r.church ? ` + Kirche ${num(r.church, 0)} %` : ""} (${t.year})`, `Rate ${num(r.muni, 0)} %${r.church ? ` + church ${num(r.church, 0)} %` : ""} (${t.year})`) : L(`Tabelle ${t.year} mit ${Object.keys(t.rows).length} Gemeinden`, `${t.year} table with ${Object.keys(t.rows).length} municipalities`)}</span></label>`;
    if (vs) {
      h += field("konf", L("Konfession (Kirchensteuer)", "Denomination (church tax)"), { select: KONF.map(([k, de, en]) => [k, L(de, en)]) });
      if (vs.length > 1) h += field("gemVar", L("Steuerfuss im Gemeindegebiet", "Rate within municipality"), {
        select: vs.map((v, i) => [i, L(`Variante ${i + 1}: ${num(v[0], 0)} %`, `Variant ${i + 1}: ${num(v[0], 0)} %`)]),
        hint: L("je nach Schul- oder Kirchgemeinde – siehe Steuerrechnung", "depends on school or parish district – see tax bill") });
    }
  }
  if (!vs) h += field("muni", L("Gemeindesteuerfuss (%)", "Municipal tax rate (%)"), { hint: L(`Stadt Zürich: ${c.muni} %`, `City of Zurich: ${c.muni} %`) })
    + field("church", L("Kirchensteuerfuss (%)", "Church tax rate (%)"), { hint: L("0 = keine Kirchensteuer", "0 = no church tax") });
  const all = (plan.rates || {})[P.canton] || {};
  h += `<div class="plan-upload">
    <label class="btn ghost small" for="plMuniFile">${L("Steuerfüsse-CSV hochladen …", "Upload tax rate CSV …")}</label>
    <input type="file" id="plMuniFile" accept=".csv,text/csv" hidden>
    ${Object.keys(all).sort().map((y) => `<span class="active-chip">${y}<button type="button" class="plan-x" data-delyear="${y}" title="${L("Tabelle löschen", "Delete table")}" aria-label="${L("Tabelle löschen", "Delete table")} ${y}">✕</button></span>`).join("")}
    <span class="hint">${L("CSV der Gemeindesteuerfüsse vom Kantonalen Steueramt (zh.ch/steuerfuesse). Das Jahr wird aus dem Dateinamen gelesen.",
      "CSV of municipal tax rates from the cantonal tax office (zh.ch/steuerfuesse). The year is read from the file name.")}</span></div>`;
  return h;
}

/* CSV der Gemeindesteuerfüsse (Kanton Zürich, Semikolon) einlesen */
function parseCsvLine(line, sep) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) { if (ch === '"') { if (line[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === sep) { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur);
  return out.map((x) => x.trim());
}
function parseMuniCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) throw new Error(L("Die Datei ist leer.", "The file is empty."));
  const sep = (lines[0].match(/;/g) || []).length >= (lines[0].match(/,/g) || []).length ? ";" : ",";
  const head = parseCsvLine(lines[0], sep).map((h) => h.toLowerCase());
  const iName = head.findIndex((h) => h === "gemeinde" || h.startsWith("gemeinde"));
  const col = (kind, n) => head.findIndex((h) => h.startsWith(`${n}.`) && h.includes("gesamtsteuerfuss") && (
    kind === "none" ? h.includes("ohne kirche") : kind === "christkath" ? h.includes("christkath") : kind === "kath" ? h.includes("kath.") && !h.includes("christkath") : h.includes("ref.")));
  const cols = KONF.map(([k]) => [1, 2, 3].map((n) => col(k, n)));
  if (iName < 0 || cols[0][0] < 0) throw new Error(L("Spalten „Gemeinde“ und „1. Gesamtsteuerfuss ohne Kirche“ nicht gefunden.", "Columns “Gemeinde” and “1. Gesamtsteuerfuss ohne Kirche” not found."));
  const numv = (x) => { const v = parseFloat(String(x || "").replace(",", ".")); return isFinite(v) ? v : null; };
  const rows = {};
  for (const line of lines.slice(1)) {
    const f = parseCsvLine(line, sep), name = f[iName];
    if (!name) continue;
    const vals = cols.map((cs) => cs.map((i) => (i >= 0 ? numv(f[i]) : null)));
    if (vals[0][0] == null) continue;
    const n = Math.max(...vals.map((v) => (v[2] != null ? 3 : v[1] != null ? 2 : 1)));
    const vs = Array.from({ length: n }, (_, j) => vals.map((v) => (v[j] != null ? v[j] : v[0])));
    rows[name] = vs.filter((v, j) => vs.findIndex((w) => w.join() === v.join()) === j);   // identische Varianten nur einmal
  }
  if (!Object.keys(rows).length) throw new Error(L("Keine Steuerfüsse gefunden", "No tax rates found"));
  return rows;
}
async function uploadMuniCsv(file) {
  const buf = await file.arrayBuffer();
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(buf); } catch { text = new TextDecoder("windows-1252").decode(buf); }
  const rows = parseMuniCsv(text);
  const year = (file.name.match(/20\d\d/) || [String(new Date().getFullYear())])[0];
  await api("/api/muni-rates", { method: "POST", body: { canton: plan.P.canton, year, rates: rows } });
  plan.rates = await api("/api/muni-rates");
  plan.P.taxYear = year;
  if (!plan.P.gemeinde) {   // bisherigen Gemeindesteuerfuss einer Gemeinde zuordnen, wenn eindeutig
    const hit = Object.keys(rows).filter((n) => rows[n][0][0] === nv(plan.P.muni, -1));
    if (hit.length === 1) plan.P.gemeinde = hit[0];
  }
  savePlan(); renderPlan();
  toast(L(`Steuerfüsse ${year} gespeichert (${Object.keys(rows).length} Gemeinden)`, `Tax rates ${year} saved (${Object.keys(rows).length} municipalities)`));
}

function renderPlan() {
  const root = $("#planView");
  if (!root || !plan.P) return;
  const P = plan.P, c = canton();
  root.innerHTML = `
  <div class="settings-head">
    <button class="btn ghost" id="planBack">${L("← Zurück zum Dashboard", "← Back to dashboard")}</button>
    <div>
      <h2>${L("Spartipps & Vorsorge", "Savings tips & retirement")}</h2>
      <p class="hint">${L(`Steuern, Vorsorge, FIRE und Pensionierung · Kanton ${c.name[0]} · Zahlen Stand ${PLAN_YEAR}`, `Taxes, pensions, FIRE and retirement · Canton of ${c.name[1]} · figures as of ${PLAN_YEAR}`)}</p>
    </div>
    <label class="field plan-canton">${L("Kanton", "Canton")}<select data-k="canton">${Object.entries(CANTONS).map(([k, x]) =>
      `<option value="${k}" ${k === P.canton ? "selected" : ""}>${L(x.name[0], x.name[1])}</option>`).join("")}
      <option disabled>${L("weitere folgen …", "more to come …")}</option></select></label>
  </div>
  <p class="plan-note">${L("Alle Berechnungen sind Näherungen mit vereinfachten Tarifen und ersetzen keine Steuer- oder Anlageberatung. Massgebend sind die Veranlagung und die Reglemente deiner Vorsorgeeinrichtungen.",
    "All calculations are estimates using simplified tariffs and are no substitute for tax or investment advice. The tax assessment and your pension funds' rules are what count.")}</p>

  <section class="plan-grid">
    <div class="plan-col">
    <article class="card">
      <div class="card-head"><h2>${L("Als Nächstes", "Coming up")}</h2></div>
      <div id="plA3"></div>
      <ul class="plan-dl" id="plDeadlines"></ul>
    </article>
    <article class="card">
      <div class="card-head"><h2>${L("Steuern sparen", "Save taxes")}</h2></div>
      <div id="plTax"></div>
    </article>
    </div>
    <div class="plan-col">
    <article class="card">
      <div class="card-head"><h2>${L("Dein Profil", "Your profile")}</h2><span class="hint">${L("wird automatisch gespeichert", "saved automatically")}</span></div>
      <form class="plan-form" id="planForm" novalidate>
        <h4>${L("Steuern", "Taxes")}</h4>
        ${field("civil", L("Zivilstand", "Marital status"), { select: [["single", L("Alleinstehend", "Single")], ["married", L("Verheiratet", "Married")], ["parent", L("Alleinerziehend", "Single parent")]] })}
        ${field("kids", L("Kinder", "Children"))}
        ${field("income", L("Steuerbares Einkommen (CHF)", "Taxable income (CHF)"), { amt: true, hint: L("nach Abzügen – steht auf der letzten Steuerrechnung", "after deductions – see your last tax bill") })}
        ${muniFields(c)}
        ${field("marg", L("Eigener Grenzsteuersatz (%)", "Own marginal tax rate (%)"), { ph: L("automatisch", "automatic"), hint: L("optional, überschreibt die Schätzung", "optional, overrides the estimate") })}
        <h4>${L("Vorsorge", "Pension")}</h4>
        ${field("age", L("Alter", "Age"))}
        ${field("buyIn", L("Geplanter PK-Einkauf (CHF)", "Planned pension buy-in (CHF)"), { amt: true })}
        ${field("buyYears", L("Einkauf verteilt auf Jahre", "Buy-in spread over years"))}
        ${field("earned", L("Erwerbseinkommen (CHF)", "Earned income (CHF)"), { amt: true, hint: L("nur ohne PK relevant (3a: 20 %)", "only relevant without pension fund (3a: 20 %)") })}
        ${field("pk", L("In einer Pensionskasse versichert", "Insured in a pension fund"), { check: true })}
        <h4>${L("FIRE & Pensionierung", "FIRE & retirement")}</h4>
        ${field("spend", L("Ausgaben pro Jahr (CHF)", "Expenses per year (CHF)"), { amt: true })}
        ${field("save", L("Sparrate pro Jahr ohne 3a (CHF)", "Savings per year excl. 3a (CHF)"), { amt: true })}
        ${field("ret", L("Erwartete Rendite real (% p.a.)", "Expected real return (% p.a.)"), { hint: L("nach Teuerung und Kosten", "after inflation and costs") })}
        ${field("swr", L("Entnahmerate (%)", "Withdrawal rate (%)"))}
        ${field("fireAge", L("Wunschalter für den Ausstieg", "Target age to stop working"))}
        ${field("ahv", L("Erwartete AHV-Rente pro Jahr (CHF)", "Expected AHV pension per year (CHF)"), { amt: true, hint: L(`Maximum Einzelperson ca. ${num(CHV.ahvMax, 0)}`, `Maximum single about ${num(CHV.ahvMax, 0)}`) })}
        ${field("pkRente", L("Erwartete PK-Rente pro Jahr (CHF)", "Expected pension fund annuity (CHF)"), { amt: true, hint: L("laut Vorsorgeausweis; bei frühem Ausstieg tiefer", "per pension certificate; lower if you quit early") })}
        ${field("wealthFree", L("Freies Vermögen (CHF)", "Free assets (CHF)"), { amt: true, ph: L("aus Dashboard", "from dashboard"), hint: L("leer = Summe der normalen Portfolios", "empty = sum of standard portfolios") })}
        ${field("wealth3a", L("Säule-3a-Guthaben (CHF)", "Pillar 3a balance (CHF)"), { amt: true, ph: L("aus Dashboard", "from dashboard"), hint: L("leer = gewähltes 3a-Portfolio (beim Vergleich die Summe)", "empty = selected 3a portfolio (sum when comparing)") })}
        ${field("a3full", L("Bis zum Ausstieg jedes Jahr 3a voll einzahlen", "Max out 3a every year until quitting"), { check: true })}
      </form>
    </article>
    <article class="card">
      <div class="card-head"><h2>${L("FIRE & Pensionierung", "FIRE & retirement")}</h2></div>
      <div id="plFire"></div>
    </article>
    </div>
  </section>

  <article class="card">
    <div class="card-head">
      <h2>${L("Tipps & Tricks", "Tips & tricks")}</h2>
      <label class="check"><input type="checkbox" id="plHideDone" ${P.hideDone ? "checked" : ""}> <span>${L("Erledigte ausblenden", "Hide done")}</span></label>
    </div>
    <div class="plan-chips" id="plCats" role="tablist" aria-label="${L("Kategorien", "Categories")}"></div>
    <div class="plan-tips" id="plTips"></div>
  </article>`;
  root.classList.toggle("hide-amts", !!state.hide);
  $("#planBack").onclick = () => { location.hash = ""; };
  root.querySelectorAll("[data-k]").forEach((el) => {
    el.addEventListener(el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input", () => {
      const k = el.dataset.k;
      plan.P[k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : el.value === "" ? "" : Number(el.value);
      if (["canton", "gemeinde", "taxYear", "konf", "gemVar"].includes(k)) { if (k === "gemeinde") plan.P.gemVar = 0; renderPlan(); }
      renderPlanResults(); savePlan();
    });
  });
  $("#plMuniFile").onchange = async (e) => {
    const f = e.target.files[0]; e.target.value = "";
    if (f) try { await uploadMuniCsv(f); } catch (ex) { toast(ex.message, 6000); }
  };
  root.querySelectorAll("[data-delyear]").forEach((b) => (b.onclick = async () => {
    const y = b.dataset.delyear;
    if (!(await confirmDialog(L(`Steuerfüsse ${y} löschen?`, `Delete tax rates ${y}?`), L("Die hochgeladene Tabelle wird entfernt. Du kannst sie jederzeit neu hochladen.", "The uploaded table is removed. You can upload it again at any time."), L("Löschen", "Delete")))) return;
    try { await api("/api/muni-rates", { method: "POST", body: { canton: plan.P.canton, year: y, delete: true } }); plan.rates = await api("/api/muni-rates"); renderPlan(); }
    catch (ex) { toast(ex.message); }
  }));
  $("#plHideDone").onchange = (e) => { plan.P.hideDone = e.target.checked; renderTips(); savePlan(); };
  renderPlanResults();
}

function planCtx() {
  const P = plan.P, inc = Math.max(0, nv(P.income)), a3 = a3Limit();
  const buy = Math.max(0, nv(P.buyIn)), years = Math.max(1, Math.min(10, Math.round(nv(P.buyYears, 1))));
  const r = rates(), t = taxes(inc, P, r), w = wealthNow();
  const muni10 = t.simple * 0.10;
  return {
    inc, a3, t, r, buy, years, muni10, spend: nv(P.spend), swr: nv(P.swr, 3.5),
    marg: marginal(inc), save3a: saving(inc, a3), saveBack: saving(Math.max(0, inc - a3), CHV.a3Small),
    buyOnce: saving(inc, buy), buySplit: saving(inc, buy / years) * years,
    wealth: w.free + w.p3, w, fireNum: nv(P.spend) / (nv(P.swr, 3.5) / 100 || 0.035),
    saveRate: inc ? (nv(P.save) + (P.a3full ? a3 : 0)) / inc * 100 : 0,
  };
}

function renderPlanResults() {
  if (!$("#plTax")) return;
  const c = planCtx();
  renderDeadlines(c); renderTax(c); renderFire(c); renderTips(c);
}

function renderDeadlines(c) {
  const d = plan.dash, box = $("#plA3"), list = (d && d.pf3a) || [];
  if (list.length) {
    const year = new Date().getFullYear(), cmp = list.length > 1 && sel3a().length > 1;
    const tabs = list.length > 1 ? `<div class="plan-chips plan-a3-tabs" role="tablist" aria-label="${L("3a-Portfolio wählen", "Choose 3a portfolio")}">
      ${[["cmp", L("Vergleich", "Compare")], ...list.map((x) => [String(x.id), esc(x.name)])].map(([k, t]) => {
        const on = k === "cmp" ? cmp : !cmp && String(sel3a()[0].id) === k;
        return `<button role="tab" data-pf3a="${k}" class="${on ? "on" : ""}" aria-selected="${on}">${t}</button>`;
      }).join("")}</div>` : "";
    const row = (x) => {
      const cf = cfg3a(x.id), lim = a3Limit(cf), pct = lim ? Math.min(100, x.dep / lim * 100) : 0;
      const rest = Math.max(0, lim - x.dep), over = x.dep - lim, open = plan.open3a.has(String(x.id));
      return `<div class="plan-a3">
        <div class="plan-a3-head"><b>${esc(x.name)} · ${year}</b><span>${chf(x.dep)} / ${chf(lim)}</span></div>
        <div class="plan-bar"><i style="width:${pct.toFixed(1)}%"></i></div>
        <p class="hint">${L(`Guthaben ${chf(x.value)}`, `Balance ${chf(x.value)}`)} · ${rest > 0
          ? L(`noch ${chf(rest)} einzahlbar – spart ca. ${chf(save3aFor(x.id, rest))} Steuern.`, `${chf(rest)} still possible – saves about ${chf(save3aFor(x.id, rest))} in tax.`)
          : over > 1 ? L(`${chf(over)} über dem Maximum – Einzahlungen prüfen (Nachzahlung für Lückenjahre oder Fondswechsel als Einzahlung erfasst?).`, `${chf(over)} above the maximum – check deposits (catch-up for gap years or a fund switch recorded as deposit?).`)
          : L("Maximum erreicht – allenfalls Lücken aus Vorjahren nachzahlen.", "Maximum reached – consider catching up on earlier gaps.")}</p>
        <details class="plan-a3-cfg" data-cfg="${x.id}" ${open ? "open" : ""}><summary>${L("Einstellungen für dieses Konto", "Settings for this account")}
          <span class="hint">${cf.pk ? L("mit PK", "with pension fund") : L("ohne PK", "no pension fund")} · ${cf.income === "" ? L("Einkommen aus Profil", "income from profile") : L(`Einkommen ${chf(nv(cf.income))}`, `income ${chf(nv(cf.income))}`)}</span></summary>
          <div class="plan-form">
            <label class="check full"><input type="checkbox" data-c3="pk" ${cf.pk ? "checked" : ""}> <span>${L("Inhaber/in ist in einer Pensionskasse versichert", "Holder is insured in a pension fund")}</span></label>
            <label class="field">${L("Erwerbseinkommen (CHF)", "Earned income (CHF)")}<input type="number" inputmode="decimal" step="any" class="amt" data-c3="earned" value="${esc(cf.earned)}" ${cf.pk ? "disabled" : ""}><span class="hint">${L("ohne PK: 3a-Maximum = 20 %", "no pension fund: 3a max = 20 %")}</span></label>
            <label class="field">${L("Steuerbares Einkommen (CHF)", "Taxable income (CHF)")}<input type="number" inputmode="decimal" step="any" class="amt" data-c3="income" value="${esc(cf.income)}" placeholder="${L("aus Profil", "from profile")}"><span class="hint">${L("Verheiratete: leer lassen (gemeinsame Veranlagung)", "Married: leave empty (joint assessment)")}</span></label>
          </div></details></div>`;
    };
    box.innerHTML = tabs + sel3a().map(row).join("") + (cmp ? `<p class="hint plan-a3">${L(
      `Jedes Konto wird einzeln gegen sein eigenes Maximum gerechnet. Für den FIRE-Rechner zählt hier die Summe von ${chf(sel3a().reduce((a, x) => a + x.value, 0))}.`,
      `Each account is checked separately against its own maximum. The FIRE calculator uses the total of ${chf(sel3a().reduce((a, x) => a + x.value, 0))}.`)}</p>` : "");
    box.querySelectorAll("details[data-cfg]").forEach((dt) => {
      const id = dt.dataset.cfg;
      dt.ontoggle = () => { if (dt.open) plan.open3a.add(id); else plan.open3a.delete(id); };
      dt.querySelectorAll("[data-c3]").forEach((el) => (el.onchange = () => {
        const cur = { ...(plan.P.cfg3a[id] || {}) }, k = el.dataset.c3;
        cur[k] = el.type === "checkbox" ? el.checked : el.value === "" ? "" : Number(el.value);
        if (k === "income" && cur.income === "") delete cur.income;
        plan.P.cfg3a[id] = cur; renderPlanResults(); savePlan();
      }));
    });
    box.querySelectorAll("[data-pf3a]").forEach((b) => (b.onclick = () => { plan.P.pf3a = b.dataset.pf3a; renderPlanResults(); savePlan(); }));
  } else box.innerHTML = d ? `<p class="hint plan-a3">${L("Lege ein Portfolio vom Typ „Säule 3a“ an und importiere die Einzahlungen, dann siehst du hier, wie viel du dieses Jahr noch einzahlen kannst.",
    "Create a portfolio of type “Pillar 3a” and import the deposits to see how much you can still pay in this year.")}</p>` : "";
  $("#plDeadlines").innerHTML = nextDeadlines().map((x) => `<li class="${x.days <= 30 ? "soon" : ""}">
    <div class="plan-dl-date"><b>${x.dt.getDate()}.</b><span>${x.dt.toLocaleDateString(LOCALE, { month: "short" })}</span></div>
    <div><b>${L(x.de, x.en)}</b> <span class="plan-days">${x.days === 0 ? L("heute", "today") : L(`in ${x.days} Tagen`, `in ${x.days} days`)}</span>
    <p class="hint">${L(x.tde, x.ten)}</p></div></li>`).join("");
}

function kv(label, value, sub = "") {
  return `<div class="plan-kv"><span>${label}</span><b>${value}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
}
/* Gemeindevergleich mit der hochgeladenen Tabelle (gleiche Konfession, jeweils erster Steuerfuss) */
function muniCompare(c) {
  const t = muniTable();
  if (!t) return `<p class="hint">${L("Tipp: Lade die CSV der Gemeindesteuerfüsse hoch (Profil → Steuern), dann rechnet das Dashboard mit deiner Gemeinde und vergleicht alle Gemeinden.",
    "Tip: upload the municipal tax rate CSV (profile → taxes) to calculate with your municipality and compare all municipalities.")}</p>`;
  const list = Object.keys(t.rows).map((n) => { const r = rates(plan.P, n, 0); return { n, total: r.muni + r.church }; })
    .sort((a, b) => a.total - b.total || a.n.localeCompare(b.n, "de"));
  const mine = c.r.muni + c.r.church, unit = c.t.simple / 100, rank = list.filter((x) => x.total < mine).length + 1;
  const diff = (x) => (mine - x.total) * unit;
  const pick = [...list.slice(0, 3), ...list.slice(-1)];
  return `<h4 class="plan-sub">${L(`Gemeindevergleich ${t.year}`, `Municipality comparison ${t.year}`)}</h4>
    <p class="hint">${plan.P.gemeinde && c.r.csv ? L(`${esc(plan.P.gemeinde)}: ${num(mine, 0)} % – Platz ${rank} von ${list.length} (1 = am günstigsten).`, `${esc(plan.P.gemeinde)}: ${num(mine, 0)} % – rank ${rank} of ${list.length} (1 = cheapest).`)
      : L(`Dein Steuerfuss ${num(mine, 0)} % – Platz ${rank} von ${list.length}.`, `Your rate ${num(mine, 0)} % – rank ${rank} of ${list.length}.`)}</p>
    <table class="table plan-table"><tbody>${pick.map((x) => `<tr><td>${esc(x.n)} <span class="hint">${num(x.total, 0)} %</span></td>
      <td class="num ${diff(x) > 0 ? "up" : diff(x) < 0 ? "down" : ""}">${diff(x) > 0 ? L(`${chf(diff(x))} weniger`, `${chf(diff(x))} less`) : diff(x) < 0 ? L(`${chf(-diff(x))} mehr`, `${chf(-diff(x))} more`) : "–"}</td></tr>`).join("")}</tbody></table>`;
}
function renderTax(c) {
  const box = $("#plTax");
  if (!c.inc) { box.innerHTML = `<p class="hint">${L("Gib im Profil dein steuerbares Einkommen ein.", "Enter your taxable income in the profile.")}</p>`; return; }
  const own = plan.P.marg !== "" && isFinite(Number(plan.P.marg));
  const rows = [
    [L(`3a-Einzahlung ${chf(c.a3)}`, `3a contribution ${chf(c.a3)}`), c.save3a],
    [L(`3a-Nachzahlung ${chf(CHV.a3Small)} (Lückenjahr)`, `3a catch-up ${chf(CHV.a3Small)} (gap year)`), c.saveBack],
  ];
  if (c.buy > 0) {
    rows.push([L(`PK-Einkauf ${chf(c.buy)} auf einmal`, `Buy-in ${chf(c.buy)} at once`), c.buyOnce]);
    if (c.years > 1) rows.push([L(`… verteilt auf ${c.years} Jahre`, `… spread over ${c.years} years`), c.buySplit]);
  }
  box.innerHTML = `<div class="plan-kvs">
      ${kv(L("Einkommenssteuer total", "Total income tax"), chf(c.t.total), L(`${pctS(c.t.total / c.inc * 100)} im Schnitt`, `${pctS(c.t.total / c.inc * 100)} on average`))}
      ${kv(L("Grenzsteuersatz", "Marginal tax rate"), pctS(c.marg * 100), own ? L("eigener Wert", "own value") : L("auf die nächsten 1'000 CHF", "on the next CHF 1,000"))}
      ${kv(L("Kanton & Gemeinde", "Canton & municipality"), chf(c.t.cant), L(`Steuerfuss ${num(canton().rate + c.r.muni + c.r.church, 0)} %`, `Rate ${num(canton().rate + c.r.muni + c.r.church, 0)} %`))}
      ${kv(L("Direkte Bundessteuer", "Federal income tax"), chf(c.t.fed))}
    </div>
    <table class="table plan-table"><thead><tr><th>${L("Massnahme", "Measure")}</th><th class="num">${L("Ersparnis pro Jahr", "Saving per year")}</th></tr></thead>
    <tbody>${rows.map(([l, v]) => `<tr><td>${l}</td><td class="num up">${chf(v)}</td></tr>`).join("")}</tbody></table>
    ${c.buy > 0 && c.years > 1 && c.buySplit > c.buyOnce + 50 ? `<p class="plan-win">${L(`Verteilt sparst du ca. ${chf(c.buySplit - c.buyOnce)} mehr als mit einem einmaligen Einkauf.`, `Spreading saves about ${chf(c.buySplit - c.buyOnce)} more than a single buy-in.`)}</p>` : ""}
    ${muniCompare(c)}
    <p class="hint">${L("Bundessteuer nach amtlichem Tarif 2026, Staatssteuer mit vereinfachtem Tarif; ohne Vermögens- und Personalsteuer. Abweichungen zur Steuerrechnung von einigen Prozent sind möglich.",
      "Federal tax per the official 2026 tariff, cantonal tax with a simplified tariff; excludes wealth and poll tax. Deviations of a few percent from your tax bill are possible.")}</p>`;
}

function renderFire(c) {
  const box = $("#plFire"), P = plan.P;
  if (!c.spend) { box.innerHTML = `<p class="hint">${L("Gib im Profil deine jährlichen Ausgaben ein.", "Enter your annual expenses in the profile.")}</p>`; return; }
  const a0 = Math.round(nv(P.age, 35)), fa = Math.max(a0, Math.round(nv(P.fireAge, 65)));
  const sim = simulate(fa), sim65 = simulate(Math.max(a0, CHV.refAge)), early = earliestFire();
  const prog = Math.min(100, c.wealth / c.fireNum * 100);
  const gap = Math.max(0, c.spend - nv(P.ahv) - nv(P.pkRente));
  const ahvAvg = sim.ahvCost.length ? sim.ahvCost.reduce((a, b) => a + b, 0) / sim.ahvCost.length : 0;
  const ages = [...new Set([a0, fa, 60, 65, 75, 85, 95])].filter((x) => x >= a0 && x <= 95).sort((a, b) => a - b);
  const status = (s) => (s.ok ? `<span class="up">${L("reicht bis 95 ✓", "lasts to 95 ✓")}</span>` : `<span class="down">${L(`aufgebraucht mit ${s.broke}`, `depleted at ${s.broke}`)}</span>`);
  box.innerHTML = `<div class="plan-kvs">
      ${kv(L("FIRE-Zahl", "FIRE number"), chf(c.fireNum), L(`Ausgaben ÷ ${pctS(c.swr)}`, `expenses ÷ ${pctS(c.swr)}`))}
      ${kv(L("Frühestes Ausstiegsalter", "Earliest exit age"), early != null ? String(early) : L("über 70", "over 70"), L("Vermögen reicht bis 95", "wealth lasts to 95"))}
    </div>
    <div class="plan-a3-head"><span>${L("Fortschritt", "Progress")} ${chf(c.wealth)}</span><b>${pctS(prog, 0)}</b></div>
    <div class="plan-bar"><i style="width:${prog.toFixed(1)}%"></i></div>
    <table class="table plan-table"><tbody>
      <tr><td>${L(`Ausstieg mit ${fa}`, `Stop at ${fa}`)}</td><td class="num">${status(sim)}</td></tr>
      <tr><td>${L("Normale Pensionierung mit 65", "Regular retirement at 65")}</td><td class="num">${status(sim65)}</td></tr>
      ${fa < CHV.refAge ? `<tr><td>${L(`Brücke bis 65: ${CHV.refAge - fa} Jahre, AHV-Beiträge als Nichterwerbstätige`, `Bridge to 65: ${CHV.refAge - fa} years, AHV contributions as non-employed`)}</td><td class="num">${L(`ca. ${chf(ahvAvg)}/J.`, `≈ ${chf(ahvAvg)}/yr`)}</td></tr>` : ""}
      <tr><td>${L("Rentenlücke ab 65 (Ausgaben − AHV − PK)", "Pension gap from 65 (expenses − AHV − pension fund)")}</td><td class="num">${chf(gap)}${L("/J.", "/yr")}</td></tr>
      <tr><td>${L("Dafür nötiges Kapital mit 65", "Capital needed at 65 for that")}</td><td class="num">${chf(gap / (c.swr / 100 || 0.035))}</td></tr>
    </tbody></table>
    <h4 class="plan-sub">${L(`Vermögensverlauf bei Ausstieg mit ${fa}`, `Wealth path when stopping at ${fa}`)}</h4>
    <div class="plan-path">${ages.map((a) => {
      const v = sim.marks[a];
      return `<div><span>${L("Alter", "Age")} ${a}</span><b>${v == null ? "–" : chf(v)}</b></div>`;
    }).join("")}</div>
    <p class="hint">${L("Reale Werte in heutiger Kaufkraft. Vereinfachungen: 3a-Bezug mit 60, Steuern auf Erträge und Bezüge sowie AHV-Kürzungen durch Beitragslücken nicht berücksichtigt.",
      "Real values in today's purchasing power. Simplifications: 3a withdrawn at 60; taxes on returns and withdrawals and AHV reductions from contribution gaps not included.")}</p>`;
}

function renderTips(c = planCtx()) {
  const P = plan.P, cats = $("#plCats");
  if (!cats) return;
  const list = TIPS.filter((t) => !t.canton || t.canton === P.canton);
  const counts = {}; for (const t of list) counts[t.cat] = (counts[t.cat] || 0) + 1;
  cats.innerHTML = TIP_CATS.map(([k, de, en]) => `<button role="tab" data-cat="${k}" class="${P.cat === k ? "on" : ""}" aria-selected="${P.cat === k}">${L(de, en)}<small>${k === "all" ? list.length : counts[k] || 0}</small></button>`).join("");
  cats.querySelectorAll("button").forEach((b) => (b.onclick = () => { P.cat = b.dataset.cat; renderTips(); savePlan(); }));
  const shown = list.filter((t) => (P.cat === "all" || t.cat === P.cat) && !(P.hideDone && P.done[t.id]))
    .sort((a, b) => (!!P.done[a.id] - !!P.done[b.id]) || b.impact - a.impact);
  const catName = (k) => { const x = TIP_CATS.find((y) => y[0] === k); return L(x[1], x[2]); };
  const imp = (n) => [L("klein", "small"), L("mittel", "medium"), L("gross", "high")][n - 1];
  $("#plTips").innerHTML = shown.length ? shown.map((t) => {
    const [title, text] = LANG === "en" ? t.en : t.de, extra = t.calc ? t.calc(c) : "";
    return `<article class="plan-tip ${P.done[t.id] ? "done" : ""}">
      <label class="plan-tip-check" title="${L("Erledigt", "Done")}"><input type="checkbox" data-done="${t.id}" ${P.done[t.id] ? "checked" : ""} aria-label="${L("Erledigt", "Done")}: ${esc(title)}"></label>
      <div><div class="plan-tip-head"><b>${esc(title)}</b>
        <span class="plan-tag imp${t.impact}">${L("Wirkung", "Impact")}: ${imp(t.impact)}</span>
        <span class="plan-tag">${catName(t.cat)}</span>${t.canton ? `<span class="plan-tag">${t.canton}</span>` : ""}</div>
      <p>${esc(text)}</p>${extra ? `<p class="plan-win">${extra}</p>` : ""}</div></article>`;
  }).join("") : `<p class="hint">${L("Alles erledigt in dieser Kategorie 🎉", "All done in this category 🎉")}</p>`;
  $("#plTips").querySelectorAll("[data-done]").forEach((el) => (el.onchange = () => {
    if (el.checked) P.done[el.dataset.done] = true; else delete P.done[el.dataset.done];
    renderTips(); savePlan();
  }));
}
