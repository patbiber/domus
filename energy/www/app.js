/* homi – Demo mit simulierten Daten (homi.solar)
   Tageszeit und Sonnenstand sind echt (Stäfa, Zürcher Zeit), Wetter und Verbrauch werden pro Tag
   reproduzierbar simuliert (gleicher Tag = gleiche Kurve). Keine externen Dienste. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const N = 288;                                   // 5-Minuten-Schritte pro Tag
  const PROFILE = {
    heim: {
      kwp: 9.8, batt: 10, battKw: 5, autoMax: 7.4, autoBedarf: 16, tarif: 26.68,
      haus: "Verbrauch", auto: "E-Auto",
      note: "Simulierte Daten eines typischen Einfamilienhauses am Zürichsee: 9.8 kWp Solaranlage, 10 kWh Batterie, Wärmepumpe und E-Auto. Tageszeit und Sonnenstand sind echt, Wetter und Verbrauch sind simuliert.",
      kat: { grund: ["Grundlast", "#98a2b3"], wp: ["Wärmepumpe", "#f97066"], haushalt: ["Küche & Haushalt", "#ffb020"], auto: ["E-Auto", "#53b1fd"], boiler: ["Boiler & Wäsche", "#a48bff"] },
    },
    betrieb: {
      kwp: 60, batt: 40, battKw: 20, autoMax: 22, autoBedarf: 70, tarif: 22.4,
      haus: "Betrieb", auto: "E-Flotte",
      note: "Simulierte Daten eines Gewerbebetriebs (Schreinerei): 60 kWp Solaranlage, 40 kWh Batterie, zwei E-Lieferwagen. Tageszeit und Sonnenstand sind echt, Wetter und Verbrauch sind simuliert.",
      kat: { grund: ["Kühlung & IT", "#98a2b3"], wp: ["Heizung", "#f97066"], haushalt: ["Maschinen", "#ffb020"], auto: ["E-Flotte", "#53b1fd"], boiler: ["Druckluft & Absaugung", "#a48bff"] },
    },
  };
  let profil = "heim";

  // ---------- Hilfen ----------
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const hash = s => { let h = 1779033703 ^ s.length; for (let i = 0; i < s.length; i++) { h = Math.imul(h ^ s.charCodeAt(i), 3432918353); h = (h << 13) | (h >>> 19); } return h >>> 0; };
  const rng = seed => () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const kw = v => (Math.abs(v) < 10 ? v.toFixed(1) : v.toFixed(0)).replace(".", ".") + " kW";
  const chf = v => "CHF " + v.toFixed(2);
  const hm = min => `${String(Math.floor(min / 60) % 24).padStart(2, "0")}:${String(Math.floor(min % 60)).padStart(2, "0")}`;
  const WT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

  function zuerich(date = new Date()) {
    const p = Object.fromEntries(new Intl.DateTimeFormat("de-CH", { timeZone: "Europe/Zurich", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })
      .formatToParts(date).map(x => [x.type, x.value]));
    return { datum: `${p.year}-${p.month}-${p.day}`, min: +p.hour * 60 + +p.minute + +p.second / 60 };
  }
  const plusTage = (datum, n) => { const d = new Date(datum + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

  // ---------- Simulation eines Tages ----------
  const cache = {};
  function tag(datum, key) {
    const id = datum + key;
    if (cache[id]) return cache[id];
    const P = PROFILE[key], r = rng(hash(id)), rw = rng(hash(datum + "wetter"));
    const [y, m, d] = datum.split("-").map(Number);
    const doy = Math.round((Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 0)) / 864e5);
    const wt = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    const werktag = wt >= 1 && wt <= 5, wochenende = !werktag;
    const sommerzeit = (m > 3 && m < 10) || (m === 3 && d >= 29) || (m === 10 && d < 25);
    const decl = 23.44 * Math.sin(2 * Math.PI * (284 + doy) / 365) * Math.PI / 180, lat = 47.24 * Math.PI / 180;
    const klar = 0.3 + 0.7 * Math.pow(rw(), 0.5);
    const ph = [rw() * 6.3, rw() * 6.3, rw() * 6.3];
    const heiz = [1, 0.9, 0.7, 0.4, 0.15, 0.05, 0.05, 0.05, 0.15, 0.45, 0.75, 1][m - 1];
    const sommer = m >= 4 && m <= 9;
    const o = { datum, key, pv: [], last: [], soc: [], netz: [], bat: [], kat: { grund: [], wp: [], haushalt: [], auto: [], boiler: [] }, klar, werktag };
    let soc = P.batt * (0.2 + 0.25 * r()), autoGeladen = 0;
    const autoDa = key === "betrieb" ? true : (wochenende || r() > 0.45);
    const waschTag = r() > 0.35, wpPhase = Math.floor(r() * 18);
    for (let i = 0; i < N; i++) {
      const t = i / 12;
      const solar = t - (sommerzeit ? 2 : 1) + 8.72 / 15;
      const sinEl = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos((solar - 12) * 15 * Math.PI / 180);
      const wolke = clamp(klar + 0.18 * (1.25 - klar) * (Math.sin(t * 1.3 + ph[0]) + 0.6 * Math.sin(t * 3.1 + ph[1]) + 0.4 * Math.sin(t * 7.7 + ph[2])), 0.06, 1);
      const pv = sinEl > 0 ? Math.min(P.kwp * 0.92, P.kwp * 0.95 * Math.pow(sinEl, 1.1) * wolke) : 0;
      const k = { grund: 0, wp: 0, haushalt: 0, auto: 0, boiler: 0 };
      if (key === "heim") {
        k.grund = 0.2 + ((i % 5) < 2 ? 0.07 : 0) + 0.02 * r();
        if (t >= 6.5 && t < 8) k.haushalt = 0.35 + (r() < 0.22 ? 1.7 : 0);
        else if (t >= 11.5 && t < 13) k.haushalt = 0.25 + (r() < 0.18 ? 1.3 : 0);
        else if (t >= 17.5 && t < 19.5) k.haushalt = 0.9 * Math.sin((t - 17.5) / 2 * Math.PI) + (r() < 0.3 ? 1.6 : 0) + 0.2;
        else if (t >= 19.5 && t < 23) k.haushalt = 0.28;
        if (wochenende && t >= 9 && t < 17) k.haushalt += 0.25 + (r() < 0.08 ? 1.2 : 0);
        const lauf = Math.round(2 + 6 * heiz);
        if (((i + wpPhase) % 18) < lauf && (heiz > 0.2 || (t >= 13 && t < 14))) k.wp = 1.5 + 0.2 * r();
        if (waschTag && t >= 12.5 && t < 13.75) k.boiler = t < 12.85 ? 2.0 : 0.35;
        if (t >= 20.5 && t < 21) k.boiler += 1.7;
      } else {
        k.grund = 1.5 + ((i % 6) < 2 ? 0.4 : 0) + 0.1 * r();
        const arbeit = werktag && ((t >= 7 && t < 12) || (t >= 13 && t < 17));
        if (arbeit) { k.haushalt = 4.5 + (r() < 0.35 ? 7 + 14 * r() : 0); k.boiler = 2.5 + (k.haushalt > 6 ? 1.6 : 0); }
        else if (werktag && t >= 12 && t < 13) k.haushalt = 1.2;
        else if (wt === 6 && t >= 8 && t < 12) k.haushalt = 1.5 + (r() < 0.2 ? 6 : 0);
        if (heiz > 0.2 && ((werktag && t >= 5 && t < 17) || t < 2) && ((i + wpPhase) % 12) < Math.round(3 + 6 * heiz)) k.wp = 4 + r();
      }
      // E-Auto/E-Flotte: zuerst mit Überschuss, Betrieb abends Rest aus dem Netz
      const ohneAuto = k.grund + k.wp + k.haushalt + k.boiler;
      const ueberschuss = pv - ohneAuto;
      if (autoDa && autoGeladen < P.autoBedarf) {
        if (t >= 10 && t < 16.5 && ueberschuss > 1.4) k.auto = Math.min(ueberschuss, P.autoMax);
        else if (key === "betrieb" && t >= 17.5 && t < 23) k.auto = 11;
        k.auto = Math.min(k.auto, (P.autoBedarf - autoGeladen) * 12);
        autoGeladen += k.auto / 12;
      }
      const last = ohneAuto + k.auto;
      let bat = 0;                                  // + laden, - entladen (aus Sicht der Batterie)
      const diff = pv - last;
      if (diff > 0) bat = Math.min(diff, P.battKw, (P.batt - soc) / 0.95 * 12);
      else bat = -Math.min(-diff, P.battKw, Math.max(0, soc - 0.05 * P.batt) * 0.95 * 12);
      soc += bat > 0 ? bat / 12 * 0.95 : bat / 12 / 0.95;
      o.pv.push(pv); o.last.push(last); o.bat.push(bat); o.soc.push(100 * soc / P.batt); o.netz.push(last - pv + bat);
      for (const kk in k) o.kat[kk].push(k[kk]);
    }
    // Börsenpreise (Rp/kWh) pro Stunde
    const pr = rng(hash(datum + "boerse"));
    o.boerse = Array.from({ length: 24 }, (_, h) => {
      let p = sommer ? 8 : 13;
      if (h < 5) p -= 2; else if (h >= 6 && h < 9) p += 6; else if (h >= 10 && h < 16) p -= klar * (sommer ? 11 : 7); else if (h >= 17 && h < 21) p += 9; else if (h >= 21) p += 2;
      if (wochenende) p -= 2.5;
      if (wochenende && sommer && klar > 0.85 && h >= 11 && h < 16) p -= 6;
      return Math.round((p + (pr() - 0.5) * 3) * 10) / 10;
    });
    o.verguetung = sommer ? 7 : 15;
    o.tarif = P.tarif;
    cache[id] = o;
    return o;
  }

  function summen(o, bis) {
    let s = { pv: 0, last: 0, bezug: 0, einsp: 0, entladen: 0, kat: { grund: 0, wp: 0, haushalt: 0, auto: 0, boiler: 0 } };
    for (let i = 0; i < Math.min(bis, N); i++) {
      s.pv += o.pv[i] / 12; s.last += o.last[i] / 12;
      if (o.netz[i] > 0) s.bezug += o.netz[i] / 12; else s.einsp -= o.netz[i] / 12;
      if (o.bat[i] < 0) s.entladen -= o.bat[i] / 12;
      for (const k in s.kat) s.kat[k] += o.kat[k][i] / 12;
    }
    const ohne = s.last * o.tarif / 100, mit = s.bezug * o.tarif / 100 - s.einsp * o.verguetung / 100;
    s.gespart = ohne - mit;
    s.autarkie = s.last > 0 ? 100 * (1 - s.bezug / s.last) : 0;
    return s;
  }

  function prognose(o) {
    let best = 0, start = 0, total = 0;
    for (let i = 0; i < N; i++) total += o.pv[i] / 12;
    for (let i = 0; i <= N - 24; i++) { let s = 0; for (let j = i; j < i + 24; j++) s += o.pv[j] / 12; if (s > best) { best = s; start = i; } }
    return { total, von: start * 5, bis: start * 5 + 120, kwh: best };
  }

  // ---------- Zeit: live oder Zeitraffer ----------
  let zeitraffer = null;                            // {start, datum}
  function jetzt() {
    const z = zuerich();
    if (zeitraffer) { const min = Math.min(1439.9, (performance.now() - zeitraffer.start) / 30000 * 1440); return { datum: zeitraffer.datum, min, raffer: true }; }
    return { ...z, raffer: false };
  }

  // ---------- Darstellung ----------
  function werte(o, min, raffer) {
    const i = Math.min(N - 1, Math.floor(min / 5)), j = Math.min(N - 1, i + 1), f = (min % 5) / 5;
    const mix = a => a[i] + (a[j] - a[i]) * f;
    const zit = raffer ? 1 : 1 + (Math.random() - 0.5) * 0.04;
    const pv = mix(o.pv) * zit, last = mix(o.last) * (raffer ? 1 : 1 + (Math.random() - 0.5) * 0.03);
    const bat = mix(o.bat), soc = mix(o.soc), auto = mix(o.kat.auto);
    return { i, pv, last, bat, soc, auto, netz: last - pv + bat };
  }

  function setDash(el, leistung, rueckwaerts) {
    const a = Math.abs(leistung);
    if (a < 0.05) { el.classList.add("aus"); return; }
    el.classList.remove("aus");
    el.style.animationDuration = clamp(2.2 / Math.sqrt(a), 0.25, 3) + "s";
    el.style.animationDirection = rueckwaerts ? "reverse" : "normal";
  }

  function zeichneFluss(o, w) {
    const P = PROFILE[profil];
    $("v-pv").textContent = kw(w.pv);
    $("v-haus").textContent = kw(w.last);
    $("s-haus").textContent = P.haus;
    $("v-soc").textContent = Math.round(w.soc) + " %";
    $("s-bat").textContent = Math.abs(w.bat) < 0.05 ? "wartet" : w.bat > 0 ? "lädt " + kw(w.bat) : "liefert " + kw(-w.bat);
    $("v-netz").textContent = kw(Math.abs(w.netz));
    $("s-netz").textContent = Math.abs(w.netz) < 0.05 ? "ausgeglichen" : w.netz > 0 ? "Bezug" : "Einspeisung";
    $("v-auto").textContent = kw(w.auto);
    $("s-auto").textContent = w.auto > 0.05 ? P.auto + " lädt" : P.auto;
    $("bat-fill").setAttribute("height", (26 * w.soc / 100).toFixed(1));
    $("bat-fill").setAttribute("y", (-26 + 26 * (1 - w.soc / 100)).toFixed(1));
    $("sun-glow").setAttribute("opacity", (0.08 + 0.9 * clamp(w.pv / P.kwp, 0, 1)).toFixed(2));
    setDash($("d-pv"), w.pv, false);
    setDash($("d-bat"), w.bat, w.bat > 0);
    const dn = $("d-netz");
    dn.setAttribute("stroke", w.netz > 0 ? "#F97066" : "#32D583");
    $("netz-icon").setAttribute("stroke", w.netz > 0 ? "#F97066" : "#32D583");
    setDash(dn, w.netz, w.netz < 0);
    setDash($("d-auto"), w.auto, false);
  }

  function zeichneKacheln(o, w, s, prog, min) {
    const P = PROFILE[profil];
    $("t-pv").textContent = kw(w.pv);
    $("t-pv-s").textContent = `heute bisher ${s.pv.toFixed(1)} kWh`;
    $("t-last").textContent = kw(w.last);
    $("t-last-s").textContent = `heute bisher ${s.last.toFixed(1)} kWh`;
    $("t-soc").textContent = Math.round(w.soc) + " %";
    $("t-soc-bar").style.width = clamp(w.soc, 0, 100) + "%";
    $("t-soc-s").textContent = Math.abs(w.bat) < 0.05 ? `${P.batt} kWh Speicher` : w.bat > 0 ? `lädt mit ${kw(w.bat)}` : `liefert ${kw(-w.bat)} ins ${profil === "heim" ? "Haus" : "Gebäude"}`;
    const bez = w.netz > 0.05, eins = w.netz < -0.05;
    $("t-netz").textContent = kw(Math.abs(w.netz));
    $("t-netz-dot").style.background = bez ? "var(--red)" : "var(--green)";
    $("t-netz-s").textContent = bez ? `Bezug · kostet ${chf(w.netz * o.tarif / 100)}/h` : eins ? `Einspeisung · bringt ${chf(-w.netz * o.verguetung / 100)}/h` : "ausgeglichen";
    $("t-gespart").textContent = chf(s.gespart);
    $("t-gespart-s").textContent = `gegenüber ohne Solaranlage · ${Math.round(s.autarkie)} % selbst versorgt`;
    $("t-prog").textContent = prog.total.toFixed(0) + " kWh " + (prog.total > P.kwp * 3 ? "☀️" : prog.total > P.kwp * 1.2 ? "⛅" : "☁️");
    $("t-fenster").textContent = `morgen ${hm(prog.von)}–${hm(prog.bis)} Uhr`;
    $("m-gespart").textContent = chf(s.gespart);
    $("m-autarkie").textContent = Math.round(s.autarkie) + " %";
    const h = Math.floor(min / 60), b = o.boerse[h];
    $("t-preis-s").textContent = `Dein Tarif ${o.tarif.toFixed(2)} Rp · Börse jetzt ${b.toFixed(1)} Rp · Vergütung ${o.verguetung} Rp pro kWh`;
    // Verbraucher
    const kat = Object.entries(s.kat).filter(([, v]) => v > 0.01).sort((a, b2) => b2[1] - a[1]);
    const max = Math.max(0.1, ...kat.map(([, v]) => v));
    $("verbraucher").innerHTML = kat.map(([k, v]) => `<div class="bar-row"><span>${P.kat[k][0]}</span><span class="bar"><i style="width:${(100 * v / max).toFixed(0)}%;background:${P.kat[k][1]}"></i></span><span class="num" style="text-align:right">${v.toFixed(1)} kWh</span></div>`).join("");
  }

  function zeichneChart(o, min) {
    const W = 800, H = 280, L = 52, R = 44, T = 14, B = 30, w = W - L - R, h = H - T - B;
    const max = Math.max(1, ...o.pv, ...o.last) * 1.08;
    const X = i => L + i / (N - 1) * w, Y = v => T + h - v / max * h, Ys = s => T + h - s / 100 * h;
    const jetztI = Math.min(N - 1, Math.floor(min / 5));
    const linie = (arr, f, von, bis) => arr.slice(von, bis + 1).map((v, k) => `${k ? "L" : "M"}${X(von + k).toFixed(1)} ${f(v).toFixed(1)}`).join("");
    const flaeche = (von, bis) => `${linie(o.pv, Y, von, bis)}L${X(bis).toFixed(1)} ${Y(0)}L${X(von).toFixed(1)} ${Y(0)}Z`;
    let g = `<defs><linearGradient id="gpv" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFB020" stop-opacity=".75"/><stop offset="1" stop-color="#FFB020" stop-opacity=".08"/></linearGradient></defs>`;
    for (let v = 0; v <= 2; v++) { const y = Y(max / 2 * v); g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#eef1f7"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" font-size="11" fill="#64748b">${(max / 2 * v).toFixed(0)}${v === 2 ? " kW" : ""}</text><text x="${W - R + 6}" y="${y + 4}" font-size="11" fill="#7a5af8">${50 * v}%</text>`; }
    for (let hh = 0; hh <= 24; hh += 3) g += `<text x="${X(hh * 12 - (hh === 24 ? 1 : 0))}" y="${H - 8}" text-anchor="middle" font-size="11" fill="#64748b">${hh}h</text>`;
    g += `<path d="${flaeche(0, jetztI)}" fill="url(#gpv)"/><path d="${flaeche(jetztI, N - 1)}" fill="#FFB020" opacity=".14"/>`;
    g += `<path d="${linie(o.pv, Y, 0, jetztI)}" fill="none" stroke="#f59e0b" stroke-width="2"/>`;
    g += `<path d="${linie(o.last, Y, 0, jetztI)}" fill="none" stroke="#0f1b2d" stroke-width="2" stroke-linejoin="round"/><path d="${linie(o.last, Y, jetztI, N - 1)}" fill="none" stroke="#0f1b2d" stroke-width="1.5" opacity=".22" stroke-dasharray="4 4"/>`;
    g += `<path d="${linie(o.soc, Ys, 0, jetztI)}" fill="none" stroke="#7a5af8" stroke-width="2" stroke-dasharray="6 4"/><path d="${linie(o.soc, Ys, jetztI, N - 1)}" fill="none" stroke="#7a5af8" stroke-width="1.5" opacity=".25" stroke-dasharray="6 4"/>`;
    const xj = X(jetztI);
    g += `<line x1="${xj}" x2="${xj}" y1="${T}" y2="${T + h}" stroke="#ff7a1a" stroke-width="1.5"/><circle cx="${xj}" cy="${Y(o.pv[jetztI])}" r="5" fill="#ff7a1a" stroke="#fff" stroke-width="2"/><text x="${xj + (xj > W - 120 ? -8 : 8)}" y="${T + 12}" text-anchor="${xj > W - 120 ? "end" : "start"}" font-size="12" font-weight="700" fill="#ff7a1a">jetzt ${hm(min)}</text>`;
    $("chart").innerHTML = g;
  }

  function zeichnePreise(o, min) {
    const W = 360, H = 150, L = 6, R = 6, T = 10, B = 20, w = W - L - R, h = H - T - B;
    const lo = Math.min(0, ...o.boerse) - 1, hi = Math.max(o.tarif, ...o.boerse) + 2;
    const Y = v => T + h - (v - lo) / (hi - lo) * h, bw = w / 24, jetztH = Math.floor(min / 60);
    let g = `<line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" stroke="#d5dbe7"/>`;
    o.boerse.forEach((p, hh) => {
      const y0 = Y(0), y = Y(p), farbe = p < 0 ? "#f04438" : hh === jetztH ? "#ff7a1a" : "#2e90fa";
      g += `<rect x="${(L + hh * bw + 1.5).toFixed(1)}" y="${Math.min(y, y0).toFixed(1)}" width="${(bw - 3).toFixed(1)}" height="${Math.max(1, Math.abs(y0 - y)).toFixed(1)}" rx="2" fill="${farbe}" opacity="${hh === jetztH ? 1 : 0.75}"><title>${hh}:00 · ${p.toFixed(1)} Rp/kWh</title></rect>`;
    });
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(o.tarif)}" y2="${Y(o.tarif)}" stroke="#f04438" stroke-dasharray="5 4"/><line x1="${L}" x2="${W - R}" y1="${Y(o.verguetung)}" y2="${Y(o.verguetung)}" stroke="#12b76a" stroke-dasharray="5 4"/>`;
    [0, 6, 12, 18].forEach(hh => g += `<text x="${L + hh * bw + bw / 2}" y="${H - 5}" text-anchor="middle" font-size="10" fill="#64748b">${hh}h</text>`);
    $("boerse-chart").innerHTML = g;
  }

  function meldungen(o, w, s, prog, min) {
    const P = PROFILE[profil], m = [];
    if (w.auto > 0.3) m.push(["☀️", "#fff4df", `${P.auto} lädt mit ${kw(w.auto)} reinem Sonnenstrom`]);
    if (w.soc > 97 && w.netz < -0.3) m.push(["🔋", "#f1edff", `Batterie voll – Überschuss geht für ${o.verguetung} Rp/kWh ins Netz`]);
    if (w.netz > 1.5) m.push(["⚡", "#ffeceb", `Netzbezug ${kw(w.netz)} – kostet gerade ${chf(w.netz * o.tarif / 100)} pro Stunde`]);
    const negH = o.boerse.findIndex((p, hh) => p < 0 && hh >= Math.floor(min / 60));
    if (negH >= 0) m.push(["📉", "#eaf3ff", `Börsenpreis um ${negH}:00 Uhr negativ – Boiler & ${P.auto} werden eingeplant`]);
    m.push(["🌤️", "#fff8e5", `Morgen ${prog.total.toFixed(0)} kWh Sonne erwartet – beste Zeit ${hm(prog.von)}–${hm(prog.bis)} Uhr`]);
    m.push(["💰", "#e8f8f0", `Heute schon ${chf(s.gespart)} gespart, ${Math.round(s.autarkie)} % selbst versorgt`]);
    if (min < 7 * 60) m.push(["🌙", "#f1f4fa", `Nacht: Grundlast ${kw(o.kat.grund[Math.floor(min / 5)])} – die Batterie übernimmt`]);
    $("feed").innerHTML = m.slice(0, 4).map(([ic, bg, t]) => `<li><span class="ic" style="background:${bg}">${ic}</span><span>${t}<br><span class="zeit">${hm(min)}</span></span></li>`).join("");
  }

  // ---------- Hauptschleife ----------
  let letzterChart = -Infinity, letzteMeldung = -Infinity;   // -Infinity = beim nächsten Tick sofort zeichnen
  function tick() {
    const z = jetzt();
    const o = tag(z.datum, profil), w = werte(o, z.min, z.raffer);
    const s = summen(o, w.i + 1), prog = prognose(tag(plusTage(z.datum, 1), profil));
    zeichneFluss(o, w);
    zeichneKacheln(o, w, s, prog, z.min);
    const wt = WT[new Date(z.datum + "T12:00:00Z").getUTCDay()];
    const uhr = z.raffer ? `⏩ ${hm(z.min)}` : `${wt} ${z.datum.slice(8)}.${z.datum.slice(5, 7)}. · ${hm(z.min)}`;
    $("uhr").textContent = uhr; $("uhr").classList.toggle("zeitraffer", z.raffer);
    $("flow-uhr").textContent = uhr;
    const t = performance.now();
    if (z.raffer || t - letzterChart > 20000) { zeichneChart(o, z.min); zeichnePreise(o, z.min); letzterChart = t; }
    if (z.raffer || t - letzteMeldung > 10000) { meldungen(o, w, s, prog, z.min); letzteMeldung = t; }
    if (z.raffer && z.min >= 1439.9) stoppeZeitraffer();
  }
  let timer = setInterval(tick, 1000);
  function starteZeitraffer() {
    zeitraffer = { start: performance.now(), datum: zuerich().datum };
    clearInterval(timer); timer = setInterval(tick, 50);
    $("zeitraffer").textContent = "■ Zurück zu live";
  }
  function stoppeZeitraffer() {
    zeitraffer = null; clearInterval(timer); timer = setInterval(tick, 1000);
    $("zeitraffer").textContent = "▶ Ein Tag in 30 Sekunden"; letzterChart = -Infinity; letzteMeldung = -Infinity; tick();
  }
  $("zeitraffer").addEventListener("click", () => zeitraffer ? stoppeZeitraffer() : starteZeitraffer());
  document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
    profil = b.dataset.profil;
    document.querySelectorAll(".seg button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
    $("demo-note").textContent = PROFILE[profil].note;
    letzterChart = -Infinity; letzteMeldung = -Infinity; tick();
  }));
  $("demo-note").textContent = PROFILE[profil].note;
  tick();

  // ---------- KI-Demo ----------
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const log = $("chat-log");
  function nachricht(rolle, html) { const d = document.createElement("div"); d.className = "msg " + rolle; d.innerHTML = html; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }
  nachricht("ki", "Hallo! Ich bin die KI hinter homi. Was soll dein Dashboard können? Tipp auf einen Wunsch oder schreib deinen eigenen.");
  let beschaeftigt = false;

  function karte(html) {
    const out = $("ki-out"), leer = out.querySelector(".leer");
    if (leer) leer.remove();
    const c = document.createElement("div"); c.className = "card neu-karte"; c.innerHTML = html;
    out.prepend(c);
    if (window.innerWidth < 960) c.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function antwort(wunsch, frei) {
    const z = zuerich(), P = PROFILE[profil], o = tag(z.datum, profil), morgen = tag(plusTage(z.datum, 1), profil), prog = prognose(morgen);
    const s = summen(o, N), vorteil = (o.tarif - o.verguetung) / 100;
    switch (wunsch) {
      case "waschen": return {
        schritte: ["Lese Solarprognose für morgen", "Suche das sonnigste 2-Stunden-Fenster", "Rechne die Ersparnis mit deinem Tarif"],
        text: "Fertig! Ab sofort zeigt dir homi jeden Abend den besten Zeitpunkt – auf Wunsch auch per Mail.",
        karte: `<h3>🧺 Bester Waschzeitpunkt</h3><div class="gross">Morgen ${hm(prog.von)}–${hm(prog.bis)}</div><p class="s">☀️ ${prog.kwh.toFixed(1)} kWh Sonne in diesen zwei Stunden. Eine Wäsche (ca. 1.5 kWh) kostet dann praktisch nichts – du sparst ca. ${chf(1.5 * vorteil)} gegenüber Waschen am Abend.</p>` };
      case "boiler": {
        let kwh = 0, sonne = 0;
        for (let i = 0; i < N; i++) { const b = o.kat.boiler[i] / 12; kwh += b; sonne += b * clamp(o.pv[i] / Math.max(0.01, o.last[i]), 0, 1); }
        const anteil = kwh ? 100 * sonne / kwh : 0;
        return {
          schritte: ["Erkenne den Boiler im Verbrauchsverlauf", "Ordne die Leistung zu", "Rechne mit deinem Tarif"],
          text: `Erledigt. Tipp: Läuft der Boiler zur Mittagszeit, steigt der Sonnenanteil auf über 90 %.`,
          karte: `<h3>🚿 ${P.kat.boiler[0]} heute</h3><div class="gross">${kwh.toFixed(1)} kWh · ${chf((kwh - sonne) * o.tarif / 100)}</div><p class="s">${anteil.toFixed(0)} % davon mit eigenem Sonnenstrom gedeckt. Ohne Solaranlage wären es ${chf(kwh * o.tarif / 100)}.</p>` };
      }
      case "negativ": {
        const heuteNeg = o.boerse.findIndex(p => p < 0), morgenNeg = morgen.boerse.findIndex(p => p < 0);
        const wann = heuteNeg >= 0 ? `heute ${heuteNeg}:00 Uhr` : morgenNeg >= 0 ? `morgen ${morgenNeg}:00 Uhr` : null;
        return {
          schritte: ["Verbinde die Day-Ahead-Börse Schweiz", `Lege Regel an: Preis unter 0 → ${P.auto} und Boiler laden`, "Starte im Testmodus"],
          text: "Die Regel läuft jetzt zwei Wochen im Testmodus. Danach siehst du, was sie dir gebracht hätte – und entscheidest.",
          karte: `<h3>📉 Alarm: negative Strompreise</h3><div class="gross">${wann ? "Nächster: " + wann : "Keine in Sicht"}</div><p class="s">Bei Preisen unter 0 Rp/kWh lädt homi ${P.auto} und Boiler und meldet sich bei dir. Status: <b>Testmodus</b>.</p>` };
      }
      case "batterie": {
        const proJahr = s.entladen * vorteil * 300;
        return {
          schritte: ["Simuliere 2, 5 und 10 kWh mit deinem Verbrauch", "Rechne mit Tarif und Vergütung", "Schätze Amortisation"],
          text: "Das ist die Rechnung mit Beispieldaten. Bei dir rechnet homi mit deinem echten Verbrauch – über Wochen, nicht über einen Tag.",
          karte: `<h3>🔋 Speicher-Rechner</h3><div class="gross">ca. CHF ${Math.round(proJahr / 10) * 10} / Jahr</div><p class="s">Die ${P.batt}-kWh-Batterie hat heute ${s.entladen.toFixed(1)} kWh in die Nacht verschoben – jede kWh bringt ${(vorteil * 100).toFixed(1)} Rp. Hochgerechnet auf 300 sonnige Tage.</p>` };
      }
      case "retro": return {
        schritte: ["Nehme dieselben Daten", "Zeichne alles in 8-Bit-Pixeln", "Füge eine Figur und ein Sparschwein hinzu"],
        text: "Gleiche Daten, anderes Design. So sieht unser Pilot in Stäfa aus – dein homi kann aussehen, wie du willst.",
        karte: `<h3>👾 Retro-Look</h3><img src="img/pilot-retro.png" alt="Retro-Dashboard des Pilots in Stäfa" style="border-radius:12px;margin:8px 0;width:100%;max-width:320px;height:auto" loading="lazy"><p class="s">Das echte homi in Stäfa, mit Live-Daten vom Fronius-Wechselrichter.</p>` };
      default: return {
        schritte: ["Verstehe deinen Wunsch", "Prüfe, welche Daten dafür nötig sind"],
        text: "Gute Idee! Genau solche Wünsche setze ich mit KI-Unterstützung für dich um – oft in wenigen Stunden. Ich habe ihn ins Anfrageformular übernommen.",
        karte: `<h3>💡 Dein Wunsch</h3><div class="gross" style="font-size:1.25rem">«${frei}»</div><p class="s">Im Anfrageformular vorgemerkt.</p><a class="btn btn-sun btn-small" href="#kontakt" style="margin-top:8px">Jetzt anfragen →</a>` };
    }
  }

  async function fragen(wunsch, label, frei) {
    if (beschaeftigt) return;
    beschaeftigt = true;
    document.querySelectorAll(".chip").forEach(c => c.disabled = true);
    nachricht("du", label);
    const a = antwort(wunsch, frei);
    const m = nachricht("ki", `<span class="tippt"><i></i><i></i><i></i></span>`);
    await pause(700);
    m.innerHTML = a.schritte.map(s => `<span class="schritt">${s} …</span>`).join("");
    const sp = m.querySelectorAll(".schritt");
    for (const el of sp) { await pause(650); el.classList.add("ok"); el.textContent = el.textContent.replace(" …", ""); }
    await pause(400);
    m.insertAdjacentHTML("beforeend", `<span style="display:block;margin-top:6px">${a.text}</span>`);
    log.scrollTop = log.scrollHeight;
    karte(a.karte);
    beschaeftigt = false;
    document.querySelectorAll(".chip").forEach(c => { if (!c.dataset.genutzt) c.disabled = false; });
  }
  document.querySelectorAll(".chip").forEach(c => c.addEventListener("click", () => { c.dataset.genutzt = "1"; fragen(c.dataset.w, c.textContent); }));
  $("wunsch-form").addEventListener("submit", e => {
    e.preventDefault();
    const t = $("wunsch-text").value.trim().replace(/[<>]/g, "");
    if (!t) return;
    $("wunsch-text").value = "";
    const feld = $("wunsch-feld");
    feld.value = (feld.value ? feld.value + "\n" : "") + t;
    fragen("frei", t, t);
  });

  // ---------- Pakete → Formular ----------
  document.querySelectorAll("[data-paket]").forEach(a => a.addEventListener("click", () => { $("paket").value = a.dataset.paket; }));

  // ---------- Anfrageformular ----------
  const geladen = Date.now();
  $("anfrage").addEventListener("submit", async e => {
    e.preventDefault();
    const f = e.target, st = $("form-status"), fd = new FormData(f);
    const daten = { name: fd.get("name").trim(), email: fd.get("email").trim(), telefon: fd.get("telefon").trim(), ort: fd.get("ort").trim(),
      objekt: fd.get("objekt"), paket: fd.get("paket"), vorhanden: fd.getAll("vorhanden"), wunsch: fd.get("wunsch").trim(),
      website: fd.get("website"), einwilligung: fd.get("einwilligung") === "on", t: Date.now() - geladen, demo: profil };
    st.className = "form-status fehler";
    if (!daten.name) return st.textContent = "Bitte gib deinen Namen an.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(daten.email)) return st.textContent = "Bitte gib eine gültige E-Mail-Adresse an.";
    if (!daten.einwilligung) return st.textContent = "Bitte bestätige die Einwilligung zur Speicherung.";
    st.className = "form-status"; st.textContent = "Wird gesendet …";
    f.querySelector("button[type=submit]").disabled = true;
    try {
      const r = await fetch("api/anfrage", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(daten) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.fehler || "Fehler " + r.status);
      f.outerHTML = `<div class="card danke"><div class="gross">☀️</div><h3>Danke, ${daten.name.split(" ")[0].replace(/[<>&]/g, "")}!</h3><p class="sub">Deine Anfrage ist angekommen. Ich melde mich innerhalb von zwei Arbeitstagen bei dir.</p></div>`;
    } catch (err) {
      f.querySelector("button[type=submit]").disabled = false;
      const body = encodeURIComponent(`Name: ${daten.name}\nTelefon: ${daten.telefon}\nOrt: ${daten.ort}\nObjekt: ${daten.objekt}\nInteresse: ${daten.paket}\nVorhanden: ${daten.vorhanden.join(", ")}\n\n${daten.wunsch}`);
      st.className = "form-status fehler";
      st.innerHTML = `Das hat leider nicht geklappt (${String(err.message).replace(/[<>&]/g, "")}). <a href="mailto:patrick@biber.solar?subject=homi%20Anfrage&body=${body}">Stattdessen per Mail senden</a>`;
    }
  });

  // ---------- Beispiel Minuspreis-Tag (gleiche Daten wie die Beispielrechnung) ----------
  (function beispielTag() {
    const ueb = [0, 0, 0, 0, 0, 0, 0, 0, 0.3, 1.4, 2.9, 4.4, 4.9, 4.9, 4.4, 3.4, 1.9, 0.6, 0, 0, 0, 0, 0, 0];
    const preis = [9, 8, 8, 8, 8, 9, 12, 15, 14, 10, 4, -2, -6, -8, -5, 1, 8, 15, 22, 24, 20, 16, 12, 10];
    const W = 640, H = 260, L = 54, R = 40, T = 16, B = 28, w = W - L - R, h = H - T - B, bw = w / 24;
    const pMin = -10, pMax = 26, Yp = v => T + h - (v - pMin) / (pMax - pMin) * h, Yk = v => T + h - v / 5.5 * h;
    let g = `<rect x="${L + 11 * bw}" y="${T}" width="${4 * bw}" height="${h}" fill="#f04438" opacity=".08"/><text x="${L + 13 * bw}" y="${T + 14}" text-anchor="middle" font-size="12" font-weight="700" fill="#d92d20">Minuspreise: Einspeisen kostet</text>`;
    g += `<path d="M${L} ${Yk(0)}` + ueb.map((v, i) => `L${L + i * bw + bw / 2} ${Yk(v)}`).join("") + `L${L + w} ${Yk(0)}Z" fill="#FFB020" opacity=".35"/>`;
    g += `<line x1="${L}" x2="${W - R}" y1="${Yp(0)}" y2="${Yp(0)}" stroke="#98a2b3"/>`;
    preis.forEach((p, i) => { const y = Yp(p), y0 = Yp(0); g += `<rect x="${L + i * bw + 3}" y="${Math.min(y, y0)}" width="${bw - 6}" height="${Math.max(1, Math.abs(y - y0))}" rx="2" fill="${p < 0 ? "#f04438" : "#2e90fa"}" opacity=".85"><title>${i}:00 · ${p} Rp/kWh</title></rect>`; });
    [0, 6, 12, 18].forEach(hh => g += `<text x="${L + hh * bw + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="#64748b">${hh}h</text>`);
    [-10, 0, 10, 20].forEach(v => g += `<text x="${L - 6}" y="${Yp(v) + 4}" text-anchor="end" font-size="11" fill="#2e90fa">${v}</text>`);
    g += `<text x="${L - 6}" y="${T - 4}" text-anchor="end" font-size="10" fill="#2e90fa">Rp/kWh</text><text x="${W - R + 6}" y="${T - 4}" font-size="10" fill="#b54708">kW</text>`;
    [0, 2.5, 5].forEach(v => g += `<text x="${W - R + 6}" y="${Yk(v) + 4}" font-size="11" fill="#b54708">${v}</text>`);
    $("ein-chart").innerHTML = g;
  })();

  // ---------- Gratis-Minuspreis-Warnung ----------
  const wq = new URLSearchParams(location.search).get("warnung");
  if (wq) {
    const t = { aktiv: ["ok", "✓ Bestätigt! Du bekommst ab jetzt an Tagen mit negativen Strompreisen um 6:45 Uhr eine Mail."],
      abgemeldet: ["ok", "Du bist abgemeldet und bekommst keine Warnungen mehr."],
      ungueltig: ["nein", "Dieser Link ist nicht (mehr) gültig. Melde dich einfach neu an."] }[wq];
    if (t) { const d = document.createElement("div"); d.className = "warnung-meldung " + t[0]; d.textContent = t[1]; document.querySelector("#einspeisung .section-head").after(d); }
  }
  $("warnung-form").addEventListener("submit", async e => {
    e.preventDefault();
    const f = e.target, st = $("warnung-status"), fd = new FormData(f);
    const daten = { email: fd.get("email").trim(), einwilligung: fd.get("einwilligung") === "on", website: fd.get("website"), t: Date.now() - geladen };
    st.className = "form-status fehler";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(daten.email)) return st.textContent = "Bitte gib eine gültige E-Mail-Adresse an.";
    if (!daten.einwilligung) return st.textContent = "Bitte bestätige, dass du die Warnung erhalten möchtest.";
    st.className = "form-status"; st.textContent = "Wird gesendet …";
    try {
      const r = await fetch("api/warnung", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(daten) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.fehler || "Fehler " + r.status);
      f.innerHTML = `<div class="warnung-meldung ok" style="grid-column:1/-1;margin:0">📬 Fast geschafft! Bitte bestätige den Link in der Mail, die wir dir gerade geschickt haben.</div>`;
    } catch (err) { st.className = "form-status fehler"; st.textContent = "Das hat nicht geklappt: " + String(err.message).replace(/[<>&]/g, "") + ". Versuch es bitte später nochmals."; }
  });

  // ---------- Einblenden beim Scrollen ----------
  const rein = document.querySelectorAll(".rein");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(es => es.forEach(x => { if (x.isIntersecting) { x.target.classList.add("sichtbar"); io.unobserve(x.target); } }), { threshold: 0.12 });
    rein.forEach(el => io.observe(el));
  } else rein.forEach(el => el.classList.add("sichtbar"));
})();
