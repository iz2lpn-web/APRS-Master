// APRS Master e Geopin by IZ2LPN op. Giorgio - piccoli grafici SVG senza librerie esterne (meteo e statistiche)
// SPDX-License-Identifier: GPL-3.0-or-later
//
// geoChart(contenitore, {
//   type: "line" | "bar",
//   x: [...],                       // istanti (secondi Unix) per "line", etichette per "bar"
//   series: [{ name, values, color: "c1" | "c2" }],   // al massimo due serie, stessa unità
//   unit: "°C", digits: 1, xLabel: x => testo, yZero: true, height: 150, label: "descrizione"
// })
// Una sola scala verticale; griglia leggera; crocino con riquadro dei valori al passaggio del mouse
// (o del dito); con due serie la legenda sta sopra il grafico.
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const el = (n, a, p) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  const fmtNum = (v, d) => v == null || !isFinite(v) ? "—" : v.toLocaleString(document.documentElement.lang || "it", { minimumFractionDigits: d, maximumFractionDigits: d });

  function niceTicks(lo, hi, n) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
    const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, out = [];
    for (let v = a; v <= b + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function draw(box, o) {
    box.textContent = "";
    const W = Math.max(240, box.clientWidth), H = o.height || 150;
    const m = { l: 44, r: 10, t: o.series.length > 1 ? 24 : 10, b: 22 };
    const vals = o.series.flatMap(s => s.values).filter(v => v != null && isFinite(v));
    if (!vals.length) { box.innerHTML = `<p class="chart-empty">${o.empty || "Nessun dato"}</p>`; return; }
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (o.type === "bar" || o.yZero) lo = Math.min(0, lo);
    const ticks = niceTicks(lo, hi, 3);
    lo = ticks[0]; hi = ticks[ticks.length - 1];
    const n = o.x.length, iw = W - m.l - m.r, ih = H - m.t - m.b;
    const xs = o.type === "bar"
      ? i => m.l + (i + 0.5) * iw / n
      : (() => { const a = o.x[0], b = o.x[n - 1] === a ? a + 1 : o.x[n - 1]; return i => m.l + (o.x[i] - a) / (b - a) * iw; })();
    const ys = v => m.t + ih - (v - lo) / (hi - lo) * ih;
    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label || "", class: "chart" }, box);
    // griglia e scala
    for (const t of ticks) {
      el("line", { x1: m.l, x2: W - m.r, y1: ys(t), y2: ys(t), class: t === 0 && lo < 0 ? "zero" : "grid" }, svg);
      el("text", { x: m.l - 6, y: ys(t) + 4, "text-anchor": "end", class: "ax" }, svg).textContent = fmtNum(t, Math.abs(t) < 10 && t % 1 ? 1 : 0);
    }
    const xl = o.xLabel || (v => v);
    let xi;
    if (n <= 1) xi = [0];
    else if (o.type === "bar") {               // etichette ad almeno 52 px l'una dall'altra
      const step = Math.max(1, Math.ceil(52 / (iw / n)));
      xi = [...Array(n).keys()].filter(i => i % step === 0);
    } else xi = [0, Math.floor((n - 1) / 2), n - 1];
    for (const i of xi) {
      if (xl(o.x[i], i) === "") continue;
      el("text", { x: xs(i), y: H - 6, "text-anchor": i === 0 && o.type !== "bar" ? "start" : i === n - 1 && o.type !== "bar" ? "end" : "middle", class: "ax" }, svg).textContent = xl(o.x[i], i);
    }
    // legenda (solo con due serie)
    if (o.series.length > 1) {
      let lx = m.l;
      for (const s of o.series) {
        el("rect", { x: lx, y: 6, width: 14, height: 3, rx: 1.5, class: "fill-" + s.color }, svg);
        const t = el("text", { x: lx + 19, y: 11, class: "lg" }, svg); t.textContent = s.name;
        lx += 30 + s.name.length * 6.5;
      }
    }
    // segni
    if (o.type === "bar") {
      const bw = Math.max(1, iw / n - 2);
      o.series[0].values.forEach((v, i) => {
        if (v == null || v === 0) return;
        const x = xs(i) - bw / 2, y0 = ys(0), y1 = ys(v), r = Math.min(4, bw / 2, Math.abs(y0 - y1));
        el("path", { d: `M${x},${y0}V${y1 + r}q0,${-r} ${r},${-r}h${bw - 2 * r}q${r},0 ${r},${r}V${y0}Z`, class: "fill-" + (o.series[0].color || "c1") }, svg);
      });
    } else {
      for (const s of o.series) {
        // interruzioni dove manca il dato o c'è un buco di tempo grande (oltre 6 volte il passo tipico)
        const gaps = []; for (let i = 1; i < n; i++) gaps.push(o.x[i] - o.x[i - 1]);
        const typical = gaps.length ? gaps.slice().sort((a, b) => a - b)[Math.floor(gaps.length / 2)] : 0;
        let d = "", pen = false;
        s.values.forEach((v, i) => {
          if (v == null || !isFinite(v)) { pen = false; return; }
          const brk = i > 0 && typical > 0 && o.x[i] - o.x[i - 1] > Math.max(6 * typical, 1800);
          d += `${pen && !brk ? "L" : "M"}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`;
          pen = true;
        });
        el("path", { d, class: "line stroke-" + (s.color || "c1") }, svg);
      }
    }
    // passaggio del mouse: crocino e valori del punto più vicino
    const cross = el("line", { y1: m.t, y2: m.t + ih, class: "cross", visibility: "hidden" }, svg);
    const dots = o.type === "bar" ? [] : o.series.map(s => el("circle", { r: 4, class: "dot fill-" + (s.color || "c1"), visibility: "hidden" }, svg));
    const tip = document.createElement("div");
    tip.className = "chart-tip"; tip.hidden = true; box.appendChild(tip);
    const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
    const near = px => {
      let best = 0, bd = Infinity;
      for (let i = 0; i < n; i++) { const dd = Math.abs(xs(i) - px); if (dd < bd) { bd = dd; best = i; } }
      return best;
    };
    const show = ev => {
      const r = svg.getBoundingClientRect(), px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      const i = near(px), x = xs(i);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      dots.forEach((c, k) => { const v = o.series[k].values[i]; if (v == null) { c.setAttribute("visibility", "hidden"); return; } c.setAttribute("cx", x); c.setAttribute("cy", ys(v)); c.setAttribute("visibility", "visible"); });
      tip.innerHTML = `<b>${xl(o.x[i], i, true)}</b>` + o.series.map(s => `<span><i class="fill-${s.color || "c1"}"></i>${o.series.length > 1 ? s.name + ": " : ""}${fmtNum(s.values[i], o.digits ?? 0)} ${o.unit || ""}</span>`).join("");
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = Math.min(W - tw - 2, Math.max(0, x + 10 > W - tw ? x - tw - 10 : x + 10)) + "px";
      tip.style.top = m.t + "px";
    };
    const hide = () => { cross.setAttribute("visibility", "hidden"); dots.forEach(c => c.setAttribute("visibility", "hidden")); tip.hidden = true; };
    hit.addEventListener("mousemove", show); hit.addEventListener("touchstart", show, { passive: true });
    hit.addEventListener("touchmove", show, { passive: true }); hit.addEventListener("mouseleave", hide);
  }

  window.geoChart = function (box, o) {
    box.classList.add("chart-box");
    draw(box, o);
    if (window.ResizeObserver) {
      let w = box.clientWidth;
      new ResizeObserver(() => { if (Math.abs(box.clientWidth - w) > 4) { w = box.clientWidth; draw(box, o); } }).observe(box);
    }
  };
})();
