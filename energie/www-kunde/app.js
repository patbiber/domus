/* homi – Kunden-Oberfläche: Live-Daten, Diagramme (eigenes SVG, keine Fremdbibliothek), App und Push. */
"use strict";
const $ = id => document.getElementById(id);
const WT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const WT_LANG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
const MON = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
const D = { status: null, hist: [], boerse: null, ein: null, prog: null, heute: null, info: null, speicher: null };
const zahl = (x, n = 1) => x == null || isNaN(x) ? "–" : Number(x).toLocaleString("de-CH", { minimumFractionDigits: n, maximumFractionDigits: n });
const kw = w => w == null ? "–" : (Math.abs(w) < 1000 ? `${Math.round(w)} W` : `${zahl(w / 1000, 2)} kW`);
const chf = x => x == null ? "–" : `${x < 0 ? "−" : ""}CHF ${zahl(Math.abs(x), 2)}`;
const rp = x => x == null ? "–" : `${zahl(x, 1)} Rp.`;
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const datum = s => { const d = new Date(s); return `${WT[d.getDay()]} ${d.getDate()}.${d.getMonth() + 1}.`; };
async function holen(pfad) {
  const r = await fetch("/api/" + pfad, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

/* ======================= Tabs ======================= */
function tabZeigen(name, merken = true) {
  document.querySelectorAll(".tabs button").forEach(b => {
    const an = b.dataset.tab === name;
    b.classList.toggle("aktiv", an);
    an ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("aktiv", t.id === "tab-" + name));
  if (merken) history.replaceState(null, "", name === "uebersicht" ? location.pathname : "#" + name);
  window.scrollTo({ top: 0 });
  if (name === "verlauf") verlaufLaden();
  zeichnen();
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => tabZeigen(b.dataset.tab));

/* ======================= Diagramme (SVG) ======================= */
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, eltern) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (eltern) eltern.appendChild(e);
  return e;
}
function farbe(name) { return getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim(); }
function schoeneSkala(max) {
  if (max <= 0) return { max: 1, schritt: 0.25 };
  const roh = max / 4, p = Math.pow(10, Math.floor(Math.log10(roh)));
  const schritt = [1, 2, 2.5, 5, 10].map(f => f * p).find(s => s >= roh);
  return { max: Math.ceil(max / schritt) * schritt, schritt };
}
function tipZeigen(x, y, html) { const t = $("tip"); t.innerHTML = html; t.style.left = x + "px"; t.style.top = y + "px"; t.hidden = false; }
function tipWeg() { $("tip").hidden = true; }

/* Gemeinsamer Rahmen: Achsen, Gitter, Interaktion. n Punkte, xLabel(i) -> Text oder null */
function rahmen(box, { n, min, max, einheit, xLabel, tip, fmt = v => v === 0 ? "0" : zahl(v, Math.abs(v) < 10 ? 1 : 0) }) {
  box.innerHTML = "";
  const B = Math.max(260, box.clientWidth), H = +box.dataset.hoehe || 200;
  const L = 40, R = 8, O = 10, U = 24;
  const svg = el("svg", { viewBox: `0 0 ${B} ${H}`, height: H }, box);
  const lo = Math.min(0, min), sk = schoeneSkala(Math.max(max, -lo, 0.001));
  const yMin = lo < 0 ? -Math.ceil(-lo / sk.schritt) * sk.schritt : 0;
  const yMax = sk.max;
  const y = v => O + (yMax - v) / (yMax - yMin) * (H - O - U);
  const x = i => L + (n <= 1 ? 0 : i * (B - L - R) / (n - 1));
  for (let v = yMin; v <= yMax + 1e-9; v += sk.schritt) {
    el("line", { x1: L, x2: B - R, y1: y(v), y2: y(v), class: v === 0 ? "achse" : "gitter" }, svg);
    el("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v);
  }
  for (let i = 0; i < n; i++) {
    const t = xLabel(i);
    if (t) el("text", { x: x(i), y: H - 6, "text-anchor": "middle" }, svg).textContent = t;
  }
  const zeiger = el("line", { y1: O, y2: H - U, class: "zeiger", visibility: "hidden" }, svg);
  const bewegen = ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) * B / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / ((B - L - R) / Math.max(1, n - 1)))));
    zeiger.setAttribute("x1", x(i)); zeiger.setAttribute("x2", x(i)); zeiger.setAttribute("visibility", "visible");
    const html = tip(i);
    html ? tipZeigen(r.left + x(i) * r.width / B, r.top + 8, html) : tipWeg();
  };
  svg.addEventListener("pointermove", bewegen);
  svg.addEventListener("pointerdown", bewegen);
  svg.addEventListener("pointerleave", () => { zeiger.setAttribute("visibility", "hidden"); tipWeg(); });
  return { svg, x, y, B, H, L, R, O, U, einheit };
}

function linienChart(box, { punkte, reihen, xLabel, tip }) {
  const n = punkte.length;
  if (!n) { box.innerHTML = '<p class="leise">Noch keine Daten.</p>'; return; }
  const alle = reihen.flatMap(r => r.werte).filter(v => v != null);
  const f = rahmen(box, { n, min: Math.min(...alle), max: Math.max(...alle), xLabel, tip });
  for (const r of reihen) {
    const pfad = r.werte.map((v, i) => `${i ? "L" : "M"}${f.x(i).toFixed(1)} ${f.y(v ?? 0).toFixed(1)}`).join(" ");
    if (r.flaeche) {
      const id = "g" + Math.random().toString(36).slice(2);
      const g = el("linearGradient", { id, x1: 0, x2: 0, y1: 0, y2: 1 }, el("defs", {}, f.svg));
      el("stop", { offset: "0", "stop-color": r.farbe, "stop-opacity": ".45" }, g);
      el("stop", { offset: "1", "stop-color": r.farbe, "stop-opacity": ".03" }, g);
      el("path", { d: `${pfad} L${f.x(n - 1)} ${f.y(0)} L${f.x(0)} ${f.y(0)} Z`, fill: `url(#${id})` }, f.svg);
    }
    el("path", { d: pfad, fill: "none", stroke: r.farbe, "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
  }
}

function balkenChart(box, { labels, gruppen, xLabel, tip, farbeFn, hervor = -1, fmt }) {
  const n = labels.length;
  if (!n) { box.innerHTML = '<p class="leise">Noch keine Daten.</p>'; return; }
  const alle = gruppen.flatMap(g => g.werte);
  const f = rahmen(box, { n, min: Math.min(...alle), max: Math.max(...alle), xLabel, tip, fmt });
  const slot = (f.B - f.L - f.R) / n;
  // Balken in Slots statt auf Punkten: x(i) des Rahmens verschieben
  const mitte = i => f.L + slot * (i + .5);
  f.svg.querySelectorAll("text[text-anchor=middle]").forEach(t => t.remove());
  for (let i = 0; i < n; i++) {
    const t = xLabel(i);
    if (t) el("text", { x: mitte(i), y: f.H - 6, "text-anchor": "middle" }, f.svg).textContent = t;
  }
  const breite = Math.max(2, Math.min(26, slot * .78 / gruppen.length));
  gruppen.forEach((g, gi) => g.werte.forEach((v, i) => {
    const x0 = mitte(i) - breite * gruppen.length / 2 + gi * breite;
    const y0 = f.y(Math.max(0, v)), y1 = f.y(Math.min(0, v));
    el("rect", { x: x0 + .5, y: y0, width: Math.max(1, breite - 1), height: Math.max(v ? 1 : 0, y1 - y0), rx: Math.min(4, breite / 3),
      fill: farbeFn ? farbeFn(v, i, gi) : g.farbe, opacity: hervor >= 0 && hervor !== i ? .55 : 1 }, f.svg);
  }));
  // Zeiger auf Slot-Mitte korrigieren
  const zeiger = f.svg.querySelector(".zeiger");
  const r0 = () => f.svg.getBoundingClientRect();
  const bewegen = ev => {
    const r = r0(), px = (ev.clientX - r.left) * f.B / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.floor((px - f.L) / slot)));
    zeiger.setAttribute("x1", mitte(i)); zeiger.setAttribute("x2", mitte(i)); zeiger.setAttribute("visibility", "visible");
    tipZeigen(r.left + mitte(i) * r.width / f.B, r.top + 8, tip(i));
    ev.stopImmediatePropagation();
  };
  f.svg.addEventListener("pointermove", bewegen, true);
  f.svg.addEventListener("pointerdown", bewegen, true);
}

/* ======================= Übersicht ======================= */
function flussZeichnen() {
  const s = D.status;
  const live = $("live");
  if (!s) return;
  const alter = (Date.now() - new Date(s.zeit).getTime()) / 1000;
  if (!s.fronius_ok) { live.className = "live schlaf"; live.lastElementChild.textContent = "Wechselrichter schläft"; }
  else if (alter > 60) { live.className = "live fehler"; live.lastElementChild.textContent = "keine neuen Daten"; }
  else { live.className = "live ok"; live.lastElementChild.textContent = "Live"; }
  $("fluss-zeit").textContent = `Stand ${new Date(s.zeit).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;

  const pv = Math.max(0, s.pv_w || 0), haus = Math.max(0, s.verbrauch_w || 0);
  const bezug = Math.max(0, s.bezug_w || 0), ein = Math.max(0, s.einspeisung_w || 0);
  const direkt = Math.max(0, pv - ein);
  const setze = (id, an, klasse, w) => {
    const p = $(id);
    p.setAttribute("class", "leitung" + (an ? " an " + klasse : ""));
    // schneller fliessen bei mehr Leistung
    p.style.animationDuration = an ? `${Math.max(.35, 1.6 - Math.min(1.2, w / 4000))}s` : "";
  };
  setze("l-pv-haus", direkt > 15, "sonne", direkt);
  setze("l-pv-netz", ein > 15, "gruen", ein);
  setze("l-netz-haus", bezug > 15, "blau", bezug);
  $("l-pv-netz").setAttribute("d", "M60 92 L60 140");
  document.querySelector(".knoten.sonne").classList.toggle("an", pv > 15);
  $("t-pv").textContent = kw(pv);
  $("t-haus").textContent = kw(haus);
  $("t-netz").textContent = ein > 15 ? kw(ein) : kw(bezug);
  $("t-netz-l").textContent = ein > 15 ? "ins Netz" : bezug > 15 ? "aus dem Netz" : "Netz";

  let satz;
  if (!s.fronius_ok) satz = "Der Wechselrichter ruht – sobald die Sonne aufgeht, ist er wieder da.";
  else if (pv < 15) satz = `Die Sonne ist weg. Dein Haus braucht <b>${kw(haus)}</b> aus dem Netz, das kostet gerade <b>${zahl((s.kosten_chf_h || 0) * 100, 1)} Rp. pro Stunde</b>.`;
  else if (ein > 15) satz = `Deine Anlage deckt den ganzen Verbrauch und liefert <b>${kw(ein)}</b> ins Netz – das bringt <b>${zahl((s.erloes_chf_h || 0) * 100, 1)} Rp. pro Stunde</b>.`;
  else satz = `Die Sonne deckt <b>${Math.round(100 * direkt / Math.max(1, haus))} %</b> deines Verbrauchs, <b>${kw(bezug)}</b> kommen aus dem Netz.`;
  $("fluss-satz").innerHTML = satz;
}

function kpisZeichnen() {
  const s = D.status, h = D.heute;
  if (s) { $("k-pv").textContent = `${zahl(s.pv_heute_kwh, 1)} kWh`; }
  const p = s && s.prognose && s.prognose.heute;
  $("k-pv2").textContent = p ? `erwartet ${zahl(p.kwh, 0)} kWh` : " ";
  if (h) {
    $("k-verbrauch").textContent = `${zahl(h.verbrauch_kwh, 1)} kWh`;
    $("k-verbrauch2").textContent = `${zahl(h.bezug_kwh, 1)} kWh vom Netz`;
    $("k-autarkie").textContent = `${Math.round(h.autarkie_pct ?? 0)} %`;
    const b = $("k-bilanz");
    b.textContent = `${h.saldo_chf >= 0 ? "+" : "−"}${zahl(Math.abs(h.saldo_chf), 2)}`;
    b.className = h.saldo_chf >= 0 ? "plus" : "minus";
    $("k-bilanz2").textContent = `CHF, Erlös minus Kosten`;
  }
}

function prognoseZeichnen() {
  const p = D.prog, s = D.status;
  if (!p || !p.morgen) return;
  const m = p.morgen;
  $("p-tag").textContent = WT_LANG[new Date(m.datum).getDay()];
  $("p-symbol").textContent = m.kwh >= 20 ? "☀️" : m.kwh >= 8 ? "⛅" : "☁️";
  $("p-kwh").textContent = `${zahl(m.kwh, 1)} kWh`;
  $("p-fenster").innerHTML = m.bestes_fenster
    ? `Beste Zeit für Waschmaschine &amp; Co.: <b>${m.bestes_fenster.von}–${m.bestes_fenster.bis} Uhr</b>`
    : "Kaum Sonne erwartet.";
  const ist = s ? s.pv_heute_kwh : null;
  $("p-heute").textContent = `${zahl(ist, 1)} / ${zahl(p.heute && p.heute.kwh, 1)} kWh`;
  if (p.uebermorgen) {
    $("p-ueber-l").textContent = WT_LANG[new Date(p.uebermorgen.datum).getDay()];
    $("p-ueber").textContent = `${zahl(p.uebermorgen.kwh, 1)} kWh`;
  }
  const t = (p.treffer || []).filter(x => x.ist_kwh);
  if (t.length >= 3) {
    const f = t.reduce((a, x) => a + Math.abs(x.prognose_kwh - x.ist_kwh) / x.ist_kwh, 0) / t.length;
    $("a-treffer").textContent = `± ${Math.round(100 * f)} % (${t.length} Tage)`;
  }
}

function preisZeichnen() {
  const s = D.status;
  if (!s || !s.tarif) return;
  const t = s.tarif, b = s.boerse;
  $("pr-tarif").textContent = t.produkt || "";
  $("pr-bezug").textContent = `${zahl(t.bezug_chf_kwh * 100, 2)} Rp./kWh`;
  $("pr-rueck").textContent = t.rueckliefer_chf_kwh != null ? `${zahl(t.rueckliefer_chf_kwh * 100, 2)} Rp./kWh` : "–";
  if (b) {
    const el_ = $("pr-boerse");
    el_.textContent = `${zahl(b.rp_kwh, 1)} Rp./kWh`;
    el_.className = b.negativ ? "minus" : "";
    $("pr-hinweis").textContent = b.naechster_negativer
      ? `Nächster negativer Börsenpreis: ${new Date(b.naechster_negativer.zeit || b.naechster_negativer).toLocaleString("de-CH", { weekday: "short", hour: "2-digit", minute: "2-digit" })}`
      : `Heute zwischen ${zahl(b.min_rp_kwh, 1)} und ${zahl(b.max_rp_kwh, 1)} Rp./kWh`;
  }
  $("t-produkt").textContent = t.produkt || "–";
  $("t-stand").textContent = t.stand ? `Stand ${new Date(t.stand).toLocaleDateString("de-CH")}` : "";
  $("t-bezug").textContent = `${zahl(t.bezug_chf_kwh * 100, 2)} Rp./kWh`;
  $("t-rueck").textContent = t.rueckliefer_chf_kwh != null ? `${zahl(t.rueckliefer_chf_kwh * 100, 2)} Rp./kWh` : "–";
  $("t-vorteil").textContent = t.rueckliefer_chf_kwh != null ? `${zahl((t.bezug_chf_kwh - t.rueckliefer_chf_kwh) * 100, 2)} Rp./kWh` : "–";
}

function warnungZeichnen() {
  const e = D.ein, w = $("warnung");
  if (!e) return;
  const teile = [];
  if (e.heute && e.heute.minuspreis_bloecke && e.heute.minuspreis_bloecke.length) teile.push(`heute ${e.heute.minuspreis_bloecke.join(" und ")}`);
  if (e.morgen && e.morgen.minuspreis_bloecke && e.morgen.minuspreis_bloecke.length) teile.push(`morgen ${e.morgen.minuspreis_bloecke.join(" und ")}`);
  if (!teile.length) { w.hidden = true; return; }
  w.innerHTML = `<span class="ico" aria-hidden="true">⚡</span><div><b>Negative Strompreise ${teile.join(", ")}</b>
    <span>Dann lohnt es sich, Strom selbst zu verbrauchen: Waschmaschine, Boiler oder E-Auto in diese Stunden legen. Details unter «Strompreis».</span></div>`;
  w.hidden = false;
}

function verlauf24Zeichnen() {
  const box = $("chart-24h");
  if (!box.offsetParent || !D.hist.length) return;
  // auf 10-Minuten-Raster mitteln
  const ende = Date.now(), start = ende - 24 * 3600e3, schritt = 600e3;
  const n = Math.ceil((ende - start) / schritt);
  const sum = Array.from({ length: n }, () => ({ pv: 0, v: 0, k: 0 }));
  for (const p of D.hist) {
    const t = new Date(p.zeit).getTime();
    const i = Math.floor((t - start) / schritt);
    if (i >= 0 && i < n) { sum[i].pv += p.pv_w || 0; sum[i].v += p.verbrauch_w || 0; sum[i].k++; }
  }
  const pv = [], vb = [], zeiten = [];
  let lpv = 0, lv = 0;
  sum.forEach((s, i) => {
    if (s.k) { lpv = s.pv / s.k; lv = s.v / s.k; }
    pv.push(lpv / 1000); vb.push(lv / 1000); zeiten.push(new Date(start + (i + .5) * schritt));
  });
  linienChart(box, {
    punkte: zeiten,
    reihen: [{ werte: pv, farbe: farbe("sonne"), flaeche: true }, { werte: vb, farbe: farbe("blau") }],
    xLabel: i => { const d = zeiten[i]; return d.getMinutes() < 10 && d.getHours() % 6 === 0 ? `${String(d.getHours()).padStart(2, "0")}:00` : null; },
    tip: i => `<b>${zeiten[i].toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" })}</b><br>Solar ${zahl(pv[i], 2)} kW<br>Verbrauch ${zahl(vb[i], 2)} kW`,
  });
}

/* ======================= Verlauf ======================= */
let verlaufZeitraum = "woche", verlaufDaten = null;
async function verlaufLaden() {
  const heute = new Date();
  let von, aufl, titel;
  if (verlaufZeitraum === "woche") { von = new Date(heute - 6 * 864e5); aufl = "tag"; titel = "Letzte 7 Tage"; }
  else if (verlaufZeitraum === "monat") { von = new Date(heute - 29 * 864e5); aufl = "tag"; titel = "Letzte 30 Tage"; }
  else { von = new Date(heute.getFullYear(), heute.getMonth() - 11, 1); aufl = "monat"; titel = "Letzte 12 Monate"; }
  $("v-titel").textContent = titel;
  try { verlaufDaten = await holen(`archiv?aufloesung=${aufl}&von=${iso(von)}&bis=${iso(heute)}`); } catch { return; }
  verlaufZeichnen();
}
function verlaufZeichnen() {
  const d = verlaufDaten, box = $("chart-verlauf");
  if (!d || !box.offsetParent) return;
  const z = d.zeilen, monat = d.aufloesung === "monat";
  const lab = r => monat ? MON[+r.zeit.slice(5, 7) - 1] : datum(r.zeit);
  balkenChart(box, {
    labels: z.map(lab),
    gruppen: [{ werte: z.map(r => r.pv_kwh), farbe: farbe("sonne") }, { werte: z.map(r => r.verbrauch_kwh), farbe: farbe("blau") }],
    xLabel: i => z.length <= 12 ? (monat ? MON[+z[i].zeit.slice(5, 7) - 1] : WT[new Date(z[i].zeit).getDay()])
      : (i % 5 === 0 ? `${+z[i].zeit.slice(8, 10)}.${+z[i].zeit.slice(5, 7)}.` : null),
    tip: i => `<b>${lab(z[i])}</b><br>Erzeugt ${zahl(z[i].pv_kwh, 1)} kWh<br>Verbraucht ${zahl(z[i].verbrauch_kwh, 1)} kWh<br>Bilanz ${chf(z[i].saldo_chf)}`,
  });
  const s = d.summe || {};
  const k = (titel, wert, klein, art = "") => `<div class="kpi ${art}"><span>${titel}</span><b>${wert}</b><small>${klein}</small></div>`;
  $("v-summen").innerHTML =
    k("Erzeugt", `${zahl(s.pv_kwh, 0)} kWh`, "Solaranlage", "sonne") +
    k("Verbraucht", `${zahl(s.verbrauch_kwh, 0)} kWh`, "im ganzen Haus", "blau") +
    k("Autarkie", `${Math.round(s.autarkie_pct || 0)} %`, "aus eigenem Strom", "gruen") +
    k("Eingespeist", `${zahl(s.einspeisung_kwh, 0)} kWh`, `Erlös ${chf(s.erloes_chf)}`) +
    k("Bezogen", `${zahl(s.bezug_kwh, 0)} kWh`, `Kosten ${chf(s.kosten_chf)}`) +
    k("Bilanz", `<span class="${(s.saldo_chf || 0) >= 0 ? "plus" : "minus"}">${chf(s.saldo_chf)}</span>`, "Erlös minus Kosten");
  $("v-hinweis").textContent = d.erste_daten && new Date(d.erste_daten) > new Date(d.von)
    ? `homi misst seit ${new Date(d.erste_daten).toLocaleDateString("de-CH")} – frühere Tage sind leer.` : " ";
}
document.querySelectorAll("#seg-verlauf button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#seg-verlauf button").forEach(x => x.classList.toggle("an", x === b));
  verlaufZeitraum = b.dataset.z; verlaufLaden();
});

/* ======================= Strompreis ======================= */
let planTag = "heute";
function boerseZeichnen() {
  const box = $("chart-boerse"), b = D.boerse;
  if (!b || !box.offsetParent) return;
  const ab = new Date(); ab.setHours(0, 0, 0, 0);
  const p = (b.preise || []).filter(x => new Date(x.zeit) >= ab);
  const jetzt = p.findIndex(x => { const t = new Date(x.zeit).getTime(); return Date.now() >= t && Date.now() < t + 3600e3; });
  balkenChart(box, {
    labels: p.map(x => x.zeit),
    gruppen: [{ werte: p.map(x => x.rp_kwh), farbe: farbe("blau") }],
    farbeFn: (v, i) => i === jetzt ? farbe("sonne") : v < 0 ? farbe("rot") : farbe("blau"),
    xLabel: i => {                     // schmal: nur Datum und Mittag, breit: alle 6 Stunden
      const h = new Date(p[i].zeit).getHours();
      if (h === 0) return datum(p[i].zeit);
      return (box.clientWidth < 560 ? h === 12 : h % 6 === 0) ? `${h}h` : null;
    },
    tip: i => `<b>${datum(p[i].zeit)} ${new Date(p[i].zeit).getHours()}–${new Date(p[i].zeit).getHours() + 1} Uhr</b><br>${zahl(p[i].rp_kwh, 2)} Rp./kWh`,
  });
}
function planZeichnen() {
  const e = D.ein;
  if (!e) return;
  const h = e.heute;
  if (h) {
    $("e-dyn").textContent = chf(h.einspeisung_dynamisch_chf);
    $("e-fix").textContent = chf(h.einspeisung_fix_chf);
    $("e-batt").textContent = h.batterie ? `+ ${chf(h.batterie.mehrwert_chf)}` : "–";
  }
  const t = e[planTag], ol = $("plan");
  if (!t) { ol.innerHTML = '<li class="leer">Die Börsenpreise für morgen kommen am frühen Nachmittag.</li>'; return; }
  const jetzt = new Date().getHours();
  // Nur Stunden mit Solarüberschuss: einspeisen – oder bei negativem Preis selbst verbrauchen
  const zeilen = t.stunden.filter(x => (x.ueberschuss_kwh || 0) >= 0.05 && (planTag !== "heute" || x.stunde >= jetzt));
  const text = x => x.preis_rp < 0 ? `⚡ ${zahl(x.ueberschuss_kwh, 1)} kWh selbst verbrauchen`
                                    : `☀️ ${zahl(x.ueberschuss_kwh, 1)} kWh Überschuss`;
  ol.innerHTML = zeilen.length ? zeilen.map(x => `<li class="${x.preis_rp < 0 ? "negativ" : ""} ${planTag === "heute" && x.stunde === jetzt ? "jetzt" : ""}">
      <span class="h">${String(x.stunde).padStart(2, "0")}:00</span><span>${text(x)}</span><span class="p">${zahl(x.preis_rp, 1)} Rp.</span></li>`).join("")
    : `<li class="leer">${planTag === "heute" ? "Heute gibt es keinen Solarüberschuss mehr." : "Morgen wird kaum Solarüberschuss erwartet."}</li>`;
}
document.querySelectorAll("#seg-plan button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#seg-plan button").forEach(x => x.classList.toggle("an", x === b));
  planTag = b.dataset.t; planZeichnen();
});

/* ======================= Mehr: Speicher, Anlage ======================= */
function speicherZeichnen() {
  const s = D.speicher;
  if (!s || !s.speicher) return;
  const max = Math.max(...s.speicher.map(x => x.ersparnis_pro_jahr_chf || 0), 1);
  $("s-tage").textContent = `${zahl(s.speicher[0].tage_gemessen, 0)} Tage gemessen`;
  $("speicher").innerHTML = s.speicher.map(x => `<div class="speicher"><b>${x.name}</b><b>${chf(x.ersparnis_pro_jahr_chf)} / Jahr</b>
    <div class="balken"><i style="width:${Math.round(100 * (x.ersparnis_pro_jahr_chf || 0) / max)}%"></i></div>
    <small>${Math.round(x.autarkie_plus_pct || 0)} % weniger Netzbezug · Ladestand jetzt ${Math.round(x.soc_pct || 0)} %</small></div>`).join("")
    + '<p class="leise">Hochrechnung aus den bisher gemessenen Tagen, Winter und Sommer unterscheiden sich stark.</p>';
}

/* ======================= Laden ======================= */
function zeichnen() {
  flussZeichnen(); kpisZeichnen(); prognoseZeichnen(); preisZeichnen(); warnungZeichnen();
  verlauf24Zeichnen(); verlaufZeichnen(); boerseZeichnen(); planZeichnen(); speicherZeichnen();
}
async function status() {
  try { D.status = await holen("status"); flussZeichnen(); kpisZeichnen(); preisZeichnen(); }
  catch { const l = $("live"); l.className = "live fehler"; l.lastElementChild.textContent = "offline"; }
}
async function minute() {
  try { D.hist = await holen("history"); } catch {}
  try { const r = await holen(`archiv?aufloesung=tag&von=${iso(new Date())}&bis=${iso(new Date())}`); D.heute = r.zeilen[0] || null; } catch {}
  kpisZeichnen(); verlauf24Zeichnen();
}
async function selten() {
  for (const [k, p] of [["boerse", "boerse"], ["ein", "einspeisung"], ["prog", "prognose"], ["speicher", "speicher"]]) {
    try { D[k] = await holen(p); } catch {}
  }
  zeichnen();
}
async function start() {
  try {
    D.info = await holen("info");
    $("titel").textContent = D.info.titel; $("a-titel").textContent = D.info.titel;
    $("a-kwp").textContent = D.info.kwp ? `${zahl(D.info.kwp, 1)} kW` : "–";
    document.title = `homi · ${D.info.titel}`;
  } catch {}
  await status(); await minute(); await selten();
  try { const a = await holen(`archiv?aufloesung=monat&von=${iso(new Date(Date.now() - 5 * 365 * 864e5))}&bis=${iso(new Date())}`); if (a.erste_daten) $("a-seit").textContent = new Date(a.erste_daten).toLocaleDateString("de-CH"); } catch {}
  setInterval(status, 5000);
  setInterval(minute, 60000);
  setInterval(selten, 10 * 60000);
}
let groesse = 0;
addEventListener("resize", () => { clearTimeout(groesse); groesse = setTimeout(zeichnen, 150); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { status(); minute(); } });
const anfang = (location.hash || "").slice(1);
if (["verlauf", "preise", "mehr"].includes(anfang)) tabZeigen(anfang, false);
start();

/* ======================= App & Push ======================= */
const app = {
  reg: null, installEvent: null,
  istIos: /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
  installiert: matchMedia("(display-mode: standalone)").matches || navigator.standalone === true,
};
const meldung = t => { $("app-meldung").textContent = t; };
const themen = () => [...document.querySelectorAll("#app .schalter input")];
function u8(b64) {
  const s = atob((b64 + "=".repeat((4 - b64.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, c => c.charCodeAt(0));
}
async function pushApi(aktion, abo) {
  const r = await fetch("/api/push", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ aktion, abo, themen: themen().filter(x => x.checked).map(x => x.value) }) });
  if (!r.ok) throw new Error("Server antwortet " + r.status);
  return r.json();
}
function pushAnzeigen(an) {
  $("push-an").hidden = an; $("push-aus").hidden = !an; $("push-test").hidden = !an;
  const st = $("push-status");
  st.className = "status-zeile" + (an ? " an" : "");
  st.textContent = an ? "✓ Benachrichtigungen sind auf diesem Gerät aktiv" : "Benachrichtigungen sind auf diesem Gerät aus";
}
async function pushZustand() {
  const abo = app.reg && await app.reg.pushManager.getSubscription();
  const imBrowser = !!abo && Notification.permission === "granted";
  pushAnzeigen(imBrowser);
  if (!imBrowser) return abo;
  for (let v = 0; v < 3; v++) {
    try {
      const z = await pushApi("lesen", abo.toJSON());
      if (z.angemeldet) themen().forEach(x => { x.checked = z.themen.includes(x.value); });
      pushAnzeigen(z.angemeldet);
      break;
    } catch { await new Promise(r => setTimeout(r, 1500 * (v + 1))); }
  }
  return abo;
}
$("push-an").onclick = async () => {
  try {
    if (!app.reg || !("PushManager" in window)) {
      return meldung(app.istIos && !app.installiert
        ? "Auf dem iPhone gehen Benachrichtigungen erst, wenn homi auf dem Home-Bildschirm ist (ab iOS 16.4). Danach homi dort öffnen."
        : "Dieser Browser kann leider keine Benachrichtigungen.");
    }
    if (await Notification.requestPermission() !== "granted") return meldung("Benachrichtigungen sind blockiert – bitte in den Einstellungen des Browsers erlauben.");
    const { schluessel } = await holen("push");
    if (!schluessel) return meldung("Benachrichtigungen sind auf dem Server noch nicht eingerichtet.");
    meldung("Einen Moment, ich melde dein Gerät an …");
    const abo = await app.reg.pushManager.getSubscription() || await Promise.race([
      app.reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: u8(schluessel) }),
      new Promise((_, nein) => setTimeout(() => nein(new Error("der Push-Dienst des Browsers antwortet nicht")), 20000)),
    ]);
    await pushApi("anmelden", abo.toJSON());
    await pushZustand();
    meldung("Fertig! Tippe auf «Testnachricht senden», um es auszuprobieren.");
  } catch (e) { meldung("Das hat nicht geklappt: " + e.message); }
};
$("push-aus").onclick = async () => {
  try {
    const abo = await app.reg.pushManager.getSubscription();
    if (abo) { await pushApi("abmelden", abo.toJSON()).catch(() => {}); await abo.unsubscribe(); }
    await pushZustand(); meldung("Benachrichtigungen ausgeschaltet.");
  } catch (e) { meldung("Fehler: " + e.message); }
};
$("push-test").onclick = async () => {
  try { await pushApi("test", (await app.reg.pushManager.getSubscription()).toJSON()); meldung("Unterwegs – die Nachricht kommt in ein paar Sekunden."); }
  catch (e) { meldung("Test hat nicht geklappt: " + e.message); }
};
themen().forEach(x => x.onchange = async () => {
  const abo = app.reg && await app.reg.pushManager.getSubscription();
  if (abo && !$("push-aus").hidden) {
    try { await pushApi("anmelden", abo.toJSON()); meldung("Gespeichert."); } catch (e) { meldung("Fehler: " + e.message); }
  }
});
addEventListener("beforeinstallprompt", e => { e.preventDefault(); app.installEvent = e; $("app-install").hidden = false; });
$("app-install").onclick = async () => {
  if (!app.installEvent) return;
  app.installEvent.prompt(); await app.installEvent.userChoice;
  app.installEvent = null; $("app-install").hidden = true;
};
addEventListener("appinstalled", () => meldung("homi ist installiert – du findest es bei deinen Apps."));
if (app.installiert) $("app-text").textContent = "homi läuft als App. Wähle, worüber ich dich benachrichtigen soll:";
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").then(() => navigator.serviceWorker.ready).then(r => { app.reg = r; pushZustand(); }).catch(() => {});
}
