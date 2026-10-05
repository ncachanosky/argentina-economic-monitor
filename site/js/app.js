/* Argentina Economic Monitor — front end
 *
 * Reads data/manifest.json and data/<indicator>.json (written by
 * pipeline/export.py) and renders one card per indicator. All
 * transformations (y/y, m/m) are computed here, in the browser.
 */
(function () {
  "use strict";

  const DATA = "data/";
  const MAX_PANEL_SERIES = 4;              // fixed palette has four distinguishable slots
  const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4"];
  const EXPORT = { width: 1200, height: 800 }; // EO figure standard

  // Transformations by measure. Indices change in percent; rates (e.g. capacity
  // utilization, already in %) change in percentage points.
  //   suffix: appended to values; signed: show +/-; change: is a change, not a level
  const INDEX_TRANSFORMS = {
    level: { label: "Level", short: null, suffix: "", signed: false, change: false },
    yoy:   { label: "y/y %", short: "Year-over-year change, %", suffix: "%", signed: true, change: true },
    mom:   { label: "m/m %", short: "Month-over-month change, %", suffix: "%", signed: true, change: true },
  };
  const RATE_TRANSFORMS = {
    level: { label: "Level", short: null, suffix: "%", signed: false, change: false },
    yoy:   { label: "y/y (pp)", short: "Year-over-year change, percentage points", suffix: " pp", signed: true, change: true },
    mom:   { label: "m/m (pp)", short: "Month-over-month change, percentage points", suffix: " pp", signed: true, change: true },
  };
  const TRANSFORMS = INDEX_TRANSFORMS;
  function tfs(ind) {
    const all = ind.measure === "rate" ? RATE_TRANSFORMS : INDEX_TRANSFORMS;
    const keys = ind.transforms && ind.transforms.length ? ind.transforms : Object.keys(all);
    const out = Object.fromEntries(keys.map((k) => [k, { ...all[k] }]));
    if (ind.frequency === "Q" && out.mom) {
      out.mom.label = out.mom.label.replace("m/m", "q/q");
      out.mom.short = out.mom.short.replace("Month-over-month", "Quarter-over-quarter");
    }
    return out;
  }
  const RANGES = { "2Y": 2, "5Y": 5, "10Y": 10, "Max": null };

  // ---------- small helpers ----------
  const $ = (sel, el = document) => el.querySelector(sel);
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid);
    return el;
  };
  const appendAll = (el, ...kids) => el.append(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const isDark = () => {
    const t = document.documentElement.dataset.theme;
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const fmtMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
  const fmtShortMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  // Period label: "Jul 2026" for monthly data, "Q3 2026" for quarterly.
  const fmtPeriod = (iso, freq) => {
    if (freq !== "Q") return fmtShortMonth(iso);
    const [y, m] = iso.split("-").map(Number);
    return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
  };
  const specOf = (p) => (p && typeof p === "object" ? p : p ? INDEX_TRANSFORMS.yoy : INDEX_TRANSFORMS.level);
  const fmtNum = (v, p) => {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    const s = specOf(p);
    return s.signed ? (v > 0 ? "+" : "") + v.toFixed(1) + s.suffix
      : v.toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 }) + s.suffix;
  };
  const monthIndex = (iso) => { const [y, m] = iso.split("-").map(Number); return y * 12 + (m - 1); };

  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  // ---------- transformations ----------
  function transform(dates, values, kind, measure, freq) {
    if (kind === "level") return values.slice();
    const diff = measure === "rate";
    // Lags in months: a year back is 12 months for both monthly and quarterly
    // dates; one period back is 1 month, or 3 for quarters.
    const lag = kind === "yoy" ? 12 : freq === "Q" ? 3 : 1;
    const mi = dates.map(monthIndex);
    const pos = new Map(mi.map((m, i) => [m, i]));
    return values.map((v, i) => {
      const j = pos.get(mi[i] - lag);
      const prev = j === undefined ? null : values[j];
      if (v === null || prev === null) return null;
      if (diff) return v - prev;
      return prev === 0 ? null : (v / prev - 1) * 100;
    });
  }

  function lastValid(dates, values) {
    for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null && !Number.isNaN(values[i])) return { date: dates[i], value: values[i], i };
    return null;
  }

  function startDate(lastIso, years) {
    if (!years) return null;
    const [y, m] = lastIso.split("-").map(Number);
    return `${y - years}-${String(m).padStart(2, "0")}-01`;
  }

  function rangeFromDefault(ind) {
    const s = ind.default && ind.default.start;
    if (!s) return "10Y";
    const yrs = (monthIndex(ind.last_obs) - monthIndex(s + (s.length === 7 ? "-01" : ""))) / 12;
    return yrs <= 2.5 ? "2Y" : yrs <= 6 ? "5Y" : yrs <= 12 ? "10Y" : "Max";
  }

  // ---------- chart theming ----------
  function baseLayout(opts = {}) {
    const ink = cssVar("--ink"), ink2 = cssVar("--ink-2"), grid = cssVar("--grid"), rule = cssVar("--rule");
    const font = cssVar("--font-ui");
    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: font, size: 13, color: ink2 },
      margin: { l: 52, r: 16, t: 10, b: 36 },
      hovermode: "x unified",
      hoverlabel: { bgcolor: cssVar("--surface"), bordercolor: rule, font: { family: font, color: ink } },
      showlegend: false,
      xaxis: { type: "date", gridcolor: grid, linecolor: rule, tickcolor: rule, zeroline: false, showspikes: false, ...opts.xaxis },
      yaxis: { gridcolor: grid, linecolor: rule, zerolinecolor: ink2, zerolinewidth: 1, automargin: true, ticksuffix: specOf(opts.pct).suffix, ...opts.yaxis },
      ...opts.extra,
    };
  }

  const PLOT_CONFIG = {
    responsive: true,
    displaylogo: false,
    displayModeBar: "hover",
    modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toImage", "zoomIn2d", "zoomOut2d"],
  };

  // ---------- downloads ----------
  function downloadBlob(text, filename, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = h("a", { href: url, download: filename });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function toCSV(header, rows) {
    const esc = (s) => /[",\n]/.test(String(s)) ? `"${String(s).replace(/"/g, '""')}"` : s;
    return [header.map(esc).join(","), ...rows.map((r) => r.map((v) => (v === null || v === undefined ? "" : esc(v))).join(","))].join("\n") + "\n";
  }

  // Render the current view as a branded 1200x800 PNG (always light theme).
  async function exportPNG(ind, traces, subtitle, pct, filename, extras = {}) {
    const light = { ink: "#36454F", ink2: "#5A6872", ink3: "#85909A", grid: "#ECE9E3", rule: "#E3E0D9" };
    const lightSeries = ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5"];
    const font = '"Calibri", "Carlito", "Segoe UI", Arial, sans-serif';
    const data = traces.map((t, i) => {
      const c = { ...t };
      const color = t._light || (t._slot !== undefined ? lightSeries[t._slot] : lightSeries[0]);
      if (c.type === "bar") {
        const mc = t._lightColors || (Array.isArray(c.marker && c.marker.color) ? c.marker.color.map((x) => x === "__muted__" ? "#C9CED3" : x) : color);
        c.marker = { ...(c.marker || {}), color: mc };
      }
      if (c.line) c.line = { ...c.line, color, width: c.line.dash ? 1.5 : 2.5 };
      return c;
    });
    const layout = {
      width: EXPORT.width, height: EXPORT.height,
      paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF",
      font: { family: font, size: 18, color: light.ink2 },
      margin: { l: 90, r: 50, t: 130, b: 120 },
      title: {
        text: `<b>${ind.title}</b><br><span style="font-size:18px;color:${light.ink2}">${subtitle}</span>`,
        x: 0.035, xanchor: "left", y: 0.95, yanchor: "top", font: { size: 28, color: light.ink, family: font },
      },
      showlegend: data.filter((t) => t.showlegend !== false).length > 1,
      legend: { orientation: "h", x: 0, y: 1.02, xanchor: "left", yanchor: "bottom", font: { size: 16, color: light.ink } },
      xaxis: { type: data[0] && data[0].orientation === "h" ? "linear" : "date", gridcolor: light.grid, linecolor: light.rule, ticks: "outside", tickcolor: light.rule },
      yaxis: { gridcolor: light.grid, linecolor: light.rule, zerolinecolor: light.ink2, ticksuffix: specOf(pct).suffix, automargin: true },
      shapes: (extras.shapes || []).map((sh) => ({ ...sh, line: { ...(sh.line || {}), color: (sh.line && sh.line._light) || light.ink3 } })),
      annotations: [
        ...(extras.annotations || []).map((a) => ({ ...a, font: { ...(a.font || {}), size: 15, color: light.ink2 } })),
        { text: `Source: ${ind.source_label}. Latest observation: ${ind.frequency === "Q" ? fmtPeriod(ind.last_obs, "Q") : fmtMonth(ind.last_obs)}.${ind.source_units ? ` Source data in ${ind.source_units}.` : ""}`, xref: "paper", yref: "paper", x: 0, y: -0.13, xanchor: "left", yanchor: "top", showarrow: false, font: { size: 15, color: light.ink3 } },
        { text: "<b>Argentina Economic Monitor</b> · Economic Order", xref: "paper", yref: "paper", x: 1, y: -0.13, xanchor: "right", yanchor: "top", showarrow: false, font: { size: 15, color: "#B87333" } },
      ],
      bargap: 0.25,
    };
    if (extras.uniformtext) layout.uniformtext = extras.uniformtext;
    if (extras.barmode) layout.barmode = extras.barmode;
    if (data[0] && data[0].orientation === "h") { layout.xaxis.ticksuffix = (extras.xsuffix !== undefined ? extras.xsuffix : specOf(pct).suffix); if (extras.xrange) layout.xaxis.range = extras.xrange; layout.yaxis.ticksuffix = ""; layout.margin.l = 260; }
    const url = await Plotly.toImage({ data, layout }, { format: "png", width: EXPORT.width, height: EXPORT.height });
    const a = h("a", { href: url, download: filename });
    document.body.append(a); a.click(); a.remove();
  }


  // ---------- presidential terms ----------
  // Filled from manifest.presidencies. Convention (registry/series.yaml): the
  // handover month belongs to the outgoing president; a term's first month is
  // the first full month after inauguration and its base month the one before.
  let PRESIDENCIES = [];
  let PARTY_COLORS = {};
  // Party color for the current theme, and the light-theme color used in PNG exports.
  const termColor = (p) => { const c = PARTY_COLORS[p.party]; return c ? (isDark() ? c.dark : c.light) : cssVar("--ink-3"); };
  const termLight = (p) => { const c = PARTY_COLORS[p.party]; return c ? c.light : "#85909A"; };

  const addMonths = (iso, k) => { const m = monthIndex(iso) + k; return `${Math.floor(m / 12)}-${String((m % 12) + 1).padStart(2, "0")}-01`; };
  const monthOf = (isoDay) => isoDay.slice(0, 7) + "-01";

  // For quarterly data the same rule applies to quarters: the handover
  // quarter belongs to the outgoing president (Milei: base Q4 2023 = 100).
  const quarterOf = (iso) => { const [y, m] = iso.split("-").map(Number); return `${y}-${String(Math.floor((m - 1) / 3) * 3 + 1).padStart(2, "0")}-01`; };
  function termWindow(p, freq) {
    if (freq === "Q") {
      const q0 = quarterOf(p.start);
      const first = p.start === q0 ? q0 : addMonths(q0, 3);
      return { first, base: addMonths(first, -3), last: p.end ? quarterOf(p.end) : null };
    }
    const startDay = Number(p.start.slice(8, 10));
    const first = startDay === 1 ? monthOf(p.start) : addMonths(monthOf(p.start), 1);
    return { first, base: addMonths(first, -1), last: p.end ? monthOf(p.end) : null };
  }

  function termOf(iso, freq) {
    for (const p of PRESIDENCIES) {
      const w = termWindow(p, freq);
      if (iso >= w.first && (!w.last || iso <= w.last)) return p;
    }
    return null;
  }

  // Per-term statistics on a full (not range-clipped) series.
  function termStats(dates, y, isLevel, measure, freq) {
    const idx = new Map(dates.map((d, i) => [d, i]));
    const out = [];
    for (const p of PRESIDENCIES) {
      const w = termWindow(p, freq);
      const pts = [];
      for (let i = 0; i < dates.length; i++) {
        const d = dates[i];
        if (d >= w.first && (!w.last || d <= w.last) && y[i] !== null) pts.push([d, y[i]]);
      }
      if (!pts.length) continue;
      const vals = pts.map((q) => q[1]);
      const iMin = vals.indexOf(Math.min(...vals)), iMax = vals.indexOf(Math.max(...vals));
      const bi = idx.get(w.base);
      const start = isLevel && bi !== undefined ? y[bi] : null;
      const end = vals[vals.length - 1];
      out.push({
        p, first: pts[0][0], last: pts[pts.length - 1][0], n: pts.length,
        partial: pts[0][0] > w.first, ongoing: !w.last,
        start, end, change: start === null || start === undefined ? null : measure === "rate" ? end - start : start ? (end / start - 1) * 100 : null,
        avg: vals.reduce((a, b) => a + b, 0) / vals.length,
        min: vals[iMin], minAt: pts[iMin][0], max: vals[iMax], maxAt: pts[iMax][0],
      });
    }
    return out;
  }

  // ---------- shared UI pieces ----------
  function segmented(options, current, onChange, label) {
    const wrap = h("div", { class: "seg", role: "group", "aria-label": label });
    const buttons = {};
    for (const [key, text] of options) {
      buttons[key] = h("button", { type: "button", "aria-pressed": String(key === current), onclick: () => onChange(key) }, text);
      wrap.append(buttons[key]);
    }
    wrap.update = (cur, disabled = {}) => {
      for (const [k, b] of Object.entries(buttons)) { b.setAttribute("aria-pressed", String(k === cur)); b.disabled = !!disabled[k]; }
    };
    return wrap;
  }

  function cardFrame(ind, wide) {
    const badge = ind.status === "error" ? h("span", { class: "status-badge", title: "The last update attempt failed; showing the last validated data." }, "Update failed — showing last good data")
      : ind.status === "stale" ? h("span", { class: "status-badge", title: "No new observation for longer than usual." }, "Stale") : null;
    const card = h("article", { class: "card" + (wide ? " wide" : ""), id: `ind-${ind.id}` },
      h("h3", {}, ind.title, badge),
      h("p", { class: "desc" }, ind.description));
    return card;
  }

  // "Next release" from the official calendar, judged in Buenos Aires time.
  function nextReleaseNote(ind) {
    const nr = ind.next_release;
    if (!nr) return null;
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/Argentina/Buenos_Aires" });
    const day = new Date(nr.date + "T12:00:00Z").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
    const period = fmtPeriod(nr.period + "-01", ind.frequency);
    const text = nr.date > today ? `Next release: ${day} (${period} data)`
      : nr.date === today ? `Next release: today (${period} data)`
      : `${period} data was due ${day}; updating`;
    return h("span", { class: "next-release" }, nr.calendar_url
      ? h("a", { href: nr.calendar_url, target: "_blank", rel: "noopener", title: `INDEC release calendar: ${nr.name || ""}` }, text) : text);
  }

  function footer(ind, onPNG, onCSV) {
    const src = Object.values(ind.variants)[0].source_url;
    const nr = nextReleaseNote(ind);
    return h("div", { class: "card-foot" },
      h("span", {}, "Source: ", src ? h("a", { href: src, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label,
        ` · Latest: ${fmtPeriod(ind.last_obs, ind.frequency)}`, nr ? " · " : null, nr),
      h("div", { class: "actions" },
        h("button", { class: "btn", type: "button", onclick: onPNG, title: "Download this chart as a 1200×800 PNG" }, "PNG"),
        h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download the data in this view as CSV" }, "CSV"),
        h("a", { class: "btn", href: `${DATA}${ind.id}.csv`, download: `${ind.id}_all.csv`, title: "All variants, levels, as published" }, "All data")));
  }

  function tableView(dates, cols, pct, freq) {
    const details = h("details", { class: "table-view" }, h("summary", {}, "Show data table"));
    const fill = () => {
      if (details.dataset.filled) return;
      const n = dates.length, start = Math.max(0, n - 36);
      const tbody = h("tbody");
      for (let i = n - 1; i >= start; i--) {
        tbody.append(h("tr", {}, h("td", {}, fmtPeriod(dates[i], freq)), ...cols.map((c) => h("td", {}, fmtNum(c.values[i], pct)))));
      }
      details.append(h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, freq === "Q" ? "Quarter" : "Month"), ...cols.map((c) => h("th", {}, c.label)))), tbody)));
      details.dataset.filled = "1";
    };
    details.addEventListener("toggle", fill);
    details.refresh = (d, c, p) => { dates = d; cols = c; pct = p; details.querySelector(".table-scroll")?.remove(); delete details.dataset.filled; if (details.open) fill(); };
    return details;
  }

  // ---------- card: one series with variants ----------
  function variantsCard(ind, wide) {
    const variantKeys = Object.keys(ind.variants);
    const T = tfs(ind);
    const isRate = ind.measure === "rate";
    const DF = ind.frequency === "Q" ? "Q%q %Y" : "%b %Y";   // hover date format
    const fP = (iso) => fmtPeriod(iso, ind.frequency);
    const state = {
      variant: ind.default.variant,
      transform: ind.default.transform || "level",
      range: rangeFromDefault(ind),
      base: "published",          // "published" | "term:<id>" | "custom"
      customBase: null,           // "YYYY-MM-01"
      byPres: false,
    };
    const card = cardFrame(ind, !!wide);
    const chartEl = h("div", { class: "chart" + (wide ? " tall-ish" : ""), role: "img", "aria-label": `${ind.title} chart` });
    const hint = h("p", { class: "hint" });
    const statsEl = h("div", { class: "term-stats", hidden: true });

    const vSeg = variantKeys.length > 1 ? segmented(variantKeys.map((k) => [k, ind.variants[k].label]), state.variant, (k) => { state.variant = k; if (k === "original" && state.transform === "mom") state.transform = "yoy"; draw(); }, "Series") : null;
    const tSeg = segmented(Object.entries(T).map(([k, t]) => [k, t.label]), state.transform, (k) => { state.transform = k; draw(); }, "Transformation");
    const rSeg = segmented(Object.keys(RANGES).map((k) => [k, k]), state.range, (k) => { state.range = k; draw(); }, "Time range");

    // Index base: as published, start of a presidential term, or a custom month.
    const firstDate = ind.dates[0];
    const baseSel = h("select", { class: "select", "aria-label": "Index base (month = 100)" },
      h("option", { value: "published" }, `As published (${ind.units.replace(/^Index,?\s*/i, "") || "source base"})`),
      PRESIDENCIES.length ? h("optgroup", { label: "Start of presidential term" },
        PRESIDENCIES.slice().reverse().map((p) => {
          const w = termWindow(p, ind.frequency);
          return h("option", { value: `term:${p.id}`, disabled: w.base < firstDate ? true : null }, `${p.short} (${fmtPeriod(w.base, ind.frequency)} = 100)`);
        })) : null,
      h("option", { value: "custom" }, "Custom month…"));
    const customIn = h("input", { type: "month", class: "select", "aria-label": "Custom base month", min: firstDate.slice(0, 7), max: ind.last_obs.slice(0, 7), hidden: true });
    baseSel.addEventListener("change", () => {
      state.base = baseSel.value;
      customIn.hidden = state.base !== "custom";
      if (state.base === "custom" && !state.customBase) { customIn.value = ind.last_obs.slice(0, 7); state.customBase = ind.last_obs; }
      draw();
    });
    customIn.addEventListener("change", () => { if (customIn.value) { state.customBase = customIn.value + "-01"; draw(); } });
    const baseWrap = isRate ? null : h("label", { class: "base-pick" }, h("span", {}, "Base = 100:"), baseSel, customIn);

    const presBtn = PRESIDENCIES.length ? h("button", { class: "btn toggle", type: "button", "aria-pressed": "false", onclick: () => { state.byPres = !state.byPres; draw(); } }, "By presidency") : null;
    let table;

    function baseMonth() {
      if (state.transform !== "level" || state.base === "published") return null;
      if (state.base === "custom") return state.customBase;
      const p = PRESIDENCIES.find((q) => `term:${q.id}` === state.base);
      return p ? termWindow(p, ind.frequency).base : null;
    }

    // Full transformed (and rebased) series for a variant, plus its label/units.
    function series(variantKey) {
      const v = ind.variants[variantKey];
      let y = transform(ind.dates, v.values, state.transform, ind.measure, ind.frequency);
      const bm = isRate ? null : baseMonth();
      let units = ind.units, rebased = false;
      if (bm) {
        const bi = ind.dates.indexOf(bm);
        const b = bi >= 0 ? v.values[bi] : null;
        if (b) { y = y.map((q) => (q === null ? null : (q / b) * 100)); units = `Index, ${fmtPeriod(bm, ind.frequency)} = 100`; rebased = true; }
      }
      return { v, y, units, rebased };
    }

    function visibleIdx(y) {
      const start = startDate(ind.last_obs, RANGES[state.range]);
      return ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && y[i] !== null);
    }

    // Build traces; each carries _light (export color) so the PNG matches.
    function build() {
      const t = T[state.transform];
      const main = series(state.variant);
      const idx = visibleIdx(main.y);
      const x = idx.map((i) => ind.dates[i]), y = idx.map((i) => main.y[i]);
      const sfx = t.suffix;
      const traces = [], shapes = [], annotations = [];

      if (!state.byPres) {
        const color = cssVar(SERIES_VARS[0]);
        const hover = `%{x|${DF}}: <b>%{y:,.1f}${sfx}</b><extra>${main.v.label}</extra>`;
        traces.push(state.transform === "mom"
          ? { type: "bar", x, y, name: main.v.label, marker: { color }, hovertemplate: hover, _slot: 0 }
          : { type: "scatter", mode: "lines", x, y, name: main.v.label, line: { color, width: 2 }, hovertemplate: hover, _slot: 0 });
      } else if (state.transform === "mom") {
        const terms = x.map((d) => termOf(d, ind.frequency));
        traces.push({
          type: "bar", x, y, name: main.v.label,
          marker: { color: terms.map((p) => (p ? termColor(p) : cssVar("--ink-3"))) },
          _lightColors: terms.map((p) => (p ? termLight(p) : "#85909A")),
          customdata: terms.map((p) => (p ? p.short : "")),
          hovertemplate: `%{x|${DF}}: <b>%{y:,.1f}${sfx}</b><extra>%{customdata}</extra>`,
        });
      } else {
        const stats = new Map(termStats(ind.dates, main.y, state.transform === "level", ind.measure, ind.frequency).map((s) => [s.p.id, s]));
        const visible = new Set(x);
        // Faint dashed original series behind a seasonally adjusted / trend view, as context.
        const bg = state.variant !== "original" && ind.variants.original ? series("original") : null;
        const segments = (yy, faint) => {
          for (const p of PRESIDENCIES) {
            const w = termWindow(p, ind.frequency);
            const ii = idx.filter((i) => ind.dates[i] >= w.first && (!w.last || ind.dates[i] <= w.last) && yy[i] !== null);
            if (!ii.length) continue;
            const color = termColor(p);
            const st = stats.get(p.id);
            const stat = st ? (t.change ? `avg ${fmtNum(st.avg, t)}` : `max ${fmtNum(st.max, t)}`) : "";
            const common = { type: "scatter", mode: "lines", _light: termLight(p), legendgroup: p.id };
            if (faint) {
              traces.push({ ...common, x: ii.map((i) => ind.dates[i]), y: ii.map((i) => yy[i]), line: { color, width: 1.2, dash: "dash" }, opacity: 0.35, showlegend: false, hoverinfo: "skip", name: `${p.short} (original)` });
              continue;
            }
            // Connector from the base month so consecutive terms join up (not hoverable).
            const bi = ind.dates.indexOf(w.base);
            if (bi >= 0 && visible.has(w.base) && yy[bi] !== null) {
              traces.push({ ...common, x: [w.base, ind.dates[ii[0]]], y: [yy[bi], yy[ii[0]]], line: { color, width: 2 }, showlegend: false, hoverinfo: "skip" });
            }
            traces.push({
              ...common, x: ii.map((i) => ind.dates[i]), y: ii.map((i) => yy[i]),
              name: `${p.short}${stat ? ` (${stat})` : ""}`, line: { color, width: 2 },
              hovertemplate: `%{x|${DF}}: <b>%{y:,.1f}${sfx}</b><extra>${p.short}</extra>`,
            });
          }
        };
        if (bg) segments(bg.y, true);
        segments(main.y, false);

        // Term boundaries and labels.
        for (const p of PRESIDENCIES) {
          const w = termWindow(p, ind.frequency);
          if (x.length && w.base > x[0] && w.base <= x[x.length - 1]) {
            shapes.push({ type: "line", xref: "x", yref: "paper", x0: w.base, x1: w.base, y0: 0, y1: 1, line: { color: cssVar("--rule"), width: 1, dash: "dot", _light: "#C9CED3" } });
          }
        }
        // Latest value reference line (levels only).
        if (state.transform === "level" && y.length) {
          const lastV = y[y.length - 1];
          shapes.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: lastV, y1: lastV, line: { color: cssVar("--ink-3"), width: 1, dash: "dash", _light: "#85909A" } });
          annotations.push({ xref: "paper", yref: "y", x: 0.005, y: lastV, xanchor: "left", yanchor: "bottom", showarrow: false, text: `Latest: ${fmtNum(lastV, t)}`, font: { size: 12, color: cssVar("--ink-2") } });
        }
      }
      if (main.rebased && state.transform === "level") {
        shapes.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: cssVar("--ink-2"), width: 1, _light: "#5A6872" } });
      }
      return { traces, shapes, annotations, main, t, x, y };
    }

    function drawStats(main, t) {
      if (!state.byPres) { statsEl.hidden = true; return; }
      const isLevel = state.transform === "level";
      const rows = termStats(ind.dates, main.y, isLevel, ind.measure, ind.frequency);
      const f = (v) => fmtNum(v, t);
      const fc = (v) => fmtNum(v, isRate ? RATE_TRANSFORMS.yoy : INDEX_TRANSFORMS.yoy);
      const head = isLevel
        ? ["Presidency", "Period", "Start", "Latest / end", "Change", "Average", "Min", "Max"]
        : ["Presidency", "Period", "Average", "Min", "Max", "Latest / end"];
      const tbody = h("tbody", {}, rows.slice().reverse().map((r) => {
        const sw = h("span", { class: "sw" }); sw.style.background = termColor(r.p);
        const period = `${fP(r.first)} – ${r.ongoing ? "present" : fP(r.last)}${r.partial ? "*" : ""}`;
        const mm = (v, at) => h("span", {}, f(v), h("span", { class: "at" }, ` ${fP(at)}`));
        const cells = isLevel
          ? [r.start === null ? "–" : f(r.start), f(r.end), r.change === null ? "–" : fc(r.change), f(r.avg), mm(r.min, r.minAt), mm(r.max, r.maxAt)]
          : [f(r.avg), mm(r.min, r.minAt), mm(r.max, r.maxAt), f(r.end)];
        return h("tr", {}, h("td", { class: "pres" }, sw, r.p.short), h("td", {}, period), ...cells.map((c) => h("td", {}, c)));
      }));
      const note = [
        `Statistics use each full term regardless of the time range shown. ${isLevel ? `Start is the base ${ind.frequency === "Q" ? "quarter" : "month"} (the last one before the term); change is latest/end vs. start.` : ""}`,
        rows.some((r) => r.partial) ? " *Series begins after the term started." : "",
      ].join("");
      statsEl.replaceChildren(
        h("div", { class: "table-scroll" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))), tbody)),
        h("p", { class: "chips-note" }, note));
      statsEl.hidden = false;
    }

    function draw() {
      vSeg && vSeg.update(state.variant);
      tSeg.update(state.transform, { mom: state.variant === "original" });
      rSeg.update(state.range);
      presBtn && presBtn.setAttribute("aria-pressed", String(state.byPres));
      baseSel.disabled = state.transform !== "level";
      customIn.disabled = state.transform !== "level";

      const { traces, shapes, annotations, main, t, x, y } = build();
      const msgs = [];
      if (isRate && state.transform !== "level") msgs.push("Changes in a rate are shown in percentage points.");
      if (state.variant === "original" && T.mom) msgs.push(`${T.mom.label.split(" ")[0]} changes are only meaningful on seasonally adjusted data.`);
      if (state.transform !== "level" && state.base !== "published") msgs.push("Rebasing applies to levels only; percent changes are unaffected.");
      if (state.transform === "level" && state.base !== "published" && !main.rebased) msgs.push("No data for that base month; showing the series as published.");
      hint.textContent = msgs.join(" ");

      Plotly.react(chartEl, traces, baseLayout({
        pct: t,
        extra: {
          bargap: 0.2, shapes, annotations,
          showlegend: state.byPres && state.transform !== "mom",
          legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } },
          margin: { l: 52, r: 16, t: state.byPres ? 30 : 10, b: 36 },
        },
      }), PLOT_CONFIG);
      drawStats(main, t);
      table && table.refresh(x, [{ label: `${main.v.label}${t.change ? " (" + t.label + ")" : main.rebased ? ` (${main.units})` : ""}`, values: y }], t);
    }

    const subtitle = (main, t) => `${main.v.label} · ${t.change ? t.short : main.units}`;
    const fileTag = () => `${ind.id}_${state.variant}_${state.transform}${baseMonth() ? "_base" + baseMonth().slice(0, 7) : ""}${state.byPres ? "_presidencies" : ""}`;
    const onPNG = () => {
      const { traces, shapes, annotations, main, t } = build();
      exportPNG(ind, traces, subtitle(main, t), t, `${fileTag()}.png`, { shapes, annotations });
    };
    const onCSV = () => {
      const { main, t, x, y } = build();
      const header = ["date", `${ind.short_title} — ${main.v.label} — ${t.change ? t.short : main.units}`];
      if (state.byPres) header.push("presidency");
      const rows = x.map((d, i) => {
        const r = [d, y[i] === null ? "" : +y[i].toFixed(4)];
        if (state.byPres) { const p = termOf(d, ind.frequency); r.push(p ? p.name : ""); }
        return r;
      });
      downloadBlob(toCSV(header, rows), `${fileTag()}.csv`, "text/csv");
    };

    appendAll(card, 
      h("div", { class: "controls" }, vSeg, tSeg, h("span", { class: "spacer" }), rSeg),
      h("div", { class: "controls" }, baseWrap, presBtn),
      hint, chartEl, statsEl);
    table = tableView([], [], false, ind.frequency);
    appendAll(card, table, ind.note ? h("p", { class: "chips-note" }, ind.note) : null, footer(ind, onPNG, onCSV));
    card.draw = draw;
    return card;
  }

  // ---------- card: panel of components (EMAE by sector, UCII by block) ----------
  // Breaks the bar axis when one value dwarfs the rest (e.g. fishing +424%),
  // so the others stay readable. Returns the axis cap, or null if no break.
  function axisBreak(vals) {
    const abs = vals.filter((v) => v !== null).map(Math.abs).sort((a, b) => b - a);
    if (abs.length < 3 || abs[1] === 0 || abs[0] < 2.5 * abs[1]) return null;
    return abs[1] * 1.35;
  }

  function panelCard(ind) {
    const keys = Object.keys(ind.variants);
    const T = tfs(ind);
    const isRate = ind.measure === "rate";
    // What the ranked bars show: level (rates), y/y, or incidence (y/y
    // contribution to the total in pp, when fixed-base sector weights exist).
    const W = ind.weights && ind.weights.values;
    const INC = { label: "Incidence (pp)", short: "Contribution to total y/y change, percentage points", suffix: " pp", signed: true, change: true };
    const barState = { kind: isRate ? "level" : "yoy" };
    let barKind = barState.kind, barSpec = T[barKind];
    const defaults = [].concat(ind.default.variant).slice(0, MAX_PANEL_SERIES);
    const slots = new Map(defaults.map((k, i) => [k, i]));   // color follows the component, not its rank
    const state = { transform: ind.default.transform || barKind, range: rangeFromDefault(ind) };
    const noun = ind.component_noun || "series";

    const card = cardFrame(ind, true);
    const barEl = h("div", { class: "chart tall", role: "img", "aria-label": `Latest ${barSpec.short || "level"} by ${noun}` });
    const lineEl = h("div", { class: "chart", role: "img", "aria-label": `Selected ${noun} over time` });
    const chipsEl = h("div", { class: "chips", role: "group", "aria-label": `${noun} to compare` });
    const chipsNote = h("span", { class: "chips-note" });
    const lineKinds = Object.keys(T).filter((k) => k !== "mom");
    const tSeg = segmented(lineKinds.map((k) => [k, T[k].label]), state.transform, (k) => { state.transform = k; drawLines(); }, "Transformation");
    const rSeg = segmented(Object.keys(RANGES).map((k) => [k, k]), state.range, (k) => { state.range = k; drawLines(); }, "Time range");
    let table;

    // Bar metric for every component, at the most recent month they all share.
    const yoyMetric = Object.fromEntries(keys.map((k) => [k, transform(ind.dates, ind.variants[k].values, isRate ? "level" : "yoy", ind.measure, ind.frequency)]));
    let refIdx = ind.dates.length - 1;
    while (refIdx > 0 && keys.some((k) => yoyMetric[k][refIdx] === null)) refIdx--;
    const refDate = ind.dates[refIdx];
    // Incidence_k,t = w_k (X_k,t - X_k,t-12) / sum_j w_j X_j,t-12 * 100
    const incMetric = W ? (() => {
      const lag = ind.frequency === "Q" ? 4 : 12;
      const out = Object.fromEntries(keys.map((k) => [k, ind.dates.map(() => null)]));
      for (let i = lag; i < ind.dates.length; i++) {
        let den = 0, ok = true;
        for (const k of keys) { const p = ind.variants[k].values[i - lag]; if (W[k] === undefined) continue; if (p === null) { ok = false; break; } den += W[k] * p; }
        if (!ok || !den) continue;
        for (const k of keys) {
          const a = ind.variants[k].values[i], p = ind.variants[k].values[i - lag];
          if (W[k] !== undefined && a !== null && p !== null) out[k][i] = (W[k] * (a - p)) / den * 100;
        }
      }
      return out;
    })() : null;
    let metric = yoyMetric;
    const totalInc = () => keys.reduce((a, k) => a + (incMetric[k][refIdx] || 0), 0);
    const barTitleFor = () => isRate ? `${ind.units}, ${fmtMonth(refDate)}`
      : barKind === "incidence" ? `Incidence (contribution to total y/y change), ${fmtMonth(refDate)}; total ${(totalInc() > 0 ? "+" : "") + totalInc().toFixed(2)} pp`
      : `Year-over-year change, ${fmtMonth(refDate)}`;
    let barTitle = barTitleFor();
    const barTitleEl = h("span", {}, `${barTitle}. `);
    const bSeg = W ? segmented([["yoy", "y/y %"], ["incidence", "Incidence (pp)"]], barKind, (k) => {
      barKind = k; barSpec = k === "incidence" ? INC : T.yoy; metric = k === "incidence" ? incMetric : yoyMetric;
      barTitle = barTitleFor(); barTitleEl.textContent = `${barTitle}. `; bSeg.update(k); drawBars();
    }, "Bar metric") : null;

    function freeSlot() { const used = new Set(slots.values()); for (let i = 0; i < MAX_PANEL_SERIES; i++) if (!used.has(i)) return i; return -1; }

    function toggle(k) {
      if (slots.has(k)) { if (slots.size > 1) slots.delete(k); }
      else { const s = freeSlot(); if (s >= 0) slots.set(k, s); }
      drawAll();
    }

    function bars(forExport) {
      const order = keys.slice().sort((a, b) => metric[a][refIdx] - metric[b][refIdx]);
      const actual = order.map((k) => metric[k][refIdx]);
      const cap = axisBreak(actual);
      const shown = actual.map((v) => (cap && Math.abs(v) > cap ? Math.sign(v) * cap : v));
      const clipped = actual.map((v) => !!cap && Math.abs(v) > cap);
      const muted = forExport ? "#C9CED3" : (isDark() ? "#4A5761" : "#C9CED3");
      const lightSeries = ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5"];
      const colors = order.map((k) => (slots.has(k) ? (forExport ? lightSeries[slots.get(k)] : cssVar(SERIES_VARS[slots.get(k)])) : muted));
      const inkColor = forExport ? "#36454F" : cssVar("--ink");
      const trace = {
        type: "bar", orientation: "h",
        y: order.map((k) => ind.variants[k].label),
        x: shown,
        marker: { color: colors },
        customdata: order.map((k, i) => [k, actual[i], W && W[k] !== undefined ? W[k] : null]),
        text: actual.map((v) => (barKind === "incidence" ? (v > 0 ? "+" : "") + v.toFixed(2) + " pp" : fmtNum(v, barSpec))),
        textposition: clipped.map((c) => (c ? "inside" : "outside")),
        insidetextanchor: "end",
        textfont: { family: cssVar("--font-ui") || "Calibri, Arial, sans-serif", size: forExport ? 15 : 12, color: clipped.map((c, i) => (c && slots.has(order[i]) ? "#FFFFFF" : inkColor)) },
        cliponaxis: false,
        hovertemplate: `%{y}: <b>%{customdata[1]:,${barKind === "incidence" ? ".2f" : ".1f"}}${barSpec.suffix}</b>${W ? `<br>Weight: %{customdata[2]:.1f}% of ${ind.weights.year} GDP` : ""}<extra></extra>`,
        _lightColors: colors,
      };
      // Room for outside labels; the broken bar ends at the axis edge.
      const lo = Math.min(0, ...shown), hi = Math.max(0, ...shown);
      const pad = (hi - lo) * 0.12;
      const range = [lo < 0 ? lo - pad : 0, hi > 0 ? (clipped.some((c, i) => c && shown[i] > 0) ? hi : hi + pad) : 0 + pad * 0.2];
      if (isRate) { range[0] = 0; range[1] = Math.min(100, hi + pad); }
      // Two slanted strokes in the background color mark where the axis is broken.
      const shapes = [];
      clipped.forEach((c, i) => {
        if (!c) return;
        const xm = shown[i] * 0.8, d = Math.abs(shown[i]) * 0.012, bg = forExport ? "#FFFFFF" : cssVar("--surface");
        for (const off of [-1.6 * d, 1.6 * d]) {
          shapes.push({ type: "line", xref: "x", yref: "y", x0: xm + off - d, x1: xm + off + d, y0: i - 0.42, y1: i + 0.42, line: { color: bg, width: 3, _light: "#FFFFFF" } });
        }
      });
      return { traces: [trace], shapes, range, cap };
    }

    function lineTraces() {
      const t = T[state.transform];
      const start = startDate(ind.last_obs, RANGES[state.range]);
      return [...slots.entries()].sort((a, b) => a[1] - b[1]).map(([k, slot]) => {
        const y = state.transform === "level" ? ind.variants[k].values : transform(ind.dates, ind.variants[k].values, state.transform, ind.measure, ind.frequency);
        const idx = ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && y[i] !== null);
        return {
          type: "scatter", mode: "lines", name: ind.variants[k].label,
          x: idx.map((i) => ind.dates[i]), y: idx.map((i) => y[i]),
          line: { color: cssVar(SERIES_VARS[slot]), width: 2 }, _slot: slot,
          hovertemplate: `%{y:,.1f}${t.suffix}<extra>${ind.variants[k].label}</extra>`,
        };
      });
    }

    function drawChips() {
      chipsEl.replaceChildren(...keys.map((k) => {
        const on = slots.has(k);
        const sw = h("span", { class: "sw" });
        if (on) sw.style.background = cssVar(SERIES_VARS[slots.get(k)]);
        return h("button", { class: "chip", type: "button", "aria-pressed": String(on), disabled: !on && slots.size >= MAX_PANEL_SERIES ? true : null, onclick: () => toggle(k) }, sw, ind.variants[k].label);
      }));
      chipsNote.textContent = `Compare up to ${MAX_PANEL_SERIES} ${noun}. Click a bar or a name to add or remove it.`;
    }

    let barNote;
    function drawBars() {
      const b = bars(false);
      Plotly.react(barEl, b.traces, baseLayout({
        xaxis: { type: "linear", ticksuffix: barSpec.suffix, zeroline: true, zerolinecolor: cssVar("--ink-2"), range: b.range },
        yaxis: { ticksuffix: "", automargin: true, gridcolor: "rgba(0,0,0,0)" },
        extra: { hovermode: "closest", margin: { l: 10, r: 16, t: 10, b: 36 }, bargap: 0.3, shapes: b.shapes, uniformtext: { minsize: 10, mode: "show" } },
      }), PLOT_CONFIG);
      barNote.textContent = b.cap ? ` Axis broken at ${fmtNum(b.cap, barSpec)}; the marked bar extends beyond it; its label shows the full value.` : "";
    }

    function drawLines() {
      tSeg.update(state.transform); rSeg.update(state.range);
      const t = T[state.transform];
      const tr = lineTraces();
      Plotly.react(lineEl, tr, baseLayout({ pct: t, extra: { showlegend: true, legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } }, margin: { l: 52, r: 16, t: 30, b: 36 } } }), PLOT_CONFIG);
      table && table.refresh(tr[0] ? tr[0].x : [], tr.map((s) => ({ label: s.name, values: s.y })), t);
    }

    function drawAll() { drawChips(); drawBars(); drawLines(); }

    const seriesNote = isRate ? "" : " · original series";
    const onPNG = () => {
      const t = T[state.transform];
      exportPNG(ind, lineTraces(), `${t.change ? t.short : ind.units}${seriesNote}`, t, `${ind.id}_${state.transform}.png`);
    };
    const onBarPNG = () => {
      const b = bars(true);
      exportPNG({ ...ind, title: `${ind.title}: ${barTitle.charAt(0).toLowerCase() + barTitle.slice(1)}` }, b.traces,
        isRate ? `Latest month, by ${noun}` : barKind === "incidence" ? `Percentage points; weights: shares of ${ind.weights.year} GDP${seriesNote}` : `Percent change vs. same month a year earlier${seriesNote}`, barSpec, `${ind.id}_latest_${barKind}.png`,
        { shapes: b.shapes, xrange: b.range, xsuffix: barSpec.suffix });
    };
    const onCSV = () => {
      const t = T[state.transform];
      const tr = lineTraces();
      const all = [...new Set(tr.flatMap((s) => s.x))].sort();
      const maps = tr.map((s) => new Map(s.x.map((d, i) => [d, s.y[i]])));
      downloadBlob(toCSV(["date", ...tr.map((s) => `${s.name}${t.change ? ` (${t.label})` : ""}`)], all.map((d) => [d, ...maps.map((m) => (m.has(d) ? +m.get(d).toFixed(4) : ""))])),
        `${ind.id}_${state.transform}.csv`, "text/csv");
    };

    barNote = h("span", {});
    const weightsTable = W ? h("details", { class: "table-view" }, h("summary", {}, `Sector weights (share of ${ind.weights.year} GDP at constant prices)`),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Sector"), h("th", {}, "Weight"))),
        h("tbody", {}, keys.filter((k) => W[k] !== undefined).sort((a, b) => W[b] - W[a]).map((k) => h("tr", {}, h("td", {}, ind.variants[k].label), h("td", {}, W[k].toFixed(1) + "%"))))))) : null;
    appendAll(card, 
      bSeg ? h("div", { class: "controls" }, bSeg) : null,
      h("p", { class: "hint" }, barTitleEl,
        h("button", { class: "btn", type: "button", onclick: onBarPNG, style: "padding:1px 8px;font-size:12px" }, "PNG"), barNote),
      barEl, weightsTable,
      h("div", { class: "controls", style: "margin-top:14px" }, tSeg, h("span", { class: "spacer" }), rSeg),
      chipsEl, chipsNote, lineEl);
    table = tableView([], [], T[state.transform], ind.frequency);
    appendAll(card, table, ind.note ? h("p", { class: "chips-note" }, ind.note) : null, footer(ind, onPNG, onCSV));
    card.draw = () => {
      drawAll();
      if (!barEl.dataset.bound) {
        barEl.on("plotly_click", (ev) => { const cd = ev.points && ev.points[0] && ev.points[0].customdata; if (cd) toggle(cd[0]); });
        barEl.dataset.bound = "1";
      }
    };
    return card;
  }

  // ---------- card: contributions to growth (GDP by expenditure) ----------
  const GROUP_VARS = { copper: "--series-1", sky: "--series-2", sage: "--series-3", lavender: "--series-4" };
  const GROUP_LIGHT = { copper: "#B87333", sky: "#5B9BD5", sage: "#87A96B", lavender: "#8E7AB5", neutral: "#B0B7BD" };
  const groupColor = (c) => (GROUP_VARS[c] ? cssVar(GROUP_VARS[c]) : (isDark() ? "#5C6A74" : "#B0B7BD"));

  function contributionsCard(ind) {
    const C = ind.contributions;
    const DF = ind.frequency === "Q" ? "Q%q %Y" : "%b %Y";
    const fP = (iso) => fmtPeriod(iso, ind.frequency);
    const pp = { suffix: " pp", signed: true };
    const pct = INDEX_TRANSFORMS.yoy;
    const gdpLabel = C.total.label;
    const state = { range: rangeFromDefault(ind) };
    const lineKeys = Object.keys(C.lines);
    // Lines keep the same color as their bar component (color follows the entity).
    const lc = (k) => C.lines[k].color || "neutral";
    const lineColor = (k) => (lc(k) === "neutral" ? cssVar("--ink-2") : groupColor(lc(k)));
    const lineLight = (k) => (lc(k) === "neutral" ? "#5A6872" : GROUP_LIGHT[lc(k)]);
    const shown = new Set(C.default_lines || lineKeys.slice(0, 3));

    const card = cardFrame(ind, true);
    const barEl = h("div", { class: "chart tall-ish", role: "img", "aria-label": "Contributions to GDP growth" });
    const lineEl = h("div", { class: "chart", role: "img", "aria-label": "Components, year-over-year change" });
    const chipsEl = h("div", { class: "chips", role: "group", "aria-label": "Components to compare" });
    const rSeg = segmented(Object.keys(RANGES).map((k) => [k, k]), state.range, (k) => { state.range = k; drawAll(); }, "Time range");
    let table;

    const visible = () => {
      const start = startDate(ind.last_obs, RANGES[state.range]);
      return C.dates.map((d, i) => i).filter((i) => !start || C.dates[i] >= start);
    };

    function barTraces() {
      const idx = visible(), x = idx.map((i) => C.dates[i]);
      const tr = C.groups.map((g) => ({
        type: "bar", name: g.label, x, y: idx.map((i) => g.values[i]),
        marker: { color: groupColor(g.color), line: { width: 0 } }, _lightColors: idx.map(() => GROUP_LIGHT[g.color] || "#B0B7BD"),
        hovertemplate: `${g.label}: <b>%{y:,.1f} pp</b><extra></extra>`,
      }));
      const ink = cssVar("--ink");
      tr.push({
        type: "scatter", mode: "markers+lines", name: `${C.total.label} growth`, x, y: idx.map((i) => C.total.values[i]),
        line: { color: ink, width: 1 }, marker: { color: ink, size: 7, symbol: "diamond", line: { color: cssVar("--surface"), width: 1 } },
        _light: "#36454F", hovertemplate: `<b>${C.total.label}: %{y:,.1f}%</b><extra></extra>`,
      });
      return tr;
    }

    function lineTraces() {
      const idx = visible();
      return lineKeys.filter((k) => shown.has(k)).map((k) => {
        const ln = C.lines[k];
        const ii = idx.filter((i) => ln.values[i] !== null);
        return {
          type: "scatter", mode: "lines", name: ln.label, x: ii.map((i) => C.dates[i]), y: ii.map((i) => ln.values[i]),
          line: { color: lineColor(k), width: 2, dash: lc(k) === "neutral" ? "dot" : undefined }, _light: lineLight(k),
          hovertemplate: `%{y:,.1f}%<extra>${ln.label}</extra>`,
        };
      });
    }

    function toggle(k) {
      if (shown.has(k)) { if (shown.size > 1) shown.delete(k); } else shown.add(k);
      drawAll();
    }

    function drawAll() {
      rSeg.update(state.range);
      const legend = { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } };
      Plotly.react(barEl, barTraces(), baseLayout({
        pct: pp, xaxis: { hoverformat: DF },
        extra: { barmode: "relative", bargap: 0.25, showlegend: true, legend, margin: { l: 56, r: 16, t: 40, b: 36 } },
      }), PLOT_CONFIG);
      chipsEl.replaceChildren(...lineKeys.map((k) => {
        const on = shown.has(k);
        const sw = h("span", { class: "sw" });
        if (on) sw.style.background = lineColor(k);
        return h("button", { class: "chip", type: "button", "aria-pressed": String(on), onclick: () => toggle(k) }, sw, C.lines[k].label);
      }));
      const lt = lineTraces();
      Plotly.react(lineEl, lt, baseLayout({ pct, xaxis: { hoverformat: DF }, extra: { showlegend: true, legend, margin: { l: 52, r: 16, t: 30, b: 36 } } }), PLOT_CONFIG);
      const idx = visible();
      table && table.refresh(idx.map((i) => C.dates[i]), [
        { label: `${C.total.label} (y/y %)`, values: idx.map((i) => C.total.values[i]) },
        ...C.groups.map((g) => ({ label: `${g.label} (pp)`, values: idx.map((i) => g.values[i]) })),
      ], pp);
    }

    const latestI = C.dates.length - 1;
    const onPNG = () => exportPNG(ind, barTraces(), `Contributions to year-over-year GDP growth, percentage points · latest: ${fP(C.dates[latestI])}`,
      pp, `${ind.id}_contributions.png`, { barmode: "relative" });
    const onLinePNG = () => exportPNG({ ...ind, title: C.lines_title || "Components, year-over-year change" }, lineTraces(), `Percent change vs. same ${ind.frequency === "Q" ? "quarter" : "month"} a year earlier · constant 2004 prices`, pct, `${ind.id}_lines_yoy.png`);
    const onCSV = () => {
      const header = ["date", `${C.total.label} y/y %`, ...C.groups.map((g) => `${g.label} contribution (pp)`), ...lineKeys.map((k) => `${C.lines[k].label} y/y %`)];
      const rows = C.dates.map((d, i) => [d, C.total.values[i], ...C.groups.map((g) => g.values[i]), ...lineKeys.map((k) => C.lines[k].values[i])]
        .map((v, j) => (j === 0 || v === null ? v : +v.toFixed(4))));
      downloadBlob(toCSV(header, rows), `${ind.id}_contributions.csv`, "text/csv");
    };

    // Latest-quarter summary line.
    const parts = C.groups.map((g) => `${g.label} ${fmtNum(g.values[latestI], pp)}`).join(" · ");
    appendAll(card, 
      h("p", { class: "hint" }, `${fP(C.dates[latestI])}: GDP ${fmtNum(C.total.values[latestI], pct)} y/y — ${parts}`),
      h("div", { class: "controls" }, h("span", { class: "spacer" }), rSeg),
      barEl,
      h("p", { class: "hint", style: "margin-top:14px" }, `${C.lines_title || "Components, year-over-year change"}. `,
        h("button", { class: "btn", type: "button", onclick: onLinePNG, style: "padding:1px 8px;font-size:12px" }, "PNG")),
      chipsEl, lineEl);
    table = tableView([], [], pp, ind.frequency);
    appendAll(card, table, ind.note ? h("p", { class: "chips-note" }, ind.note) : null, footer(ind, onPNG, onCSV));
    card.draw = drawAll;
    return card;
  }

  // ---------- headline tiles ----------
  function tile(ind) {
    const hl = ind.headline;
    const T = tfs(ind);
    const v = ind.variants[hl.variant];
    const series = transform(ind.dates, v.values, hl.transform, ind.measure, ind.frequency);
    const last = lastValid(ind.dates, series);
    if (!last) return null;
    const t = T[hl.transform] || INDEX_TRANSFORMS[hl.transform];
    const isRate = ind.measure === "rate";
    // Second line: y/y of the original series (indices) or of the same series in pp (rates).
    const yoySrc = isRate ? v : ind.variants.original;
    const yoySpec = isRate ? RATE_TRANSFORMS.yoy : INDEX_TRANSFORMS.yoy;
    const yoy = yoySrc ? lastValid(ind.dates, transform(ind.dates, yoySrc.values, "yoy", ind.measure, ind.frequency)) : null;
    const cls = t.change ? (last.value > 0 ? " pos" : last.value < 0 ? " neg" : "") : "";
    const what = isRate ? (t.change ? t.label : ind.units) : `${hl.transform === "mom" ? (ind.frequency === "Q" ? "q/q" : "m/m") : t.label}, ${v.label.toLowerCase()}`;
    return h("a", { class: "tile", href: `#ind-${ind.id}` },
      h("div", { class: "tile-label" }, ind.short_title),
      h("div", { class: "tile-value" + cls }, fmtNum(last.value, t)),
      h("div", { class: "tile-sub" }, `${fmtPeriod(last.date, ind.frequency)} · ${what}`),
      yoy ? h("div", { class: "tile-sub" }, isRate ? `${fmtNum(yoy.value, yoySpec)} vs. a year earlier` : `${fmtNum(yoy.value, yoySpec)} y/y (original series, ${fmtPeriod(yoy.date, ind.frequency)})`) : null);
  }

  // ---------- page ----------
  async function main() {
    const topicsEl = $("#topics");
    let manifest;
    try {
      manifest = await getJSON(DATA + "manifest.json");
    } catch (e) {
      topicsEl.replaceChildren(h("div", { class: "error-box" }, "Data could not be loaded. Please try again later."));
      console.error(e);
      return;
    }

    PRESIDENCIES = manifest.presidencies || [];
    PARTY_COLORS = manifest.party_colors || {};
    const site = manifest.site || {};
    if (site.publisher_url) {
      const sub = $("#subscribe"); sub.href = site.publisher_url; sub.hidden = false;
      $("#eo-link").href = site.publisher_url;
    }
    if (site.repo_url) $("#repo-link").href = site.repo_url;
    const built = new Date(manifest.built_at);
    $("#build-meta").textContent = `Last data check: ${built.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}`;

    const inds = await Promise.all(manifest.indicators.map((m) => getJSON(`${DATA}${m.id}.json`).catch((e) => { console.error(e); return null; })));
    const byTopic = new Map();
    for (const ind of inds.filter(Boolean)) {
      if (!byTopic.has(ind.topic)) byTopic.set(ind.topic, []);
      byTopic.get(ind.topic).push(ind);
    }

    const nav = $("#nav");
    const sections = [];
    for (const topic of manifest.topics) {
      const list = byTopic.get(topic.id);
      if (!list || !list.length) continue;
      nav.append(h("a", { href: `#topic-${topic.id}` }, topic.title));
      const cards = h("div", { class: "cards" });
      // Registry order; panels and cards flagged `wide` span the full row.
      list.forEach((ind) => cards.append(
        ind.kind === "panel" ? panelCard(ind)
          : ind.kind === "contributions" ? contributionsCard(ind)
          : variantsCard(ind, !!ind.wide)));
      sections.push(h("section", { class: "topic", id: `topic-${topic.id}` },
        h("h2", {}, topic.title), h("p", {}, topic.description || ""), cards));
    }
    topicsEl.replaceChildren(...sections);

    $("#tiles").replaceChildren(...inds.filter((i) => i && i.headline).map(tile).filter(Boolean));

    const drawAll = () => document.querySelectorAll(".card").forEach((c) => c.draw && c.draw());
    if (window.Plotly) drawAll();
    else topicsEl.prepend(h("div", { class: "error-box" }, "The charting library could not be loaded."));

    $("#theme-toggle").addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("aem-theme", next); } catch (e) {}
      drawAll();
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!document.documentElement.dataset.theme) drawAll(); });
  }

  document.addEventListener("DOMContentLoaded", main);
})();
