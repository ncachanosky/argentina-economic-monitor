/* Argentina Economic Monitor — front end
 *
 * Reads data/manifest.json and data/<indicator>.json (written by
 * pipeline/export.py) and renders one card per indicator. All
 * transformations (y/y, m/m) are computed here, in the browser.
 */
(function () {
  "use strict";

  // Each page is rendered from one template (pipeline/build.py): the body
  // carries the page id ("home" or a topic id) and the path back to the root.
  const ROOT = (document.body && document.body.dataset.root) || "";
  const PAGE = (document.body && document.body.dataset.page) || "home";
  // Spanish pages live under /es/ and read the same data; body data-data points at it.
  const DATA = (document.body && document.body.dataset.data) || ROOT + "data/";
  const LANG = document.documentElement.lang === "es" ? "es" : "en";
  const LOCALE = LANG === "es" ? "es-AR" : "en-US";
  const fx = (v, d) => (LANG === "es" ? Number(v).toFixed(d).replace(".", ",") : Number(v).toFixed(d));   // fixed decimals, local separator
  // Translations (Spanish pages): data/i18n/es.json, loaded in main(). `ui` maps English
  // interface strings to Spanish; `patterns` are [regex, replacement] for strings with values.
  let I18N = null;
  const tr = (s) => {
    if (!I18N || typeof s !== "string") return s;
    const k = s.trim();
    if (!k) return s;
    const hit = I18N.ui[k] !== undefined ? I18N.ui[k] : I18N.content[k];
    if (hit !== undefined) return s.replace(k, hit);
    for (const [re, out] of I18N._patterns) if (re.test(k)) return s.replace(k, k.replace(re, out));
    return s;
  };
  const MAX_PANEL_SERIES = 4;              // fixed palette has four distinguishable slots
  const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];
  const EXPORT = { width: 1200, height: 800 }; // EO figure standard

  // Transformations by measure. Indices change in percent; rates (e.g. capacity
  // utilization, already in %) change in percentage points.
  //   suffix: appended to values; signed: show +/-; change: is a change, not a level
  const INDEX_TRANSFORMS = {
    level: { label: "Level", short: null, suffix: "", signed: false, change: false },
    yoy:   { label: "y/y %", short: "Year-over-year change, %", suffix: "%", signed: true, change: true },
    mom:   { label: "m/m %", short: "Month-over-month change, %", suffix: "%", signed: true, change: true },
    ytd:   { label: "YTD %", short: "Year to date: change since December of the previous year, %", suffix: "%", signed: true, change: true },
    share: { label: "Composition", short: "Share of each component in the total, %", suffix: "%", signed: false, change: false },
    acc:   { label: "Accum. y/y %", short: "Accumulated: year to date vs. the same months a year earlier, %", suffix: "%", signed: true, change: true },
  };
  const RATE_TRANSFORMS = {
    level: { label: "Level", short: null, suffix: "%", signed: false, change: false },
    yoy:   { label: "y/y (pp)", short: "Year-over-year change, percentage points", suffix: " pp", signed: true, change: true },
    mom:   { label: "m/m (pp)", short: "Month-over-month change, percentage points", suffix: " pp", signed: true, change: true },
    share: { label: "Composition", short: "Share of each component in the total, %", suffix: "%", signed: false, change: false },
  };
  const TRANSFORMS = INDEX_TRANSFORMS;
  function tfs(ind) {
    const all = ind.measure === "rate" ? RATE_TRANSFORMS : INDEX_TRANSFORMS;
    const keys = ind.transforms && ind.transforms.length ? ind.transforms : ["level", "yoy", "mom"];
    const out = Object.fromEntries(keys.map((k) => [k, { ...all[k] }]));
    if (ind.frequency === "Q" && out.mom) {
      out.mom.label = out.mom.label.replace("m/m", "q/q");
      out.mom.short = out.mom.short.replace("Month-over-month", "Quarter-over-quarter");
    }
    return out;
  }
  const RANGES = { "2Y": 2, "5Y": 5, "10Y": 10, "25Y": 25, "50Y": 50, "Max": null };
  const rangeKeys = (ind) => (ind.ranges && ind.ranges.length ? ind.ranges : ["2Y", "5Y", "10Y", "Max"]);

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
  const fmtMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString(LOCALE, { month: "long", year: "numeric", timeZone: "UTC" });
  const fmtShortMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString(LOCALE, { month: "short", year: "numeric", timeZone: "UTC" });
  // Period label: "Jul 2026" for monthly data, "Q3 2026" for quarterly.
  const fmtDay = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString(LOCALE, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const fmtPeriod = (iso, freq) => {
    if (freq === "Y") return iso.length > 4 ? iso.slice(0, 4) : iso;
    if (freq === "S") return `${Number(iso.slice(5, 7)) < 7 ? (LANG === "es" ? "S1" : "H1") : (LANG === "es" ? "S2" : "H2")} ${iso.slice(0, 4)}`;
    if (freq === "D") return fmtDay(iso);
    if (freq !== "Q") return fmtShortMonth(iso);
    const [y, m] = iso.split("-").map(Number);
    return `${LANG === "es" ? "T" : "Q"}${Math.floor((m - 1) / 3) + 1} ${y}`;
  };
  const specOf = (p) => (p && typeof p === "object" ? p : p ? INDEX_TRANSFORMS.yoy : INDEX_TRANSFORMS.level);
  const fmtNum = (v, p) => {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    const s = specOf(p);
    return s.signed ? (v > 0 ? "+" : "") + (LANG === "es" ? v.toFixed(1).replace(".", ",") : v.toFixed(1)) + s.suffix
      : v.toLocaleString(LOCALE, { maximumFractionDigits: 1, minimumFractionDigits: 1 }) + s.suffix;
  };
  // Category axis of years: label every k-th year when there are many, unrotated.
  const yearAxis = (n) => ({ type: "category", tickangle: 0, ...(n > 16 ? { tickmode: "linear", tick0: 0, dtick: Math.ceil(n / 12) } : {}) });
  // Daily lines: a gap of more than a month in the data breaks the line instead of bridging it.
  function breakGaps(x, y, days = 31) {
    const X = [], Y = [];
    x.forEach((d, i) => {
      if (X.length && Date.parse(d) - Date.parse(X[X.length - 1]) > days * 864e5) { X.push(d); Y.push(null); }
      X.push(d); Y.push(y[i]);
    });
    return [X, Y];
  }
  const monthIndex = (iso) => { const [y, m] = iso.split("-").map(Number); return y * 12 + (m - 1); };

  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  // ---------- transformations ----------
  // Months back to the comparison point: a year is 12 months for both monthly
  // and quarterly dates; one period is 1 month, or 3 for quarters; year to date
  // compares with December of the previous year (so December is Dec/Dec).
  function lagOf(kind, iso, freq) {
    if (kind === "yoy") return 12;
    if (kind === "ytd") return Number(iso.slice(5, 7));
    if (kind === "acc") return 12;
    return freq === "Q" ? 3 : 1;
  }
  // Running sum of the months of each calendar year so far (null if a month is
  // missing). INDEC's "accumulated" change is the y/y change of this sum.
  function yearToDateSum(dates, values) {
    let year = null, acc = 0, ok = true;
    return values.map((v, i) => {
      const y = dates[i].slice(0, 4);
      if (y !== year) { year = y; acc = 0; ok = dates[i].slice(5, 7) === "01"; }
      if (v === null) ok = false;
      acc += v || 0;
      return ok ? acc : null;
    });
  }
  // Daily data: compare with the last observation on or before the same day a
  // month or a year earlier (or the last day of the previous year, for YTD),
  // if one exists within a week of it.
  const dayMs = 864e5;
  const isoMs = (iso) => Date.parse(iso + "T00:00:00Z");
  function shiftedTarget(iso, kind) {
    const [y, m, d] = iso.split("-").map(Number);
    if (kind === "ytd") return Date.UTC(y - 1, 11, 31);
    const ty = kind === "yoy" ? y - 1 : (m === 1 ? y - 1 : y);
    const tm = kind === "yoy" ? m - 1 : (m === 1 ? 11 : m - 2);
    const dim = new Date(Date.UTC(ty, tm + 1, 0)).getUTCDate();
    return Date.UTC(ty, tm, Math.min(d, dim));
  }
  function lastOnOrBefore(ms, target) {
    let lo = 0, hi = ms.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (ms[mid] <= target) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  function transformDaily(dates, values, kind, measure) {
    const ms = dates.map(isoMs);
    const diff = measure === "rate";
    return values.map((v, i) => {
      if (v === null) return null;
      const target = shiftedTarget(dates[i], kind);
      let j = lastOnOrBefore(ms, target);
      while (j >= 0 && values[j] === null) j--;
      if (j < 0 || target - ms[j] > 7 * dayMs) return null;
      const prev = values[j];
      if (diff) return v - prev;
      return prev === 0 ? null : (v / prev - 1) * 100;
    });
  }
  function transform(dates, values, kind, measure, freq) {
    if (kind === "level" || kind === "share") return values.slice();
    if (freq === "D") return transformDaily(dates, values, kind, measure);
    if (kind === "acc") return transform(dates, yearToDateSum(dates, values), "yoy", measure, freq);
    const diff = measure === "rate";
    const mi = dates.map(monthIndex);
    const pos = new Map(mi.map((m, i) => [m, i]));
    return values.map((v, i) => {
      const j = pos.get(mi[i] - lagOf(kind, dates[i], freq));
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

  // First month to show: the card's fixed start, or the range back from the latest.
  function viewStart(ind, rangeKey) {
    if (ind.view_start) return ind.view_start;
    const since = /^Since (\d{4})$/.exec(rangeKey || "");
    if (since) return `${since[1]}-01-01`;
    return startDate(ind.last_obs, RANGES[rangeKey]);
  }
  function startDate(lastIso, years) {
    if (!years) return null;
    const [y, m] = lastIso.split("-").map(Number);
    return `${y - years}-${String(m).padStart(2, "0")}-01`;
  }

  function rangeFromDefault(ind) {
    if (ind.default && ind.default.range) return ind.default.range;
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
  // A color with transparency, from "#rrggbb" or "rgb(r, g, b)".
  function alpha(color, a) {
    const c = String(color).trim();
    let m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(c);
    if (m) return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})`;
    m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/i.exec(c);
    return m ? `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${a})` : c;
  }
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
    const lightSeries = ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5", "#C9A227", "#C96B7E", "#3E9C9A", "#6C7A89"];
    const font = '"Calibri", "Carlito", "Segoe UI", Arial, sans-serif';
    const data = traces.map((t, i) => {
      const c = { ...t };
      const color = t._light || (t._slot !== undefined ? lightSeries[t._slot] : lightSeries[0]);
      if (c.type === "bar") {
        const mc = t._lightColors || (Array.isArray(c.marker && c.marker.color) ? c.marker.color.map((x) => x === "__muted__" ? "#C9CED3" : x) : color);
        c.marker = { ...(c.marker || {}), color: mc };
      }
      if (c.line) c.line = { ...c.line, color, width: c.line.width === 0 ? 0 : c.line.dash ? 1.5 : 2.5 };
      if (t._lightFill) c.fillcolor = t._lightFill;
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
      yaxis: { gridcolor: light.grid, linecolor: light.rule, zerolinecolor: light.ink2, ticksuffix: specOf(pct).suffix, automargin: true, ...(extras.ylog ? { type: "log", ticksuffix: "" } : {}) },
      shapes: (extras.shapes || []).map((sh) => ({ ...sh, ...(sh._lightFill ? { fillcolor: sh._lightFill } : {}), line: { ...(sh.line || {}), color: (sh.line && sh.line._light) || light.ink3 } })),
      annotations: [
        ...(extras.annotations || []).map((a) => ({ ...a, font: { ...(a.font || {}), size: 15, color: light.ink2 } })),
        { text: `Source: ${ind.source_label}. Latest observation: ${ind.frequency === "Q" ? fmtPeriod(ind.last_obs, "Q") : ind.frequency === "Y" ? ind.last_obs.slice(0, 4) : fmtMonth(ind.last_obs)}.${ind.source_units ? ` Source data in ${ind.source_units}.` : ""}`, xref: "paper", yref: "paper", x: 0, y: -0.13, xanchor: "left", yanchor: "top", showarrow: false, font: { size: 15, color: light.ink3 } },
        { text: "<b>Argentina Economic Monitor</b> · Economic Order", xref: "paper", yref: "paper", x: 1, y: -0.13, xanchor: "right", yanchor: "top", showarrow: false, font: { size: 15, color: "#B87333" } },
      ],
      bargap: 0.25,
    };
    if (extras.uniformtext) layout.uniformtext = extras.uniformtext;
    if (extras.barmode) layout.barmode = extras.barmode;
    if (extras.xcategory) layout.xaxis.type = "category";
    if (extras.xaxis) Object.assign(layout.xaxis, extras.xaxis);
    if (extras.yaxis2) {
      // Right axis: keep the subtitle clear by moving the (long) legend under the plot.
      layout.yaxis2 = extras.yaxis2; layout.margin.r = 110; layout.margin.b = 200;
      layout.legend = { ...layout.legend, y: -0.09, yanchor: "top" };
      layout.annotations.slice(-2).forEach((a) => { a.y = -0.36; });
    }
    if (extras.margin) Object.assign(layout.margin, extras.margin);
    if (extras.sourceY !== undefined) layout.annotations.slice(-2).forEach((a) => { a.y = extras.sourceY; });
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
  const isoAddDays = (iso, k) => new Date(isoMs(iso) + k * dayMs).toISOString().slice(0, 10);
  function termWindow(p, freq) {
    // Daily data: the inauguration day starts the term; the day before is its base.
    if (freq === "D") return { first: p.start, base: isoAddDays(p.start, -1), last: p.end ? isoAddDays(p.end, -1) : null };
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
  // The average of a compounding rate (m/m, q/q) is geometric:
  // (prod(1 + r/100))^(1/n) - 1, the constant rate giving the same cumulative change.
  const isGeometric = (kind, measure) => kind === "mom" && measure !== "rate";
  // For y/y views of an index, the term's "average" is its annualized change:
  // (level_end / level_base)^(12 / months) - 1, which needs the levels.
  const isAnnualized = (kind, measure) => kind === "yoy" && measure !== "rate";
  function termStats(dates, y, isLevel, measure, freq, kind, levels) {
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
      // Base: the base period, or the last observation before the term (daily data).
      let bi = idx.get(w.base);
      if (bi === undefined) { const j = dates.findIndex((d) => d >= w.first) - 1; bi = j >= 0 ? j : undefined; }
      const start = isLevel && bi !== undefined ? y[bi] : null;
      const end = vals[vals.length - 1];
      out.push({
        p, first: pts[0][0], last: pts[pts.length - 1][0], n: pts.length,
        partial: freq === "D" ? isoMs(pts[0][0]) - isoMs(w.first) > 7 * dayMs : pts[0][0] > w.first, ongoing: !w.last,
        start, end, change: start === null || start === undefined ? null : measure === "rate" ? end - start : start ? (end / start - 1) * 100 : null,
        avg: isAnnualized(kind, measure) && levels
          ? (() => {
              // Series starting mid-term: annualize from its first level in the term.
              let b = idx.get(w.base);
              if (b === undefined && freq === "D") { const j = dates.findIndex((d) => d >= w.first) - 1; b = j >= 0 && levels[j] ? j : undefined; }
              if (b === undefined || !levels[b]) b = dates.findIndex((d, i) => d >= w.base && levels[i]);
              const e = idx.get(pts[pts.length - 1][0]);
              const months = b < 0 ? 0 : (isoMs(pts[pts.length - 1][0]) - isoMs(dates[b])) / (30.4375 * dayMs);
              return b === undefined || !levels[b] || levels[e] === null || months <= 0 ? null
                : (Math.pow(levels[e] / levels[b], 12 / months) - 1) * 100;
            })()
          : isGeometric(kind, measure)
          ? (Math.exp(vals.reduce((a, b) => a + Math.log(1 + b / 100), 0) / vals.length) - 1) * 100
          : vals.reduce((a, b) => a + b, 0) / vals.length,
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
    const day = new Date(nr.date + "T12:00:00Z").toLocaleDateString(LOCALE, { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
    const period = fmtPeriod(nr.period + "-01", nr.frequency || ind.frequency);
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
        h("a", { class: "btn", href: `${DATA}${ind.id}${LANG === "es" ? ".es" : ""}.csv`, download: `${ind.id}_all.csv`, title: "All variants, levels, as published" }, "All data")));
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
        h("thead", {}, h("tr", {}, h("th", {}, freq === "Q" ? "Quarter" : freq === "Y" ? "Year" : "Month"), ...cols.map((c) => h("th", {}, c.label)))), tbody)));
      details.dataset.filled = "1";
    };
    details.addEventListener("toggle", fill);
    details.refresh = (d, c, p, f) => { dates = d; cols = c; pct = p; if (f) freq = f; details.querySelector(".table-scroll")?.remove(); delete details.dataset.filled; if (details.open) fill(); };
    return details;
  }

  // Yearly summary for monthly activity indices: the year-to-date change vs.
  // the same months a year earlier (average of Jan..m over average of the same
  // months of the previous year, original series, as INDEC reports it), and the
  // change within the year (December, or the latest month, vs. the previous
  // December, seasonally adjusted).
  function annualTable(ind) {
    const at = ind.annual_table;
    if (!at) return null;
    const Q = ind.frequency === "Q";
    const per = Q ? [1, 4, 7, 10] : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    const last = per[per.length - 1];
    const acc = ind.variants[at.accumulated], wy = ind.variants[at.within_year];
    const pos = new Map(ind.dates.map((d, i) => [d, i]));
    const val = (v, y, m) => { const i = pos.get(`${y}-${String(m).padStart(2, "0")}-01`); return i === undefined ? null : v.values[i]; };
    const pName = (m) => (Q ? `${LANG === "es" ? "T" : "Q"}${(m - 1) / 3 + 1}` : new Date(Date.UTC(2000, m - 1, 1)).toLocaleDateString(LOCALE, { month: "short", timeZone: "UTC" }));
    const sumYear = (v, y, upto) => { let s = 0; for (const m of per) { if (m > upto) break; const x = val(v, y, m); if (x === null) return null; s += x; } return s; };
    const years = [...new Set(ind.dates.map((d) => +d.slice(0, 4)))].sort((a, b) => b - a);
    const f = (v) => (v === null ? "–" : fmtNum(v, INDEX_TRANSFORMS.yoy));
    const rows = [];
    for (const y of years) {
      const have = per.filter((m) => val(acc, y, m) !== null);
      if (!have.length || have[0] !== per[0]) continue;
      const m = have[have.length - 1];
      const cur = sumYear(acc, y, m), prev = sumYear(acc, y - 1, m);
      const accum = cur !== null && prev ? (cur / prev - 1) * 100 : null;
      const haveW = per.filter((mm) => val(wy, y, mm) !== null);
      const mw = haveW.length ? haveW[haveW.length - 1] : null;
      const d0 = val(wy, y - 1, last), d1 = mw ? val(wy, y, mw) : null;
      const within = d0 && d1 !== null ? (d1 / d0 - 1) * 100 : null;
      // Carryover (seasonally adjusted): growth next year if activity stays at
      // this year's last level. For a year still in progress: the growth this
      // year would show if the remaining periods stayed at the latest level.
      let carry = null, carryNote = null;
      if (at.carryover) {
        const n = per.length;
        if (mw === last) {
          const s = sumYear(wy, y, last);
          carry = s ? (val(wy, y, last) / (s / n) - 1) * 100 : null;
        } else if (mw) {
          const s = sumYear(wy, y, mw), sp = sumYear(wy, y - 1, last), k = per.indexOf(mw) + 1;
          carry = s !== null && sp ? ((s + val(wy, y, mw) * (n - k)) / sp - 1) * 100 : null;
          carryNote = "this year, if flat";
        }
      }
      if (accum === null && within === null) continue;
      const cells = [h("td", {}, String(y)),
        h("td", {}, f(accum), m !== last ? h("span", { class: "at" }, ` ${pName(per[0])}–${pName(m)}`) : null),
        h("td", {}, f(within), mw && mw !== last ? h("span", { class: "at" }, ` to ${pName(mw)}`) : null)];
      if (at.carryover) cells.push(h("td", {}, f(carry), carryNote ? h("span", { class: "at" }, ` ${carryNote}`) : null));
      rows.push(h("tr", {}, cells));
    }
    if (!rows.length) return null;
    const lastName = Q ? "Q4/Q4" : "Dec/Dec";
    const head = [h("th", {}, "Year"),
      h("th", {}, `${Q ? "Annual growth" : "Accumulated y/y"} (${acc.label.toLowerCase()})`),
      h("th", {}, `Within year, ${lastName} (${wy.label.toLowerCase()})`)];
    if (at.carryover) head.push(h("th", {}, `Carryover (${wy.label.toLowerCase()})`));
    const unit = Q ? "quarters" : "months";
    return h("details", { class: "table-view" }, h("summary", {}, "Show yearly change"),
      h("div", { class: "table-scroll" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, head)), h("tbody", {}, rows))),
      h("p", { class: "chips-note" },
        `${Q ? "Annual growth" : "Accumulated y/y"}: the ${unit} published so far in the year over the same ${unit} a year earlier (${acc.label.toLowerCase()} series), as INDEC reports it. `,
        `Within year: the year's last ${Q ? "quarter" : "month"} vs. the previous ${Q ? "Q4" : "December"} (${wy.label.toLowerCase()}).`,
        at.carryover ? ` Carryover (statistical carry-over): next year's growth if the level stayed at the year's last ${Q ? "quarter" : "month"}; for the current year, this year's growth if the remaining ${unit} stayed at the latest level.` : ""));
  }

  // Old vs. new basket weights for a reweighted index.
  function weightsTable(ind) {
    const w = ind.derived && ind.derived.weights;
    if (!w) return null;
    const keys = Object.keys(w.labels).sort((a, b) => w.new[b] - w.new[a]);
    const f = (v) => fx(v, 1) + "%";
    const tbody = h("tbody", {}, keys.map((k) => {
      const d = Math.round((w.new[k] - w.old_at_link[k]) * 10) / 10 || 0;
      return h("tr", {}, h("td", { class: "pres" }, w.labels[k]), h("td", {}, f(w.old_at_link[k])), h("td", {}, f(w.new[k])), h("td", {}, (d > 0 ? "+" : "") + d.toFixed(1) + " pp"));
    }));
    return h("details", { class: "table-view" }, h("summary", {}, "Show basket weights"),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Division"), h("th", {}, `2004/05 basket, at ${fmtShortMonth(ind.derived.link)} prices`), h("th", {}, "2017/18 basket"), h("th", {}, "Change"))), tbody)),
      h("p", { class: "chips-note" }, `2004/05 weights recovered from the published indices (they reproduce the official index within ${ind.derived.replication_max_error_pct.toFixed(2)}% since ${fmtShortMonth(ind.derived.link)}).`));
  }

  // Net reserves: how each convention is built at the latest balance, line by
  // line, one column per convention ("–" where a convention does not deduct it).
  function breakdownTable(ind) {
    const b = ind.derived && ind.derived.breakdown;
    if (!b) return null;
    const f = (v) => (v === null || v === undefined ? "–" : (v < 0 ? "−" : "") + Math.abs(Math.round(v)).toLocaleString(LOCALE));
    const neg = (v) => (v === null || v === undefined ? "–" : Math.round(v) === 0 ? "0" : f(-v));
    const head = h("tr", {}, h("th", {}, "USD millions"), b.columns.map((c) => h("th", {}, c.label)));
    const body = b.rows.map((r) => h("tr", {}, h("td", { class: "pres" }, r.sign < 0 ? `less: ${r.label}` : r.label),
      b.columns.map((c) => h("td", {}, r.sign < 0 ? neg(r.values[c.key]) : f(r.values[c.key])))));
    const total = h("tr", { class: "total" }, h("td", { class: "pres" }, h("b", {}, "Net reserves")),
      b.columns.map((c) => h("td", {}, h("b", {}, f(b.total[c.key])))));
    const notes = [`BCRA weekly balance of ${fmtDay(b.date)}, at that balance's exchange rates.`];
    if (b.swap_cny_bn && b.cny_usd) notes.push(` China swap: CNY ${fx(b.swap_cny_bn, 0)} bn at ${fx((1 / b.cny_usd), 2)} yuan per dollar.`);
    if (b.treasury_as_of) notes.push(` Treasury deposits: monthly balance sheet, end of ${fmtMonth(b.treasury_as_of.slice(0, 7) + "-01")}.`);
    if (b.notes && b.notes.imf_repos) notes.push(` ${b.notes.imf_repos}`);
    return h("div", { class: "breakdown" },
      h("h3", { class: "breakdown-title" }, `How each estimate is built (${fmtDay(b.date)})`),
      h("div", { class: "table-scroll" }, h("table", { class: "data" }, h("thead", {}, head), h("tbody", {}, body, total))),
      h("p", { class: "chips-note" }, notes));
  }

  // Nominal / real switch for peso amounts: real = nominal / CPI x CPI of the
  // latest month with data (pesos of that month). Swaps the values in place.
  function realSwitch(ind, onChange) {
    const D = ind.deflator;
    if (!D || !D.latest_value) return null;
    const nominal = Object.fromEntries(Object.entries(ind.variants).map(([k, v]) => [k, v.values]));
    const real = Object.fromEntries(Object.entries(nominal).map(([k, vals]) =>
      [k, vals.map((x, i) => (x === null || !D.values[i] ? null : (x / D.values[i]) * D.latest_value))]));
    ind._realUnits = `${ind.units}, ${fmtShortMonth(D.latest)} prices`;
    const apply = (k) => { for (const kk of Object.keys(ind.variants)) ind.variants[kk].values = k === "real" ? real[kk] : nominal[kk]; ind._real = k === "real"; };
    const start = ind.default && ind.default.real ? "real" : "nominal";
    if (start === "real") apply("real");
    const seg = segmented([["nominal", "Nominal"], ["real", "Real"]], start, (k) => { apply(k); seg.update(k); onChange(); }, "Nominal or real terms");
    seg.title = `Real: deflated by the ${D.label}, in pesos of ${fmtMonth(D.latest)}`;
    return seg;
  }
  const unitsOf = (ind) => (ind._real ? ind._realUnits : ind.units);
  // The latest month of a monthly average built from daily data may be partial.
  const partialNote = (ind) => (ind.derived && ind.derived.partial_month
    ? `${fmtMonth(ind.derived.partial_month.slice(0, 7) + "-01")} is partial: data through ${fmtDay(ind.derived.partial_month)}.` : "");

  // ---------- yearly view of monthly data ----------
  // how = "december": the December value of each year (for twelve-month sums and
  // ratios, the calendar year); "sum": the sum of the year's months. The year in
  // progress takes its latest month (december) or the months so far (sum), and
  // is labelled with an asterisk.
  function yearlyAgg(dates, values, how) {
    const by = new Map();
    dates.forEach((d, i) => { const v = values[i]; if (v === null || v === undefined) return; const y = d.slice(0, 4); if (!by.has(y)) by.set(y, []); by.get(y).push([Number(d.slice(5, 7)), v, d]); });
    const years = [], vals = []; let partial = null;
    const ys = [...by.keys()].sort();
    // Quarterly data (quarter-start dates): a year is complete with four quarters, its last being Q4.
    const quarterly = dates.length > 1 && dates.every((d) => [1, 4, 7, 10].includes(Number(d.slice(5, 7))));
    const lastM = quarterly ? 10 : 12, full = quarterly ? 4 : 12;
    ys.forEach((y, n) => {
      const ms = by.get(y), last = ms[ms.length - 1];
      const nMonths = new Set(ms.map((m) => m[0])).size;
      const complete = how === "sum" ? nMonths === full : last[0] === lastM;
      if (!complete && n < ys.length - 1) return;           // incomplete early years are skipped
      if (!complete && how === "sum" && n === 0) return;
      const dec = ms.filter((m) => m[0] === lastM);
      const v = how === "sum" ? ms.reduce((a, m) => a + m[1], 0) : (dec.length ? dec[dec.length - 1] : last)[1];
      years.push(complete ? y : `${y}*`); vals.push(v);
      if (!complete) partial = { year: y, first: ms[0][2], last: last[2], quarterly };
    });
    return { years, values: vals, partial };
  }
  const yearlyNote = (how, partial) => (!partial ? "" : how === "sum"
    ? (partial.quarterly ? `*${partial.year}: ${fmtPeriod(partial.first, "Q").split(" ")[0]}–${fmtPeriod(partial.last, "Q")}, year to date.`
      : `*${partial.year}: ${fmtShortMonth(partial.first).split(" ")[0]}–${fmtShortMonth(partial.last)}, year to date.`)
    : `*${partial.year}: twelve months to ${fmtShortMonth(partial.last)}.`);

  // Where GDP comes from in ratios to GDP, and links to the methodology.
  function methodLine(ind) {
    const d = ind.derived || {};
    const links = [...(d.gdp_through ? [{ anchor: "gdp-ratios", label: "How ratios to GDP are calculated" }] : []), ...(ind.method_links || [])];
    if (!d.gdp_through && !links.length) return null;
    const parts = [];
    if (d.gdp_through) {
      const next = addMonths(d.gdp_through, 1);
      parts.push(d.gdp_estimated
        ? `GDP: INDEC through ${fmtShortMonth(d.gdp_through)}; from ${fmtShortMonth(next)} to ${fmtShortMonth(ind.last_obs)}, estimated by extending it with the CPI until INDEC publishes the quarter. `
        : `GDP: INDEC through ${fmtShortMonth(d.gdp_through)}. `);
    }
    links.forEach((l, i) => { if (i) parts.push(" · "); parts.push(h("a", { href: `${ROOT}methodology/#${l.anchor}` }, l.label)); });
    return h("p", { class: "hint method-line" }, ...parts);
  }

  // ---------- card: one series with variants ----------
  function variantsCard(ind, wide) {
    const variantKeys = Object.keys(ind.variants);
    const T = tfs(ind);
    const isRate = ind.measure === "rate";
    const DF = ind.frequency === "Q" ? "Q%q %Y" : ind.frequency === "D" ? "%d %b %Y" : ind.frequency === "Y" ? "%Y" : "%b %Y";   // hover date format
    const fP = (iso) => fmtPeriod(iso, ind.frequency);
    const state = {
      variant: ind.default.variant,
      transform: ind.default.transform || "level",
      range: rangeFromDefault(ind),
      base: ind.default.base ? "custom" : "published",   // "published" | "term:<id>" | "custom"
      customBase: ind.default.base || null,               // "YYYY-MM-01"
      byPres: false,
      episodes: false,            // mark dated shocks (registry `episodes`)
      yearly: false,              // yearly view (registry `yearly`)
      shown: new Set(ind.overlay_default || variantKeys),   // overlay series drawn (chips toggle the rest)
    };
    // A secondary series drawn as bars on a right axis (e.g. a rank), not one of the overlay lines.
    const SB = ind.secondary_bar || null;
    const lineKeys = SB ? variantKeys.filter((k) => k !== SB.variant && k !== SB.of) : variantKeys;
    const card = cardFrame(ind, !!wide);
    const Y = ind.yearly;
    const ySeg = Y ? segmented([["monthly", ind.frequency === "D" ? "Daily" : "Monthly"], ["yearly", "Yearly"]], "monthly", (k) => { state.yearly = k === "yearly"; ySeg.update(k); draw(); }, "Frequency") : null;
    const chartEl = h("div", { class: "chart" + (wide ? " tall-ish" : ""), role: "img", "aria-label": `${ind.title} chart` });
    const hint = h("p", { class: "hint" });
    const statsEl = h("div", { class: "term-stats", hidden: true });

    // Overlay cards draw every variant at once; the default one leads and is the
    // series used by the presidency view.
    const vSeg = variantKeys.length > 1 && !ind.overlay ? segmented(variantKeys.map((k) => [k, ind.variants[k].label]), state.variant, (k) => { state.variant = k; if (k === "original" && state.transform === "mom") state.transform = "yoy"; draw(); }, "Series") : null;
    const tSeg = Object.keys(T).length > 1 ? segmented(Object.entries(T).map(([k, t]) => [k, t.label]), state.transform, (k) => { state.transform = k; draw(); }, "Transformation") : null;
    const rSeg = segmented(rangeKeys(ind).map((k) => [k, k]), state.range, (k) => { state.range = k; draw(); }, "Time range");

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
    if (state.base === "custom") { baseSel.value = "custom"; customIn.hidden = false; customIn.value = state.customBase.slice(0, 7); }
    baseSel.addEventListener("change", () => {
      state.base = baseSel.value;
      customIn.hidden = state.base !== "custom";
      if (state.base === "custom" && !state.customBase) { customIn.value = ind.last_obs.slice(0, 7); state.customBase = ind.last_obs; }
      draw();
    });
    customIn.addEventListener("change", () => { if (customIn.value) { state.customBase = customIn.value + "-01"; draw(); } });
    const baseWrap = isRate || ind.plain_level || ind.default.rebase === false ? null : h("label", { class: "base-pick" }, h("span", {}, "Base = 100:"), baseSel, customIn);

    const EP = ind.episodes || [];
    const epBtn = EP.length ? h("button", { class: "btn toggle", type: "button", "aria-pressed": "false", title: "Mark dated shocks on the chart and show presidency averages without them",
      onclick: () => { state.episodes = !state.episodes; draw(); } }, "Episodes") : null;
    const epKey = EP.length ? h("ol", { class: "episode-key", hidden: true }, EP.map((e) =>
      h("li", {}, h("b", {}, e.label), ` (${fmtShortMonth(e.start)}${e.end !== e.start ? "–" + fmtShortMonth(e.end) : ""})`, e.description ? `: ${e.description}.` : ""))) : null;
    // Months inside any episode (for averages that leave them out).
    const inEpisode = (iso) => EP.some((e) => iso >= e.start && iso <= e.end);
    const realSeg = realSwitch(ind, () => draw());
    const presBtn = PRESIDENCIES.length && !ind.plain_level ? h("button", { class: "btn toggle", type: "button", "aria-pressed": "false", onclick: () => { state.byPres = !state.byPres; draw(); } }, "By presidency") : null;
    let table;
    // Party key for a line colored by presidency.
    function termKey() {
      if (!ind.term_colored) return null;
      const seen = new Map(); PRESIDENCIES.forEach((p) => { if (!seen.has(p.party)) seen.set(p.party, p); });
      return h("p", { class: "band-key" },
        h("span", {}, h("span", { class: "band-sw", style: `background:${cssVar("--ink")}` }), "Before December 1983"),
        [...seen.values()].map((p) => h("span", {}, h("span", { class: "band-sw", style: `background:${termColor(p)}` }), (PARTY_COLORS[p.party] || {}).label || p.party)));
    }
    // Chips: add or remove overlay series (e.g. comparison countries); the default series always stays.
    const chipButtons = {};
    const chipRow = ind.overlay && ind.overlay_default ? h("div", { class: "chip-row", role: "group", "aria-label": "Series shown" },
      h("span", { class: "chip-label" }, "Show:"),
      lineKeys.filter((k) => k !== ind.default.variant).map((k) => (chipButtons[k] = h("button", { type: "button", class: "chip", "aria-pressed": String(state.shown.has(k)),
        onclick: () => { state.shown.has(k) ? state.shown.delete(k) : state.shown.add(k); chipButtons[k].setAttribute("aria-pressed", String(state.shown.has(k))); draw(); } }, ind.variants[k].label)))) : null;

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
      // A derived series bridged over a gap has no meaningful change across it.
      const gaps = ind.derived && ind.derived.gaps && ind.derived.gaps[variantKey];
      if (gaps && state.transform !== "level") {
        const spans = gaps.map(([a, b]) => [monthIndex(a), monthIndex(b)]);
        y = y.map((q, i) => { const m = monthIndex(ind.dates[i]), lag = lagOf(state.transform, ind.dates[i], ind.frequency); return spans.some(([a, b]) => a <= m && b > m - lag) ? null : q; });
      }
      const bm = isRate ? null : baseMonth();
      let units = unitsOf(ind), rebased = false;
      if (bm) {
        let bi = ind.dates.indexOf(bm);
        // Daily data: the last observation on or before the base date.
        if (bi < 0 && ind.frequency === "D") bi = lastOnOrBefore(ind.dates.map(isoMs), isoMs(bm));
        const b = bi >= 0 ? v.values[bi] : null;
        if (b) { y = y.map((q) => (q === null ? null : (q / b) * 100)); units = `Index, ${fmtPeriod(bm, ind.frequency)} = 100`; rebased = true; }
      }
      return { v, y, units, rebased };
    }

    function visibleIdx(y) {
      const start = viewStart(ind, state.range);
      return ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && y[i] !== null);
    }
    const isLog = () => ind.log_level && state.transform === "level";
    const yFmt = () => (isLog() ? ".4~g" : ",.1f");
    // Annotations on a log axis are placed in log10 units (shapes take data values).
    const annY = (v) => (isLog() ? Math.log10(v) : v);

    // Shaded periods (e.g. the INDEC intervention), clipped to the visible range.
    function bandShapes(x) {
      const shapes = [], annotations = [];
      if (ind.term_shading && x.length && !state.byPres) {
        // Presidential terms behind annual series: a light band in the party's color, labelled at the top.
        const x0 = x[0], x1 = addMonths(x[x.length - 1], 12);
        PRESIDENCIES.forEach((p, n) => {
          const a = p.start, b = p.end || x1;
          if (b <= x0 || a >= x1) return;
          shapes.push({ type: "rect", xref: "x", yref: "paper", x0: a < x0 ? x0 : a, x1: b > x1 ? x1 : b, y0: 0, y1: 1, layer: "below",
            fillcolor: termColor(p), opacity: 0.13, line: { width: 0 }, _lightFill: termLight(p) });
          annotations.push({ xref: "x", yref: "paper", x: a < x0 ? x0 : a, y: 1, xanchor: "left", yanchor: "top", showarrow: false, textangle: -90,
            text: p.short, font: { size: 9, color: cssVar("--ink-3") } });
        });
      }
      if (!ind.bands || !x.length) return { shapes, annotations };
      const x0 = x[0], x1 = x[x.length - 1];
      for (const b of ind.bands) {
        const end = addMonths(b.end || x1, 1);
        if (end <= x0 || b.start > x1) continue;
        const s0 = b.start < x0 ? x0 : b.start;
        shapes.push(b.style === "outline"
          ? { type: "rect", xref: "x", yref: "paper", x0: s0, x1: end, y0: 0, y1: 1, layer: "below",
              fillcolor: "rgba(0,0,0,0)", line: { width: 1.2, dash: "dot", color: cssVar("--ink-3"), _light: "#85909A" } }
          : { type: "rect", xref: "x", yref: "paper", x0: s0, x1: end, y0: 0, y1: 1, layer: "below",
              fillcolor: cssVar("--band"), _lightFill: "rgba(54, 69, 79, 0.07)", line: { width: 0 } });
        if (b.label) annotations.push({ xref: "x", yref: "paper", x: s0, y: 1, xanchor: "left", yanchor: "top", showarrow: false,
          text: b.label, font: { size: 11, color: cssVar("--ink-3") } });
      }
      return { shapes, annotations };
    }

    // Key for shaded periods: shaded = one kind of period, dotted outline = another.
    const bandKey = ind.bands && ind.bands.some((b) => b.key) ? h("p", { class: "band-key" }, ind.bands.filter((b) => b.key).map((b) =>
      h("span", {}, h("span", { class: "band-sw" + (b.style === "outline" ? " outline" : "") }), b.key))) : null;

    // Quarter tracker: where the quarter in progress stands.
    function trackerLine() {
      const tk = ind.derived && ind.derived.tracker;
      if (!tk) return null;
      const mName = (iso) => fmtShortMonth(iso).split(" ")[0];
      const span = tk.months.length > 1 ? `${mName(tk.months[0])}–${mName(tk.months[tk.months.length - 1])}` : mName(tk.months[0]);
      const sp = INDEX_TRANSFORMS.yoy;
      const parts = [h("b", {}, `${fmtPeriod(tk.quarter, "Q")}${tk.complete ? "" : ` so far (${span})`}: `)];
      if (tk.qoq !== undefined) parts.push(`EMAE ${fmtNum(tk.qoq, sp)} q/q, seasonally adjusted`);
      if (!tk.complete && tk.qoq_if_flat !== undefined && tk.months.length > 1) parts.push(` (${fmtNum(tk.qoq_if_flat, sp)} if the rest of the quarter stays at ${mName(tk.months[tk.months.length - 1])}'s level)`);
      if (tk.yoy_original !== undefined) parts.push(` · ${fmtNum(tk.yoy_original, sp)} y/y, original series`);
      parts.push(tk.gdp_published ? " · GDP for the quarter is published." : " · GDP not yet published.");
      return h("p", { class: "summary-line" }, ...parts);
    }

    // Cumulative change of each variant over fixed windows, on the levels.
    const summaryEl = h("p", { class: "summary-line", hidden: true });
    function drawSummary() {
      if (!ind.summary_windows || !ind.summary_windows.length) return;
      const keys = [ind.default.variant, ...variantKeys.filter((k) => k !== ind.default.variant)];
      const parts = ind.summary_windows.map((w) => {
        const bi = ind.dates.indexOf(w.base);
        const res = keys.map((k) => {
          const vals = ind.variants[k].values;
          let ei = w.end ? ind.dates.indexOf(w.end) : lastValid(ind.dates, vals)?.i;
          if (bi < 0 || ei === undefined || ei < 0 || !vals[bi] || vals[ei] === null) return null;
          return { k, ratio: vals[ei] / vals[bi], end: ind.dates[ei] };
        });
        if (res.some((r) => r === null)) return null;
        const label = w.end ? w.label : `${w.label} (to ${fP(res[0].end)})`;
        const items = res.map((r, i) => {
          const sw = h("span", { class: "sw" + (i ? " dash" : "") }); sw.style.background = cssVar(SERIES_VARS[i]); sw.style.color = cssVar(SERIES_VARS[i]);
          return h("span", {}, sw, `${ind.variants[r.k].label} `, h("b", {}, fmtNum((r.ratio - 1) * 100, INDEX_TRANSFORMS.yoy)));
        });
        const gap = res.length === 2 ? (res[0].ratio / res[1].ratio - 1) * 100 : null;
        return h("span", {}, `Cumulative change, ${label}: `, ...items.flatMap((it, i) => (i ? [" · ", it] : [it])),
          gap === null ? null : h("span", { title: `${ind.variants[res[0].k].label} price level relative to ${ind.variants[res[1].k].label} at the end of the window` }, ` · price-level gap `, h("b", {}, fmtNum(gap, INDEX_TRANSFORMS.yoy))));
      }).filter(Boolean);
      summaryEl.replaceChildren(...parts.flatMap((p, i) => (i ? [h("br"), p] : [p])));
      summaryEl.hidden = !parts.length;
    }

    // Build traces; each carries _light (export color) so the PNG matches.
    function build() {
      const t = T[state.transform];
      const main = series(state.variant);
      const idx = visibleIdx(main.y);
      const x = idx.map((i) => ind.dates[i]), y = idx.map((i) => main.y[i]);
      const sfx = t.suffix;
      const traces = [], shapes = [], annotations = [];

      if (state.transform === "share" && ind.composition) {
        // Composition: each component's share of their sum, stacked to 100.
        const parts = ind.composition;
        const start = viewStart(ind, state.range);
        const ii = ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && parts.every((k) => ind.variants[k].values[i] !== null));
        const xs = ii.map((i) => ind.dates[i]);
        const tot = ii.map((i) => parts.reduce((a, k) => a + ind.variants[k].values[i], 0));
        parts.forEach((k, n) => {
          const slot = Math.min(n + 1, SERIES_VARS.length - 1);
          traces.push({ type: "bar", x: xs, y: ii.map((i, j) => (tot[j] ? (ind.variants[k].values[i] / tot[j]) * 100 : null)),
            name: ind.variants[k].label, marker: { color: cssVar(SERIES_VARS[slot]) }, _slot: slot,
            hovertemplate: `%{x|${DF}}: <b>%{y:,.1f}%</b><extra>${ind.variants[k].label}</extra>` });
        });
        return { traces, shapes: [], annotations: [], main, t, x: xs, y: traces.length ? traces[0].y : [] };
      }
      if (!state.byPres) {
        const keys = overlayKeys();
        const start = viewStart(ind, state.range);
        const FAN = ind.fan || null;
        keys.forEach((k, slot) => {
          if (FAN && (k === FAN.lo || k === FAN.hi)) return;   // drawn as a band below
          const s = k === state.variant ? main : series(k);
          // Overlays keep empty months in range so a gap in one series shows as a gap.
          // (Daily overlays drop each series' empty days, since sources keep different calendars.)
          const ii = ind.overlay ? ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && (!x.length || ind.dates[i] <= x[x.length - 1]) && (!["D", "S", "Y"].includes(ind.frequency) || s.y[i] !== null)) : idx;
          let xx = ii.map((i) => ind.dates[i]), yy = ii.map((i) => s.y[i]);
          if (ind.frequency === "D") [xx, yy] = breakGaps(xx, yy);
          if (ind.frequency === "S") [xx, yy] = breakGaps(xx, yy, 400);
          // With chips, each series keeps the color of its place in the full list, so adding one does not recolor the rest.
          const cslot = ind.overlay_default ? lineKeys.filter((q) => q !== ind.emphasis && !(ind.muted || []).includes(q)).indexOf(k) : slot;
          const color = cssVar(SERIES_VARS[Math.max(0, cslot) % SERIES_VARS.length]);
          const hover = `${ind.frequency === "S" ? "%{customdata}" : `%{x|${DF}}`}: <b>%{y:${yFmt()}}${sfx}</b><extra>${s.v.label}</extra>`;
          const emph = ind.emphasis === k;   // e.g. a total: thick and dark
          const muted = (ind.muted || []).includes(k);   // e.g. a regional median: grey reference
          const marks = ind.frequency === "S" || ind.frequency === "Y";
          if (emph && ind.term_colored && state.transform !== "mom") {
            // The emphasized series (e.g. Argentina) colored by presidency; before December 1983 in ink.
            const at = (d) => termOf(ind.frequency === "Y" ? d.slice(0, 4) + "-07-01" : d, ind.frequency === "Y" ? "M" : ind.frequency);
            const segs = [];
            xx.forEach((d, j) => { if (yy[j] === null) return; const p = at(d); const last = segs[segs.length - 1];
              if (last && last.p === p) { last.x.push(d); last.y.push(yy[j]); } else segs.push({ p, x: [d], y: [yy[j]] }); });
            const ink = cssVar("--ink");
            traces.push({ type: "scatter", mode: "lines", x: [null], y: [null], name: `${s.v.label} (colored by presidency)`, line: { color: ink, width: 3.2 }, _light: "#36454F", hoverinfo: "skip" });
            segs.forEach((g, n) => {
              const c = g.p ? termColor(g.p) : ink, cl = g.p ? termLight(g.p) : "#36454F";
              if (n) { const pv = segs[n - 1]; traces.push({ type: "scatter", mode: "lines", x: [pv.x[pv.x.length - 1], g.x[0]], y: [pv.y[pv.y.length - 1], g.y[0]], line: { color: c, width: 3.2 }, _light: cl, showlegend: false, hoverinfo: "skip" }); }
              traces.push({ type: "scatter", mode: marks ? "lines+markers" : "lines", x: g.x, y: g.y, name: s.v.label, showlegend: false,
                line: { color: c, width: 3.2 }, marker: { size: 5, color: c }, _light: cl,
                hovertemplate: `%{x|${DF}}: <b>%{y:${yFmt()}}${sfx}</b><extra>${s.v.label}${g.p ? " · " + g.p.short : ""}</extra>` });
            });
            return;
          }
          const lighter = ind.term_colored && !emph && !muted;   // comparison lines recede behind the emphasized one
          traces.push(state.transform === "mom"
            ? { type: "bar", x: xx, y: yy, name: s.v.label, marker: { color }, hovertemplate: hover, _slot: slot }
            : { type: "scatter", mode: marks ? "lines+markers" : "lines", x: xx, y: yy, name: s.v.label, connectgaps: false,
                ...(ind.frequency === "S" ? { customdata: xx.map((d) => fmtPeriod(d, "S")) } : {}),
                ...(marks ? { marker: { size: ind.frequency === "Y" ? 4 : 5, color: emph ? cssVar("--ink") : muted ? cssVar("--ink-3") : color } } : {}),
                line: emph ? { color: cssVar("--ink"), width: 3.2 } : muted ? { color: cssVar("--ink-3"), width: 2, dash: "dot" }
                  : cslot >= SERIES_VARS.length ? { color, width: lighter ? 1.4 : 1.8, dash: "dashdot" } : slot && !ind.emphasis && !ind.overlay_default ? { color, width: 1.8, dash: "dash" } : { color, width: lighter ? 1.5 : 2 },
                ...(lighter ? { opacity: 0.65 } : {}),
                ...(emph ? { _light: "#36454F" } : muted ? { _light: "#85909A" } : {}), hovertemplate: hover, _slot: emph || muted ? undefined : Math.max(0, cslot) % SERIES_VARS.length });
        });
        // Shaded band between two variants (e.g. the 25th and 75th percentiles of a survey).
        if (FAN && ind.variants[FAN.lo] && ind.variants[FAN.hi]) {
          const lo = series(FAN.lo).y, hi = series(FAN.hi).y;
          const ii = ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && lo[i] !== null && hi[i] !== null);
          const xs = ii.map((i) => ind.dates[i]);
          traces.unshift(
            { type: "scatter", mode: "lines", x: xs, y: ii.map((i) => lo[i]), line: { width: 0 }, showlegend: false, name: ind.variants[FAN.lo].label,
              hovertemplate: `%{x|${DF}}: <b>%{y:${yFmt()}}${sfx}</b><extra>${ind.variants[FAN.lo].label}</extra>` },
            { type: "scatter", mode: "lines", x: xs, y: ii.map((i) => hi[i]), fill: "tonexty", fillcolor: alpha(cssVar("--ink-3"), 0.3), line: { width: 0 },
              name: FAN.label, _lightFill: "rgba(133, 144, 154, 0.3)", hovertemplate: `%{x|${DF}}: <b>%{y:${yFmt()}}${sfx}</b><extra>${ind.variants[FAN.hi].label}</extra>` });
        }
        // The latest REM survey's expected path, after the last observation: dashed median and a 25th-75th percentile band.
        const FC = ind.forecast;
        if (FC && !state.yearly && !ind._real) FC.items.forEach((f) => {
          if ((f.transform || "level") !== state.transform || !keys.includes(f.variant)) return;
          const sv = f.variant === state.variant ? main : series(f.variant);
          if (sv.rebased) return;
          let li = sv.y.length - 1;
          while (li >= 0 && (sv.y[li] === null || sv.y[li] === undefined)) li--;
          if (li < 0) return;
          const d0 = ind.dates[li];
          const at = (iso) => (ind.frequency === "D" ? iso.slice(0, 8) + "15" : iso);
          const pts = f.points.filter((q) => at(q.date) > d0 && q.med !== null);
          if (!pts.length) return;
          const xs = [d0, ...pts.map((q) => at(q.date))];
          const pos = keys.filter((q) => !(FAN && (q === FAN.lo || q === FAN.hi))).indexOf(f.variant);
          const color = ind.emphasis === f.variant ? cssVar("--ink") : cssVar(SERIES_VARS[Math.max(0, pos) % SERIES_VARS.length]);
          const light = ind.emphasis === f.variant ? "#36454F" : undefined;
          const vl = ind.variants[f.variant].label;
          const lab = keys.length > 1 && !f.label.toLowerCase().includes(vl.toLowerCase()) ? `${f.label}, ${vl.toLowerCase()}` : f.label;
          if (pts.some((q) => q.p25 !== null && q.p75 !== null)) {
            traces.push(
              { type: "scatter", mode: "lines", x: xs, y: [sv.y[li], ...pts.map((q) => q.p25)], line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
              { type: "scatter", mode: "lines", x: xs, y: [sv.y[li], ...pts.map((q) => q.p75)], fill: "tonexty", fillcolor: alpha(color, 0.2), line: { width: 0 },
                showlegend: false, hoverinfo: "skip", _lightFill: alpha(light || ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5", "#C9A227", "#C96B7E", "#3E9C9A", "#6C7A89"][Math.max(0, pos) % 8], 0.2) });
          }
          traces.push({ type: "scatter", mode: "lines+markers", x: xs, y: [sv.y[li], ...pts.map((q) => q.med)], name: lab,
            line: { color, width: 2, dash: "dash" }, marker: { size: [0, ...pts.map(() => 5)], color }, _light: light, _slot: light ? undefined : Math.max(0, pos) % SERIES_VARS.length,
            customdata: [null, ...pts.map((q) => [q.p25, q.p75])],
            hovertemplate: `%{x|${ind.frequency === "Q" ? "Q%q %Y" : "%b %Y"}}: <b>%{y:${yFmt()}}${sfx}</b> expected (middle half %{customdata[0]:.1f}–%{customdata[1]:.1f})<extra>REM</extra>` });
          state._fcNote = `Dashed: expected in the BCRA's REM survey of ${fmtMonth(FC.survey)} (median; band: middle half of forecasters).`;
        });
        if (SB && ind.variants[SB.variant]) {
          const vb = ind.variants[SB.variant], vo = SB.of ? ind.variants[SB.of] : null;
          const ii = ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && vb.values[i] !== null);
          traces.unshift({ type: "bar", x: ii.map((i) => ind.dates[i]), y: ii.map((i) => vb.values[i]), yaxis: "y2", name: SB.label || vb.label,
            marker: { color: cssVar("--ink-3"), opacity: 0.28 }, _light: "#C9CED3",
            customdata: ii.map((i) => (vo ? vo.values[i] : null)),
            hovertemplate: `%{x|${DF}}: <b>%{y:,.0f}</b>${vo ? " of %{customdata:,.0f}" : ""}<extra>${SB.label || vb.label}</extra>` });
        }
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
        const stats = new Map(termStats(ind.dates, main.y, state.transform === "level", ind.measure, ind.frequency, state.transform, main.v.values).map((s) => [s.p.id, s]));
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
            const stat = st && st.avg !== null ? (t.change ? `${isAnnualized(state.transform, ind.measure) ? "annualized" : "avg"} ${fmtNum(st.avg, t)}` : `max ${fmtNum(st.max, t)}`) : "";
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
              hovertemplate: `%{x|${DF}}: <b>%{y:${yFmt()}}${sfx}</b><extra>${p.short}</extra>`,
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
          annotations.push({ xref: "paper", yref: "y", x: 0.005, y: annY(lastV), xanchor: "left", yanchor: "bottom", showarrow: false, text: `Latest: ${fmtNum(lastV, t)}`, font: { size: 12, color: cssVar("--ink-2") } });
        }
      }
      if (main.rebased && state.transform === "level") {
        shapes.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: cssVar("--ink-2"), width: 1, _light: "#5A6872" } });
      }
      if (ind.ref_line !== null && ind.ref_line !== undefined && state.transform === "level") {
        shapes.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: ind.ref_line, y1: ind.ref_line, line: { color: cssVar("--ink-2"), width: 1.2, dash: "dash", _light: "#5A6872" } });
      }
      // Quarter tracker: the quarter in progress is drawn lighter.
      const tk = ind.derived && ind.derived.tracker;
      if (tk && !tk.complete) traces.forEach((tr) => {
        if (tr.type === "bar" && tr.name === ind.variants.emae.label) tr.marker = { ...tr.marker, opacity: tr.x.map((d) => (d === tk.quarter ? 0.45 : 1)) };
      });
      const bs = bandShapes(x);
      if (state.episodes && x.length) {
        EP.forEach((e, n) => {
          if (addMonths(e.end, 1) <= x[0] || e.start > x[x.length - 1]) return;
          bs.shapes.push({ type: "rect", xref: "x", yref: "paper", x0: addMonths(e.start, 0), x1: addMonths(e.end, 1), y0: 0, y1: 1, layer: "below",
            fillcolor: isDark() ? "rgba(212, 116, 94, 0.22)" : "rgba(212, 116, 94, 0.16)", _lightFill: "rgba(212, 116, 94, 0.16)", line: { width: 0 } });
          bs.annotations.push({ xref: "x", yref: "paper", x: e.start, y: 1, xanchor: "left", yanchor: "top", showarrow: false,
            text: `<b>${n + 1}</b>`, font: { size: 12, color: cssVar("--highlight") } });
        });
      }
      return { traces, shapes: [...bs.shapes, ...shapes], annotations: [...bs.annotations, ...annotations], main, t, x, y };
    }

    // Yearly view: grouped bars of each variant's yearly value.
    function buildYearly() {
      const t = T.level || Object.values(T)[0];
      const keys = Y.variants || (ind.overlay ? [state.variant, ...variantKeys.filter((k) => k !== state.variant && state.shown.has(k))] : [state.variant]);
      const start = viewStart(ind, state.range);
      let partial = null, labels = null;
      const traces = keys.map((k, slot) => {
        const v = ind.variants[k];
        const a = yearlyAgg(ind.dates, v.values, Y.how);
        const ii = a.years.map((y, i) => i).filter((i) => !start || `${a.years[i].slice(0, 4)}-12-31` >= start);
        partial = partial || a.partial; labels = labels || ii.map((i) => a.years[i]);
        return { type: "bar", name: v.label, x: ii.map((i) => a.years[i]), y: ii.map((i) => a.values[i]), _slot: slot,
          marker: { color: cssVar(SERIES_VARS[slot % SERIES_VARS.length]) },
          hovertemplate: `%{x}: <b>%{y:,.1f}${t.suffix}</b><extra>${v.label}</extra>` };
      });
      const shapes = ind.ref_line !== null && ind.ref_line !== undefined
        ? [{ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: ind.ref_line, y1: ind.ref_line, line: { color: cssVar("--ink-2"), width: 1.2, dash: "dash", _light: "#5A6872" } }] : [];
      return { traces, shapes, t, keys, labels: labels || [], partial };
    }

    function drawYearly() {
      const { traces, shapes, t, keys, labels, partial } = buildYearly();
      const msgs = [Y.how === "sum" ? "Calendar-year totals." : "Calendar years: the December value of the twelve-month series."];
      if (partial) msgs.push(yearlyNote(Y.how, partial));
      if (ind._real) msgs.push(`Real terms: pesos of ${fmtMonth(ind.deflator.latest)}.`);
      hint.textContent = msgs.join(" ");
      statsEl.hidden = true;
      Plotly.react(chartEl, traces, baseLayout({
        pct: t, xaxis: yearAxis(labels.length),
        extra: { bargap: 0.25, barmode: "group", shapes, showlegend: traces.length > 1,
          legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } }, margin: { l: 52, r: 16, t: 10, b: 36 } },
      }), PLOT_CONFIG);
      table && table.refresh(labels, traces.map((tr) => ({ label: tr.name, values: labels.map((l) => { const j = tr.x.indexOf(l); return j < 0 ? null : tr.y[j]; }) })), t, "Y");
    }

    function drawStats(main, t) {
      if (!state.byPres) { statsEl.hidden = true; return; }
      const isLevel = state.transform === "level";
      const rows = termStats(ind.dates, main.y, isLevel, ind.measure, ind.frequency, state.transform, main.v.values);
      // With episodes on, m/m averages also shown without the episode months.
      const exEp = state.episodes && EP.length && state.transform === "mom";
      const exRows = exEp ? new Map(termStats(ind.dates, main.y.map((v, i) => (inEpisode(ind.dates[i]) ? null : v)), false, ind.measure, ind.frequency, state.transform).map((r) => [r.p.id, r])) : null;
      const geo = isGeometric(state.transform, ind.measure), ann = isAnnualized(state.transform, ind.measure);
      const f = (v) => fmtNum(v, t);
      const fc = (v) => fmtNum(v, isRate ? RATE_TRANSFORMS.yoy : INDEX_TRANSFORMS.yoy);
      const head = isLevel
        ? ["Presidency", "Period", "Start", "Latest / end", "Change", "Average", "Min", "Max"]
        : ["Presidency", "Period", geo ? "Average (geometric)" : ann ? "Annualized over term" : "Average", ...(exEp ? ["Excl. episodes"] : []), "Min", "Max", "Latest / end"];
      const tbody = h("tbody", {}, rows.slice().reverse().map((r) => {
        const sw = h("span", { class: "sw" }); sw.style.background = termColor(r.p);
        const period = `${fP(r.first)} – ${r.ongoing ? "present" : fP(r.last)}${r.partial ? "*" : ""}`;
        const mm = (v, at) => h("span", {}, f(v), h("span", { class: "at" }, ` ${fP(at)}`));
        const cells = isLevel
          ? [r.start === null ? "–" : f(r.start), f(r.end), r.change === null ? "–" : fc(r.change), f(r.avg), mm(r.min, r.minAt), mm(r.max, r.maxAt)]
          : [f(r.avg), ...(exEp ? [exRows.has(r.p.id) ? f(exRows.get(r.p.id).avg) : "–"] : []), mm(r.min, r.minAt), mm(r.max, r.maxAt), f(r.end)];
        return h("tr", {}, h("td", { class: "pres" }, sw, r.p.short), h("td", {}, period), ...cells.map((c) => h("td", {}, c)));
      }));
      const note = [
        `Statistics use each full term regardless of the time range shown. ${isLevel ? `Start is the base ${ind.frequency === "Q" ? "quarter" : "month"} (the last one before the term); change is latest/end vs. start.` : ""}`,
        geo ? ` The average ${T.mom.label.split(" ")[0]} rate is geometric: the constant rate that compounds to the term's cumulative change.` : "",
        exEp ? ` Excl. episodes: the same average without the months marked as episodes (${EP.map((e, n) => n + 1).join(", ")}).` : "",
        state.episodes && EP.length && state.transform !== "mom" ? " Averages without the episodes are shown in the m/m view." : "",
        ann ? " Annualized over term: the constant annual rate that compounds to the term's cumulative change, from the base month to the latest/end month." : "",
        rows.some((r) => r.partial) ? " *Series begins after the term started." : "",
      ].join("");
      statsEl.replaceChildren(
        h("div", { class: "table-scroll" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))), tbody)),
        h("p", { class: "chips-note" }, note));
      statsEl.hidden = false;
    }

    function draw() {
      vSeg && vSeg.update(state.variant);
      tSeg && tSeg.update(state.transform, { mom: state.variant === "original" });
      rSeg.update(state.range);
      presBtn && presBtn.setAttribute("aria-pressed", String(state.byPres));
      epBtn && epBtn.setAttribute("aria-pressed", String(state.episodes));
      if (epKey) epKey.hidden = !state.episodes;
      baseSel.disabled = state.transform !== "level";
      customIn.disabled = state.transform !== "level";
      if (state.yearly) {
        const hide = (el, on) => { if (el) el.style.display = on ? "none" : ""; };
        hide(tSeg, true); hide(presBtn, true); hide(baseWrap, true); hide(vSeg, !!Y.variants);
        drawYearly(); return;
      }
      for (const el of [tSeg, presBtn, baseWrap, vSeg]) if (el) el.style.display = "";

      const { traces, shapes, annotations, main, t, x, y } = build();
      const msgs = [];
      if (isRate && T[state.transform] && T[state.transform].change) msgs.push("Changes in a rate are shown in percentage points.");
      if (state.variant === "original" && T.mom) msgs.push(`${T.mom.label.split(" ")[0]} changes are only meaningful on seasonally adjusted data.`);
      const defaultBase = ind.default.base && state.base === "custom" && state.customBase === ind.default.base;
      if (state.transform !== "level" && state.base !== "published" && !defaultBase) msgs.push("Rebasing applies to levels only; percent changes are unaffected.");
      if (state.transform === "level" && state.base !== "published" && !main.rebased) msgs.push("No data for that base month; showing the series as published.");
      if (ind.overlay && state.byPres) msgs.push(`By presidency shows the ${main.v.label.toLowerCase()} series.`);
      if (isLog()) msgs.push("Log scale: equal distances are equal percentage changes.");
      if (ind._real) msgs.push(`Real terms: deflated by the ${ind.deflator.label}, in pesos of ${fmtMonth(ind.deflator.latest)}; months after that have no CPI yet.`);
      if (state._fcNote) { msgs.push(state._fcNote); state._fcNote = null; }
      if (partialNote(ind)) msgs.push(partialNote(ind));
      hint.textContent = msgs.join(" ");

      Plotly.react(chartEl, traces, baseLayout({
        pct: t,
        // Plain numbers on log axes unless the values are tiny (long-run price indices).
        yaxis: isLog() ? { type: "log", ticksuffix: "", exponentformat: Math.min(...y.filter((q) => q > 0)) < 0.01 ? "power" : "none", tickformat: Math.min(...y.filter((q) => q > 0)) < 0.01 ? undefined : ",~r" } : { type: "linear" },
        extra: {
          bargap: 0.2, shapes, annotations, barmode: state.transform === "share" ? "stack" : "group",
          showlegend: (state.byPres && state.transform !== "mom") || (ind.overlay && !state.byPres),
          legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } },
          margin: { l: 52, r: SB ? 56 : 16, t: state.byPres ? 30 : 10, b: 36 },
          ...(SB ? { yaxis2: y2Axis(false) } : {}),
        },
      }), PLOT_CONFIG);
      drawStats(main, t);
      const suffix = t.change ? " (" + t.label + ")" : main.rebased ? ` (${main.units})` : "";
      const cols = overlayKeys().map((k) => {
        const s = k === state.variant ? main : series(k);
        return { label: `${s.v.label}${suffix}`, values: x.map((d) => s.y[ind.dates.indexOf(d)]) };
      });
      table && table.refresh(x, cols, t, ind.frequency);
    }

    const y2Axis = (light) => ({ overlaying: "y", side: "right", showgrid: false, zeroline: false, rangemode: "tozero",
      title: { text: SB.axis || SB.label, font: { size: light ? 15 : 11, color: light ? "#85909A" : cssVar("--ink-3") } },
      tickfont: { color: light ? "#85909A" : cssVar("--ink-3") } });
    function overlayKeys() { return ind.overlay && !state.byPres ? [state.variant, ...lineKeys.filter((k) => k !== state.variant && state.shown.has(k))] : [state.variant]; }
    const subtitle = (main, t) => `${overlayKeys().map((k) => ind.variants[k].label).join(" vs. ")} · ${t.change ? t.short : main.units}`;
    const fileTag = () => `${ind.id}_${state.variant}_${state.transform}${baseMonth() ? "_base" + baseMonth().slice(0, 7) : ""}${state.byPres ? "_presidencies" : ""}`;
    const onPNG = () => {
      if (state.yearly) {
        const yb = buildYearly();
        return exportPNG(ind, yb.traces, `${yb.traces.map((tr) => tr.name).join(" vs. ")} · yearly${yb.partial ? " · " + yearlyNote(Y.how, yb.partial) : ""}`, yb.t, `${ind.id}_yearly.png`, { shapes: yb.shapes, barmode: "group", xcategory: true });
      }
      const { traces, shapes, annotations, main, t } = build();
      exportPNG(ind, traces, subtitle(main, t), t, `${fileTag()}.png`, { shapes, annotations, ylog: isLog(), barmode: state.transform === "share" ? "stack" : "group", ...(SB ? { yaxis2: y2Axis(true) } : {}) });
    };
    const onCSV = () => {
      if (state.yearly) {
        const yb = buildYearly();
        const rows = yb.labels.map((l) => [l, ...yb.traces.map((tr) => { const j = tr.x.indexOf(l); return j < 0 ? "" : +tr.y[j].toPrecision(8); })]);
        return downloadBlob(toCSV(["year", ...yb.traces.map((tr) => `${ind.short_title} — ${tr.name} — ${unitsOf(ind)}`)], rows), `${ind.id}_yearly.csv`, "text/csv");
      }
      const { main, t, x, y } = build();
      const ks = [...overlayKeys(), ...(SB && !state.byPres ? [SB.variant, SB.of].filter(Boolean) : [])];
      const ys = ks.map((k) => (k === state.variant ? main.y : series(k).y));
      const header = ["date", ...ks.map((k) => `${ind.short_title} — ${ind.variants[k].label} — ${t.change ? t.short : main.units}`)];
      if (state.byPres) header.push("presidency");
      const rows = x.map((d, i) => {
        const j = ind.dates.indexOf(d);
        const r = [d, ...ys.map((yy) => (yy[j] === null || yy[j] === undefined ? "" : +yy[j].toPrecision(8)))];
        if (state.byPres) { const p = termOf(d, ind.frequency); r.push(p ? p.name : ""); }
        return r;
      });
      downloadBlob(toCSV(header, rows), `${fileTag()}.csv`, "text/csv");
    };

    appendAll(card, 
      h("div", { class: "controls" }, ySeg, vSeg, tSeg, h("span", { class: "spacer" }), ind.view_start ? null : rSeg),
      // A short fixed window (view_start) has no use for rebasing or presidencies.
      ind.view_start ? null : h("div", { class: "controls" }, realSeg, baseWrap, presBtn, epBtn),
      ind.caveat ? h("p", { class: "caveat" }, ind.caveat) : null,
      trackerLine(), summaryEl, hint, methodLine(ind), bandKey, termKey(), epKey, chartEl, chipRow, statsEl);
    drawSummary();
    table = tableView([], [], false, ind.frequency);
    appendAll(card, breakdownTable(ind), table, annualTable(ind), weightsTable(ind), ind.note ? h("p", { class: "chips-note" }, ind.note) : null, footer(ind, onPNG, onCSV));
    card.draw = draw;
    return card;
  }

  // ---------- card: BCRA balance sheet (one weekly balance, optionally compared with another) ----------
  const ACRONYMS = ["BCRA", "IMF", "SDR", "SDRS", "USD", "LEBAC", "NOBAC", "LELIQ", "NOTALIQ", "A.L.A.D.I", "I.F.M.", "BIS", "BOPREAL", "Argentine", "Argentina", "Treasury"];
  function niceLabel(s) {
    const letters = s.replace(/[^A-Za-z]/g, "");
    if (!letters || letters.replace(/[^A-Z]/g, "").length / letters.length < 0.8) return s;
    const low = s.toLowerCase();
    let out = low.charAt(0).toUpperCase() + low.slice(1);
    for (const a of ACRONYMS) out = out.replace(new RegExp(`\\b${a.replace(/\./g, "\\.")}`, "gi"), a);
    return out;
  }

  function balanceCard(ind) {
    const T = ind.table;
    const years = Object.keys(ind.years).sort();
    const groups = ind.groups ? Object.keys(ind.groups) : null;
    const cache = new Map();
    const fD = (d) => (T.monthly ? fmtShortMonth(d) : fmtDay(d));
    const load = (y) => {
      const key = `${state.group || ""}/${y}`;
      if (!cache.has(key)) cache.set(key, getJSON(`${DATA}${T.path}${state.group ? state.group + "/" : ""}${y}.json`));
      return cache.get(key);
    };
    const lastYear = years[years.length - 1];
    const state = { year: lastYear, date: ind.years[lastYear].slice(-1)[0], cyear: "", cdate: "", units: "ars", group: groups ? groups[0] : null };
    const card = cardFrame(ind, true);

    const sel = (opts, value, label, onChange) => {
      const el = h("select", { class: "select", "aria-label": label }, opts.map(([v, t]) => h("option", { value: v }, t)));
      el.value = value; el.addEventListener("change", () => onChange(el.value)); return el;
    };
    const dateOpts = (y) => ind.years[y].slice().reverse().map((d) => [d, fD(d)]);
    const gSel = groups ? sel(groups.map((g) => [g, (ind.group_labels || {})[g] || g]), state.group, "Group", (g) => { state.group = g; render(); }) : null;
    const ySel = sel(years.slice().reverse().map((y) => [y, y]), state.year, "Year", (y) => { state.year = y; state.date = ind.years[y].slice(-1)[0]; dSel.replaceChildren(...dateOpts(y).map(([v, t]) => h("option", { value: v }, t))); dSel.value = state.date; render(); });
    const dSel = sel(dateOpts(state.year), state.date, "Weekly balance", (d) => { state.date = d; render(); });
    const setCompare = (y) => {
      state.cyear = y; state.cdate = y ? ind.years[y].slice(-1)[0] : "";
      cySel.value = y;
      cdSel.replaceChildren(...(y ? dateOpts(y) : []).map(([v, t]) => h("option", { value: v }, t))); cdSel.value = state.cdate;
      cdSel.hidden = !y; clearBtn.hidden = !y; render();
    };
    const cySel = sel([["", "No comparison"], ...years.slice().reverse().map((y) => [y, y])], "", "Compare with: year", (y) => setCompare(y));
    const clearBtn = h("button", { class: "btn", type: "button", hidden: true, title: "Show only the selected balance", onclick: () => setCompare("") }, "✕ Remove comparison");
    const cdSel = sel([], "", "Compare with: weekly balance", (d) => { state.cdate = d; render(); });
    cdSel.hidden = true;
    const unitOpts = [["ars", "Pesos"], ["usd", "US dollars"], ["pct", "% of assets"]].filter(([k]) => !T.units || T.units.includes(k));
    const uSeg = segmented(unitOpts, state.units, (k) => { state.units = k; uSeg.update(k); render(); }, "Units");

    const guide = h("details", { class: "bs-guide", open: true }, h("summary", {}, "How to read this balance sheet"),
      h("ul", {}, (T.guide || []).map((g) => { const li = h("li"); li.innerHTML = g; return li; })));
    const unitsNote = h("p", { class: "hint" });
    const grid = h("div", { class: "bs-grid" });
    const notesEl = h("div", { class: "bs-notes" });

    const keyed = (t) => {
      const seen = new Map();
      return t.rows.map((r) => { const k = `${r.side}|${r.label.toLowerCase()}`; const n = (seen.get(k) || 0) + 1; seen.set(k, n); return `${k}|${n}`; });
    };
    const valuationAt = (d) => (T.valuation_notes || []).find((n) => d >= n.from && (!n.to || d <= n.to));
    let view = null;   // for CSV

    async function render() {
      const t = await load(state.year);
      const ct = state.cyear && state.cdate ? await load(state.cyear) : null;
      const i = t.dates.indexOf(state.date), ci = ct ? ct.dates.indexOf(state.cdate) : -1;
      const fx = t.fx ? t.fx[i] : null, cfx = ct && ct.fx ? ct.fx[ci] : null;
      const totalOf = (tt, j) => {
        const r = tt.rows.find((x) => x.kind === "total" && /^total assets/i.test(x.label) && x.values[j] !== null)
          || tt.rows.find((x) => x.kind === "total" && /liabilities \+ net equity/i.test(x.label));
        return r ? r.values[j] : null;
      };
      const tot = totalOf(t, i), ctot = ct ? totalOf(ct, ci) : null;
      const conv = (v, rate, total) => {
        if (v === null || v === undefined) return null;
        if (state.units === "usd") return rate ? v / rate : null;           // millions of pesos / (pesos per dollar) = millions of dollars
        if (state.units === "pct") return total ? (v / total) * 100 : null;
        return v / 1000;                                                    // billions of pesos
      };
      const fmt = (v) => (v === null ? "–" : (v = +v.toFixed(state.units === "usd" ? 0 : 1), state.units === "pct") ? (v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%"
        : (v < 0 ? "−" : "") + Math.abs(v).toLocaleString(LOCALE, { maximumFractionDigits: state.units === "usd" ? 0 : 1, minimumFractionDigits: state.units === "usd" ? 0 : 1 }));
      const fmtChg = (v) => (v === null ? "–" : (v = +v.toFixed(state.units === "usd" ? 0 : 1), v > 0 ? "+" : v < 0 ? "−" : "") + (state.units === "pct" ? Math.abs(v).toFixed(1) + " pp"
        : Math.abs(v).toLocaleString(LOCALE, { maximumFractionDigits: state.units === "usd" ? 0 : 1, minimumFractionDigits: state.units === "usd" ? 0 : 1 })));
      const cmap = new Map();
      if (ct) { const ks = keyed(ct); ct.rows.forEach((r, j) => cmap.set(ks[j], r)); }
      const ks = keyed(t);
      const valLabel = (T.valuation_label || "").toLowerCase();
      const csv = [];
      const side = (code, title) => {
        const head = [h("th", {}, title), h("th", {}, fD(state.date))];
        if (ct) head.push(h("th", {}, fD(state.cdate)), h("th", {}, "Change"));
        const body = t.rows.map((r, j) => {
          if (r.side !== code) return null;
          const a = r.values ? conv(r.values[i], fx, tot) : null;
          const cr = ct ? cmap.get(ks[j]) : null;
          const b = cr && cr.values ? conv(cr.values[ci], cfx, ctot) : null;
          const mark = valLabel && r.label.toLowerCase().startsWith(valLabel) ? " †" : "";
          const cls = r.kind === "total" ? "total" : r.kind === "less" ? "less" : r.kind === "computed" ? "computed" : r.level === 0 ? "main" : "";
          const cells = [h("td", { class: "pres", style: `padding-left:${10 + r.level * 16}px` }, niceLabel(r.label) + mark),
            h("td", {}, r.kind === "less" || r.kind === "header" ? "" : fmt(a))];
          if (ct) cells.push(h("td", {}, r.kind === "less" || r.kind === "header" ? "" : fmt(b)), h("td", {}, r.kind === "less" || a === null || b === null ? "" : fmtChg(a - b)));
          csv.push([code === "A" ? "Assets" : "Liabilities and net equity", niceLabel(r.label), a ?? "", ...(ct ? [b ?? "", a !== null && b !== null ? a - b : ""] : [])]);
          return h("tr", { class: cls }, cells);
        });
        return h("div", { class: "bs-side" }, h("div", { class: "table-scroll" },
          h("table", { class: "data bs" }, h("thead", {}, h("tr", {}, head)), h("tbody", {}, body))));
      };
      grid.replaceChildren(side("A", "Assets"), side("L", "Liabilities and net equity"));
      const unitText = state.units === "usd" ? "Millions of US dollars, at each balance's exchange rate"
        : state.units === "pct" ? "Percent of total assets on each date" : `Billions of pesos of each ${T.monthly ? "month" : "date"}`;
      const rateText = [fx ? `${fD(state.date)}: ${fx.toLocaleString(LOCALE, { maximumFractionDigits: 2 })} pesos per dollar` : null,
        ct && cfx ? `${fD(state.cdate)}: ${cfx.toLocaleString(LOCALE, { maximumFractionDigits: 2 })}` : null].filter(Boolean).join("; ");
      unitsNote.textContent = `${unitText}.${rateText ? " Exchange rate " + rateText + "." : ""}${ct ? " Lines are matched by name; a line renamed or regrouped between the two dates shows no change." : ""}`;
      const vn = [valuationAt(state.date), ct ? valuationAt(state.cdate) : null];
      const notes = [];
      if (vn[0]) notes.push(h("p", { class: "chips-note" }, h("b", {}, `† On ${fD(state.date)}: `), vn[0].text));
      if (vn[1] && vn[1] !== vn[0]) notes.push(h("p", { class: "chips-note" }, h("b", {}, `† On ${fD(state.cdate)}: `), vn[1].text));
      const bcraNotes = [...new Set([...(t.notes || []), ...(ct && ct !== t ? ct.notes || [] : [])])];
      if (bcraNotes.length) notes.push(h("details", { class: "table-view" }, h("summary", {}, "BCRA's notes to these balances"),
        bcraNotes.map((n) => h("p", { class: "chips-note" }, n))));
      notesEl.replaceChildren(...notes);
      view = { header: ["side", "line", `${state.group ? state.group + " " : ""}${state.date} (${unitText})`, ...(ct ? [state.cdate, "change"] : [])], rows: csv };
    }

    const onCSV = () => { if (view) downloadBlob(toCSV(view.header, view.rows), `${ind.id}_${state.group ? state.group + "_" : ""}${state.date}${state.cdate ? "_vs_" + state.cdate : ""}_${state.units}.csv`, "text/csv"); };
    appendAll(card,
      h("div", { class: "controls" }, gSel ? h("label", { class: "base-pick" }, h("span", {}, "Group:"), gSel) : null,
        h("label", { class: "base-pick" }, h("span", {}, "Balance:"), ySel, dSel),
        h("label", { class: "base-pick" }, h("span", {}, "Compare with:"), cySel, cdSel), clearBtn, h("span", { class: "spacer" }), uSeg),
      guide, unitsNote, grid, notesEl,
      h("div", { class: "card-foot" },
        h("span", {}, "Source: ", T.source_url ? h("a", { href: T.source_url, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label,
          ` · Latest: ${fD(ind.last_obs)}`),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download the balance sheet in this view as CSV" }, "CSV"))));
    card.draw = () => render().catch((e) => { console.error(e); grid.replaceChildren(h("div", { class: "error-box" }, "The balance sheet could not be loaded.")); });
    return card;
  }

  // ---------- card: the ten largest institutions and their balance-sheet mix ----------
  function top10Card(ind) {
    const T = ind.table;
    const card = cardFrame(ind, true);
    let data = null;
    const state = { date: null, side: "assets" };
    const dSel = h("select", { class: "select", "aria-label": "Date" });
    dSel.addEventListener("change", () => { state.date = dSel.value; render(); });
    const sSeg = segmented([["assets", "Assets"], ["funding", "Funding"], ["summary", "Summary"]], state.side, (k) => { state.side = k; sSeg.update(k); render(); }, "View");
    const tableEl = h("div", { class: "table-scroll top10" });
    const hint = h("p", { class: "hint" });
    const pct = (v) => (v === null || v === undefined ? "–" : (v < 0 ? "−" : "") + fx(Math.abs(v), 1));
    const SUMMARY = [["public_total", "Public sector (Treasury, provinces, BCRA notes and repos)"], ["loans_private", "Loans to the private sector"],
      ["liquid", "Cash and BCRA current accounts"], ["fx_share_dep", "Dollar share of deposits"], ["loans_to_dep", "Loans / deposits"], ["equity", "Net equity"]];
    let view = null;

    function render() {
      const t = data.tables[state.date];
      const cols = state.side === "summary" ? SUMMARY : data.rows[state.side];
      // Shade each cell relative to its column's range, so differences stand out.
      const all = [...t.banks, ...t.benchmarks];
      const range = Object.fromEntries(cols.map(([k]) => { const v = all.map((r) => r[k]).filter((x) => x !== null && x !== undefined); return [k, [Math.min(...v), Math.max(...v)]]; }));
      const shade = (k, v) => { const [a, b] = range[k]; if (v === null || v === undefined || b <= a) return null; const f = (v - a) / (b - a); return `background: color-mix(in srgb, var(--accent) ${Math.round(f * 28)}%, transparent)`; };
      const head = h("tr", {}, h("th", {}, "#"), h("th", {}, "Institution"), h("th", {}, "Share of system assets"), cols.map(([, l]) => h("th", {}, l)));
      const body = t.banks.map((b) => h("tr", {}, h("td", {}, String(b.rank)),
        h("td", { class: "pres" }, b.name, b.group ? h("span", { class: "at" }, ` ${b.group}`) : null),
        h("td", {}, pct(b.assets_share)), cols.map(([k]) => h("td", { style: shade(k, b[k]) }, pct(b[k])))));
      const bench = t.benchmarks.map((b, i) => h("tr", { class: i === 0 ? "bench first" : "bench" }, h("td", {}, ""), h("td", { class: "pres" }, h("b", {}, b.name)), h("td", {}, ""),
        cols.map(([k]) => h("td", { style: shade(k, b[k]) }, pct(b[k])))));
      tableEl.replaceChildren(h("table", { class: "data top" }, h("thead", {}, head), h("tbody", {}, body, bench)));
      const what = state.side === "assets" ? "Percent of each institution's total assets." : state.side === "funding" ? "Percent of each institution's total assets (liabilities plus net equity)." : "Percent of assets, except the dollar share (percent of deposits) and loans / deposits.";
      hint.textContent = `${what} ${t.n} institutions in the system. Shading: higher values within each column are darker.`;
      view = { header: ["rank", "institution", "share_of_system_assets", ...cols.map(([k]) => k)],
        rows: [...t.banks.map((b) => [b.rank, b.name, b.assets_share, ...cols.map(([k]) => b[k] ?? "")]), ...t.benchmarks.map((b) => ["", b.name, "", ...cols.map(([k]) => b[k] ?? "")])] };
    }
    const onCSV = () => { if (view) downloadBlob(toCSV(view.header, view.rows), `${ind.id}_${state.date}_${state.side}.csv`, "text/csv"); };
    appendAll(card, h("div", { class: "controls" }, h("label", { class: "base-pick" }, h("span", {}, "Date:"), dSel), h("span", { class: "spacer" }), sSeg),
      hint, tableEl, ind.note ? h("p", { class: "chips-note" }, ind.note) : null,
      h("div", { class: "card-foot" },
        h("span", {}, "Source: ", T.source_url ? h("a", { href: T.source_url, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label, ` · Latest: ${fmtShortMonth(ind.last_obs)}`),
        h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download this table as CSV" }, "CSV"))));
    card.draw = async () => {
      if (!data) {
        try { data = await getJSON(`${DATA}${T.path}${T.file}`); } catch (e) { console.error(e); tableEl.replaceChildren(h("div", { class: "error-box" }, "The table could not be loaded.")); return; }
        const ds = data.dates.slice().reverse();
        dSel.replaceChildren(...ds.map((d) => h("option", { value: d }, d === data.latest ? `${fmtShortMonth(d)} (latest)` : (d.slice(5, 7) === "12" ? `Dec ${d.slice(0, 4)}` : fmtShortMonth(d)))));
        state.date = data.latest; dSel.value = state.date;
      }
      render();
    };
    return card;
  }

  // ---------- card: debt maturity profile (Finance Secretariat, quarterly) ----------
  function scheduleCard(ind) {
    const T = ind.table;
    const card = cardFrame(ind, true);
    let data = null;
    const state = { view: "monthly", part: "total" };
    const vSeg = segmented([["monthly", "Next 18 months"], ["annual", "By year"]], state.view, (k) => { state.view = k; vSeg.update(k); draw(); }, "View");
    const pSeg = segmented([["total", "Capital + interest"], ["capital", "Capital"], ["interest", "Interest"]], state.part, (k) => { state.part = k; pSeg.update(k); draw(); }, "Payments");
    const chartEl = h("div", { class: "chart tall-ish", role: "img", "aria-label": "Debt maturities by type of debt" });
    const hint = h("p", { class: "hint" });
    const sumEl = h("div", { class: "table-scroll" });
    const fmt = (v) => (v === null || v === undefined ? "–" : Math.round(v).toLocaleString(LOCALE));
    const LAST_YEAR = 2040;
    let view = null;

    // Columns for the current view: labels and, per group, the values of the chosen part.
    function columns() {
      const P = data[state.view];
      const val = (k, j) => (state.part === "capital" ? P.capital[k][j] : state.part === "interest" ? P.interest[k][j] : P.capital[k][j] + P.interest[k][j]);
      if (state.view === "monthly") {
        const labels = P.dates.map((d) => fmtShortMonth(d));
        return { labels, keys: P.dates, cols: data.groups.map((g) => ({ ...g, values: P.dates.map((_, j) => val(g.key, j)) })) };
      }
      // Years after LAST_YEAR (and the 2055-2089 block) together.
      const idx = P.years.map((y, j) => j);
      const keep = idx.filter((j) => /^\d{4}$/.test(P.years[j]) && Number(P.years[j]) <= LAST_YEAR);
      const rest = idx.filter((j) => !keep.includes(j));
      const first = data.as_of ? Number(data.as_of.slice(5, 7)) : 12;
      const labels = keep.map((j) => (j === 0 && first < 12 ? `${P.years[j]} (rest)` : P.years[j])).concat(rest.length ? [`${LAST_YEAR + 1}+`] : []);
      return { labels, keys: labels, cols: data.groups.map((g) => ({ ...g,
        values: keep.map((j) => val(g.key, j)).concat(rest.length ? [rest.reduce((a, j) => a + val(g.key, j), 0)] : []) })) };
    }

    function traces() {
      const { labels, keys, cols } = columns();
      const paidMonths = state.view === "monthly" ? keys.map((d) => data.paid[d] !== undefined) : keys.map(() => false);
      const tr = cols.map((g) => ({
        type: "bar", name: g.label, x: labels, y: g.values,
        marker: { color: groupColor(g.color), opacity: paidMonths.map((p) => (p ? 0.4 : 1)), line: { width: 0 } },
        _lightColors: labels.map(() => GROUP_LIGHT[g.color] || "#B0B7BD"),
        hovertemplate: `${g.label}: <b>%{y:,.0f}</b><extra></extra>`,
      }));
      if (state.view === "monthly" && state.part === "total" && paidMonths.some(Boolean)) {
        const ink = cssVar("--ink");
        tr.push({ type: "scatter", mode: "markers", name: "Actually paid (all debt)", x: labels.filter((_, j) => paidMonths[j]),
          y: keys.filter((_, j) => paidMonths[j]).map((d) => data.paid[d]),
          marker: { color: ink, size: 9, symbol: "diamond", line: { color: cssVar("--surface"), width: 1 } }, _light: "#36454F",
          hovertemplate: `Actually paid: <b>%{y:,.0f}</b><extra></extra>` });
      }
      return tr;
    }

    function draw() {
      if (!data) return;
      const { labels, cols } = columns();
      const legend = { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } };
      Plotly.react(chartEl, traces(), baseLayout({
        xaxis: { type: "category" }, yaxis: { tickformat: ",.0f" },
        extra: { barmode: "stack", bargap: 0.25, showlegend: true, legend, margin: { l: 64, r: 16, t: 60, b: 48 } },
      }), PLOT_CONFIG);
      // Next twelve months not yet elapsed: totals by group, and what needs dollars.
      const M = data.monthly;
      const ahead = M.dates.map((d, j) => j).filter((j) => data.paid[M.dates[j]] === undefined).slice(0, 12);
      const tot = (k, part) => ahead.reduce((a, j) => a + (part === "capital" ? M.capital[k][j] : part === "interest" ? M.interest[k][j] : M.capital[k][j] + M.interest[k][j]), 0);
      const rows = data.groups.map((g) => [g.label, tot(g.key, "capital"), tot(g.key, "interest"), tot(g.key, "total")]);
      const sum = (c) => rows.reduce((a, r) => a + r[c], 0);
      const fxNeed = ["fx_bonds", "imf", "ifis"].reduce((a, k) => a + tot(k, "total"), 0);
      const span = ahead.length ? `${fmtShortMonth(M.dates[ahead[0]])} - ${fmtShortMonth(M.dates[ahead[ahead.length - 1]])}` : "";
      sumEl.replaceChildren(h("table", { class: "data sched" },
        h("thead", {}, h("tr", {}, h("th", {}, `Next ${ahead.length} months (${span}), US$ m`), h("th", {}, "Capital"), h("th", {}, "Interest"), h("th", {}, "Total"))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "pres" }, r[0]), h("td", {}, fmt(r[1])), h("td", {}, fmt(r[2])), h("td", {}, fmt(r[3])))),
          h("tr", { class: "total" }, h("td", { class: "pres" }, h("b", {}, "Total")), h("td", {}, fmt(sum(1))), h("td", {}, fmt(sum(2))), h("td", {}, h("b", {}, fmt(sum(3))))))));
      hint.textContent = `As of ${fmtDay(data.as_of)}, millions of US dollars at that date's exchange rates. ` +
        `Next ${ahead.length} months: ${fmt(sum(3))} in all, of which ${fmt(fxNeed)} in foreign-currency bonds, the IMF and other multilaterals.` +
        (state.view === "monthly" && Object.keys(data.paid).length ? " Faded bars: months already past; the diamond is what was actually paid." : "");
      view = { header: [state.view === "monthly" ? "month" : "year", ...cols.map((g) => g.label), "total"],
        rows: labels.map((l, j) => [state.view === "monthly" ? M.dates[j] : l, ...cols.map((g) => +g.values[j].toFixed(1)), +cols.reduce((a, g) => a + g.values[j], 0).toFixed(1)]) };
    }
    const partWord = () => (state.part === "total" ? "Capital and interest" : state.part === "capital" ? "Capital" : "Interest");
    const onPNG = () => exportPNG(ind, traces(), `${partWord()} due, millions of US dollars, as of ${fmtDay(data.as_of)}`, { suffix: "", signed: false },
      `${ind.id}_${state.view}_${state.part}.png`, { barmode: "stack", xcategory: true });
    const onCSV = () => { if (view) downloadBlob(toCSV(view.header, view.rows), `${ind.id}_${state.view}_${state.part}.csv`, "text/csv"); };
    appendAll(card, h("div", { class: "controls" }, vSeg, h("span", { class: "spacer" }), pSeg), hint, chartEl, sumEl,
      ind.note ? h("p", { class: "chips-note" }, ind.note) : null,
      h("div", { class: "card-foot" },
        h("span", {}, "Source: ", T.source_url ? h("a", { href: T.source_url, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label,
          ` · Report as of: ${ind.last_obs ? fmtDay(ind.last_obs) : "–"}`),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: onPNG, title: "Download this chart as a 1200×800 PNG" }, "PNG"),
          h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download this view as CSV" }, "CSV"))));
    card.draw = async () => {
      if (!data) {
        try { data = await getJSON(`${DATA}${T.path}${T.file}`); } catch (e) { console.error(e); chartEl.replaceChildren(h("div", { class: "error-box" }, "The maturity profile could not be loaded.")); return; }
      }
      draw();
    };
    return card;
  }

  // ---------- navbar: a grouped "Dashboards" menu, then Methodology and About ----------
  function buildNav(nav, manifest, pageHref) {
    const topics = manifest.topics.filter((t) => manifest.indicators.some((m) => m.topic === t.id));
    const groups = [];
    for (const t of topics) {
      const name = t.group || tr("Dashboards");
      let g = groups.find((x) => x.name === name);
      if (!g) { g = { name, topics: [] }; groups.push(g); }
      g.topics.push(t);
    }
    const current = topics.find((t) => t.id === PAGE);
    const panel = h("div", { class: "nav-panel", id: "nav-panel", hidden: true },
      groups.map((g) => h("div", { class: "nav-group" }, h("div", { class: "nav-group-title" }, g.name),
        g.topics.map((t) => h("a", { href: pageHref(t.id), "aria-current": t.id === PAGE ? "page" : null }, t.title)))));
    const trigger = h("button", { class: "nav-trigger" + (current ? " is-current" : ""), type: "button", "aria-expanded": "false", "aria-controls": "nav-panel" },
      current ? current.title : tr("Dashboards"), h("span", { class: "caret", "aria-hidden": "true" }, "▾"));
    const menu = h("div", { class: "nav-menu" }, trigger, panel);
    const links = () => [...panel.querySelectorAll("a")];
    const open = (focusFirst) => {
      panel.hidden = false; trigger.setAttribute("aria-expanded", "true");
      if (focusFirst) (links().find((a) => a.getAttribute("aria-current")) || links()[0]).focus();
    };
    const close = (refocus) => {
      if (panel.hidden) return;
      panel.hidden = true; trigger.setAttribute("aria-expanded", "false");
      if (refocus) trigger.focus();
    };
    trigger.addEventListener("click", () => (panel.hidden ? open(false) : close(false)));
    trigger.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); open(true); } });
    panel.addEventListener("keydown", (e) => {
      const l = links(), i = l.indexOf(document.activeElement);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); l[(i + (e.key === "ArrowDown" ? 1 : l.length - 1)) % l.length].focus(); }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(menu.contains(document.activeElement)); });
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) close(false); });
    menu.addEventListener("focusout", (e) => { if (e.relatedTarget && !menu.contains(e.relatedTarget)) close(false); });
    nav.append(menu,
      h("a", { href: `${ROOT}methodology/`, "aria-current": PAGE === "methodology" ? "page" : null }, tr("Methodology")),
      h("a", { href: `${ROOT}about/`, "aria-current": PAGE === "about" ? "page" : null }, tr("About")));
  }

  // ---------- card: a statement (balance of payments, investment position) as a table ----------
  // Flows: a quarter, the last four quarters, the year to date or a calendar year, against
  // the same period a year earlier. Stocks: a quarter-end against a quarter and a year earlier.
  function statementCard(ind) {
    const S = ind.statement || {};
    const card = cardFrame(ind, true);
    const D = ind.dates;
    const V = (k) => (ind.variants[k] ? ind.variants[k].values : []);
    const last = (() => { for (let i = D.length - 1; i >= 0; i--) if (V(S.rows.find((r) => r.key).key)[i] !== null) return i; return D.length - 1; })();
    const qLabel = (i) => fmtPeriod(D[i], "Q");
    const state = { mode: S.stock ? "stock" : "q", end: last };
    const fmt = (v) => (v === null || v === undefined || Number.isNaN(v) ? "–" : (v < 0 ? "−" : "") + Math.abs(Math.round(v)).toLocaleString(LOCALE));
    const fmtD = (v) => (v === null || v === undefined || Number.isNaN(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(Math.round(v)).toLocaleString(LOCALE));
    // Sum of quarters i0..i1 (inclusive) of a row; null if any is missing.
    const sum = (k, i0, i1) => { if (i0 < 0) return null; let a = 0; for (let i = i0; i <= i1; i++) { const v = V(k)[i]; if (v === null || v === undefined) return null; a += v; } return a; };
    const quarterNo = (i) => Math.floor((Number(D[i].slice(5, 7)) - 1) / 3) + 1;
    // Period [i0, i1] for the mode and its end quarter; and its label.
    function period(end) {
      if (state.mode === "q" || state.mode === "stock") return [end, end, qLabel(end)];
      if (state.mode === "l4") return [end - 3, end, `4 quarters to ${qLabel(end)}`];
      if (state.mode === "ytd") { const n = quarterNo(end); return [end - n + 1, end, n === 4 ? D[end].slice(0, 4) : `${D[end].slice(0, 4)} to Q${n}`]; }
      return [end - 3, end, D[end].slice(0, 4)];   // calendar year: end is a Q4
    }
    const endChoices = () => D.map((d, i) => i).filter((i) => i <= last && (state.mode !== "year" || quarterNo(i) === 4) && (state.mode === "q" || state.mode === "stock" || i >= 3));
    const modeSeg = S.stock ? null : segmented([["q", "Quarter"], ["l4", "Last 4 quarters"], ["ytd", "Year to date"], ["year", "Calendar year"]], state.mode, (k) => {
      state.mode = k; modeSeg.update(k);
      const ok = endChoices(); if (!ok.includes(state.end)) state.end = ok[ok.length - 1];
      fillSel(); draw();
    }, "Period");
    const sel = h("select", { "aria-label": "Period" });
    sel.addEventListener("change", () => { state.end = Number(sel.value); draw(); });
    function fillSel() {
      sel.replaceChildren(...endChoices().slice().reverse().map((i) => h("option", { value: i, selected: i === state.end ? true : null },
        state.mode === "year" ? D[i].slice(0, 4) : qLabel(i))));
    }
    const tableEl = h("div", { class: "table-scroll flat" });
    const hint = h("p", { class: "hint" });
    let view = null;
    function draw() {
      const [i0, i1, lab] = period(state.end);
      let cols;   // [{label, get(key)}]
      if (S.stock) {
        cols = [
          { label: qLabel(i1), get: (k) => V(k)[i1] },
          { label: i1 >= 1 ? qLabel(i1 - 1) : "–", get: (k) => (i1 >= 1 ? V(k)[i1 - 1] : null) },
          { label: i1 >= 4 ? qLabel(i1 - 4) : "–", get: (k) => (i1 >= 4 ? V(k)[i1 - 4] : null) },
        ];
        cols.push({ label: "Change, quarter", delta: true, get: (k) => (cols[0].get(k) !== null && cols[1].get(k) !== null && cols[1].get(k) !== undefined ? cols[0].get(k) - cols[1].get(k) : null) });
        cols.push({ label: "Change, year", delta: true, get: (k) => (cols[0].get(k) !== null && cols[2].get(k) !== null && cols[2].get(k) !== undefined ? cols[0].get(k) - cols[2].get(k) : null) });
      } else {
        const prevLab = period(state.end - 4)[2];
        cols = [
          { label: lab, get: (k) => sum(k, i0, i1) },
          { label: state.end >= 4 ? prevLab : "–", get: (k) => sum(k, i0 - 4, i1 - 4) },
        ];
        cols.push({ label: "Change", delta: true, get: (k) => { const a = cols[0].get(k), b = cols[1].get(k); return a !== null && b !== null ? a - b : null; } });
      }
      const rows = S.rows.map((r) => {
        if (!r.key) return h("tr", { class: "st-head" + (r.rule ? " rule" : "") }, h("td", { class: "pres", colspan: cols.length + 1 }, r.heading));
        const label = ind.variants[r.key] ? ind.variants[r.key].label : r.key;
        const cls = [r.bold ? "st-bold" : "", r.rule ? "rule" : ""].join(" ").trim();
        return h("tr", { class: cls || null },
          h("td", { class: "pres", style: `padding-left:${10 + 18 * (r.indent || 0)}px` }, label),
          ...cols.map((c) => { const v = c.get(r.key); return h("td", { class: c.delta ? "st-delta" : null }, c.delta ? fmtD(v) : fmt(v)); }));
      });
      tableEl.replaceChildren(h("table", { class: "data statement" },
        h("thead", {}, h("tr", {}, h("th", {}, ind.units), ...cols.map((c) => h("th", {}, c.label)))), h("tbody", {}, rows)));
      hint.textContent = S.stock ? `End-of-quarter positions; latest: ${qLabel(last)}.`
        : `Flows over the period, against the same period a year earlier. Latest quarter: ${qLabel(last)}.`;
      view = { header: ["item", ...cols.map((c) => c.label)],
        rows: S.rows.filter((r) => r.key).map((r) => [ind.variants[r.key].label, ...cols.map((c) => { const v = c.get(r.key); return v === null || v === undefined ? "" : +v.toFixed(1); })]) };
    }
    const onCSV = () => { if (view) downloadBlob(toCSV(view.header, view.rows), `${ind.id}_${state.mode}_${D[state.end]}.csv`, "text/csv"); };
    fillSel();
    appendAll(card, h("div", { class: "controls" }, modeSeg, h("span", { class: "spacer" }), h("label", { class: "inline-select" }, S.stock ? "Quarter: " : "Ending: ", sel)),
      hint, tableEl, ind.note ? h("p", { class: "chips-note" }, ind.note) : null, methodLine(ind),
      h("div", { class: "card-foot" },
        h("span", {}, `Source: ${ind.source_label} · Latest: ${qLabel(last)}`),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download this view as CSV" }, "CSV"))));
    card.draw = () => draw();
    return card;
  }

  // ---------- card: real exchange rate calculator ----------
  // The rate that would put the real exchange rate index back at a reference level:
  // E* = E_today x RER_ref / RER_today (a higher index is a weaker peso).
  function rerCalcCard(ind) {
    const card = cardFrame(ind, true);
    const D = ind.dates, V = (k) => ind.variants[k].values;
    const weekday = D.map((d) => { const w = new Date(d + "T00:00:00Z").getUTCDay(); return w !== 0 && w !== 6; });
    const lastIdx = (k) => { const v = V(k); for (let i = v.length - 1; i >= 0; i--) if (v[i] !== null) return i; return -1; };
    const state = { mode: "m", ref: "avg2003", date: "2015-12-17", calc: "one", dA: "2015-12-17", dB: D[D.length - 1], alpha: 0.5 };
    const REFS = [
      ["avg1997", "Average since 1997"], ["avg2003", "Average since 2003 (after convertibility)"], ["avg2016", "Average since 2016"],
      ["med2003", "Median since 2003"], ["d20151217", "17 Dec 2015 (exchange-rate unification)"], ["date", "A date of your choice…"],
    ];
    const modeSeg = segmented([["m", "Official rate"], ["mkt", "CCL (market) rate"]], state.mode, (k) => { state.mode = k; modeSeg.update(k); draw(); }, "Exchange rate");
    const refSel = h("select", { "aria-label": "Reference" }, REFS.map(([k, l]) => h("option", { value: k, selected: k === state.ref ? true : null }, l)));
    const dateIn = h("input", { type: "date", value: state.date, min: D[0], max: D[D.length - 1], "aria-label": "Reference date", hidden: true });
    refSel.addEventListener("change", () => { state.ref = refSel.value; dateIn.hidden = state.ref !== "date"; draw(); });
    dateIn.addEventListener("change", () => { if (dateIn.value) { state.date = dateIn.value; draw(); } });
    const calcSeg = segmented([["one", "Restore a reference"], ["two", "Two equilibrium dates"]], state.calc, (c) => {
      state.calc = c; calcSeg.update(c); refWrap.hidden = c !== "one"; twoWrap.hidden = c !== "two"; draw();
    }, "Question");
    const inA = h("input", { type: "date", value: state.dA, min: D[0], max: D[D.length - 1], "aria-label": "First equilibrium date" });
    const inB = h("input", { type: "date", value: state.dB, min: D[0], max: D[D.length - 1], "aria-label": "Second equilibrium date" });
    inA.addEventListener("change", () => { if (inA.value) { state.dA = inA.value; draw(); } });
    inB.addEventListener("change", () => { if (inB.value) { state.dB = inB.value; draw(); } });
    const aOut = h("b", {}, fx(state.alpha, 2));
    const aIn = h("input", { type: "range", min: "0.3", max: "0.7", step: "0.05", value: String(state.alpha), "aria-label": "Weight of non-tradables (alpha)" });
    aIn.addEventListener("input", () => { state.alpha = Number(aIn.value); aOut.textContent = fx(state.alpha, 2); draw(); });
    const outEl = h("div", { class: "rer-out" });
    const chartEl = h("div", { class: "chart", role: "img", "aria-label": "Real exchange rate and the reference" });
    const fmtR = (v, d = 0) => (v === null || v === undefined || isNaN(v) ? "–" : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d }));
    const pct = (v) => (v === null || isNaN(v) ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${fx(Math.abs(v), 1)}%`);
    const sample = (k, from) => V(k).map((v, i) => [D[i], v]).filter(([d, v], i) => v !== null && weekday[i] && d >= from).map(([, v]) => v);
    const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
    const quant = (a, q) => { const b = [...a].sort((x, y) => x - y); const p = (b.length - 1) * q, lo = Math.floor(p); return b[lo] + (b[Math.min(lo + 1, b.length - 1)] - b[lo]) * (p - lo); };
    function at(k, day) { let j = -1; for (let i = 0; i < D.length && D[i] <= day; i++) if (V(k)[i] !== null) j = i; return j; }
    function drawTwo() {
      const k = state.mode, a = Math.min(at(k, state.dA), at(k, state.dB)), b = Math.max(at(k, state.dA), at(k, state.dB));
      if (a < 0 || b < 0 || a === b) { outEl.replaceChildren(h("p", { class: "hint" }, "Pick two different dates with data.")); return null; }
      const rA = V(k)[a], rB = V(k)[b], dln = Math.log(rB / rA), al = state.alpha;
      const years = (isoMs(D[b]) - isoMs(D[a])) / (365.25 * dayMs);
      const g = -dln / al, total = 100 * (Math.exp(g) - 1), perYear = 100 * (Math.exp(g / years) - 1);
      const tA = at("tot", D[a]), tB = at("tot", D[b]);
      const totCh = tA >= 0 && tB >= 0 && tA !== tB ? 100 * (V("tot")[tB] / V("tot")[tA] - 1) : null;
      const rer = 100 * (rB / rA - 1);
      outEl.replaceChildren(
        h("div", { class: "rer-grid" },
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, "Real exchange rate"), h("div", { class: "rer-v" }, `${fmtR(rA, 1)} → ${fmtR(rB, 1)}`),
            h("div", { class: "rer-s" }, `${fmtDay(D[a])} to ${fmtDay(D[b])}: ${pct(rer)} (${rer < 0 ? "real appreciation" : "real depreciation"})`)),
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, "Required change in relative productivity, total"), h("div", { class: "rer-v" }, pct(total)),
            h("div", { class: "rer-s" }, `over ${fx(years, 1)} years, with α = ${fx(al, 2)}`)),
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, "Required change, per year"), h("div", { class: "rer-v" }, pct(perYear)),
            h("div", { class: "rer-s" }, "compounded annual rate")),
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, "Terms of trade over the same span"), h("div", { class: "rer-v" }, totCh === null ? "–" : pct(totCh)),
            h("div", { class: "rer-s" }, totCh === null ? "no quarterly data for these dates" : `${fmtPeriod(D[tA], "Q")} to ${fmtPeriod(D[tB], "Q")} (INDEC); a rise also supports a stronger peso`))),
        h("p", { class: "hint" }, `If both dates were equilibria, Argentina's productivity in tradables relative to non-tradables, compared with its trading partners, must have ${g >= 0 ? "risen" : "fallen"} ${pct(Math.abs(total)).replace("+", "")} for the real exchange rate to move ${pct(rer)} (Balassa–Samuelson: Δln RER = −α × Δln relative productivity). Everything is attributed to productivity: other fundamentals, such as the terms of trade, would reduce or add to it.`));
      return { a, b };
    }
    function refValue(k) {
      const r = state.ref;
      if (r === "avg1997") return { v: mean(sample(k, "1997-01-01")), label: "average since 1997" };
      if (r === "avg2003") return { v: mean(sample(k, "2003-01-01")), label: "average since 2003" };
      if (r === "avg2016") return { v: mean(sample(k, "2016-01-01")), label: "average since 2016" };
      if (r === "med2003") return { v: quant(sample(k, "2003-01-01"), 0.5), label: "median since 2003" };
      const day = r === "d20151217" ? "2015-12-17" : state.date;
      // The last value on or before the chosen day.
      let j = -1; for (let i = 0; i < D.length && D[i] <= day; i++) if (V(k)[i] !== null) j = i;
      return j < 0 ? { v: null, label: fmtDay(day) } : { v: V(k)[j], label: fmtDay(D[j]), date: D[j] };
    }
    function draw() {
      if (state.calc === "two") return drawTwoChart(drawTwo());
      const k = state.mode, fxk = k === "m" ? "ofx" : "ccl", fxName = k === "m" ? "official (wholesale) rate" : "CCL rate";
      const i = lastIdx(k), jf = lastIdx(fxk);
      const now = V(k)[i], fxNow = V(fxk)[jf];
      const ref = refValue(k);
      const s03 = sample(k, "2003-01-01"), p25 = quant(s03, 0.25), p75 = quant(s03, 0.75);
      const implied = ref.v ? fxNow * ref.v / now : null;
      const lo = fxNow * p25 / now, hi = fxNow * p75 / now;
      outEl.replaceChildren(
        h("div", { class: "rer-grid" },
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, `Real exchange rate, ${fmtDay(D[i])}`), h("div", { class: "rer-v" }, fmtR(now, 1)),
            h("div", { class: "rer-s" }, `${tr("Reference")} (${tr(ref.label)}): ${fmtR(ref.v, 1)}`)),
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, "Index against the reference"), h("div", { class: "rer-v" }, ref.v ? pct(100 * (now / ref.v - 1)) : "–"),
            h("div", { class: "rer-s" }, ref.v ? (now < ref.v ? "below it: the peso is stronger in real terms" : "above it: the peso is weaker in real terms") : "")),
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, `${k === "m" ? "Official" : "CCL"} rate that restores the reference`), h("div", { class: "rer-v" }, fmtR(implied)),
            h("div", { class: "rer-s" }, `Actual ${fxName} ${fmtR(fxNow, 2)} on ${fmtDay(D[jf])}${implied ? `: ${pct(100 * (implied / fxNow - 1))} away` : ""}`)),
          h("div", { class: "rer-tile" }, h("div", { class: "rer-k" }, "Range: middle half of the index since 2003"), h("div", { class: "rer-v" }, `${fmtR(lo)} – ${fmtR(hi)}`),
            h("div", { class: "rer-s" }, `Index between ${fmtR(p25, 1)} (25th percentile) and ${fmtR(p75, 1)} (75th)`))),
        h("p", { class: "hint" }, `Today's ${fxName} × reference index / today's index. ${k === "mkt" ? "CCL mode: the index at the CCL rate (the official index before November 2011, when there were no controls), applied to the CCL rate today." : ""}`));
      // Weekdays with data; a gap of more than a month (no CCL series, Nov 2011 - Dec 2012) breaks the line.
      const x = [], y = [];
      D.forEach((d, n) => {
        if (V(k)[n] === null || !weekday[n]) return;
        if (x.length && isoMs(d) - isoMs(x[x.length - 1]) > 31 * dayMs) { x.push(d); y.push(null); }
        x.push(d); y.push(V(k)[n]);
      });
      const ink3 = cssVar("--ink-3"), accent = cssVar(SERIES_VARS[0]);
      const tr = [{ type: "scatter", mode: "lines", name: ind.variants[k].label, x, y, line: { color: accent, width: 1.6 }, _slot: 0,
        hovertemplate: "%{x|%d %b %Y}: <b>%{y:.1f}</b><extra></extra>" }];
      if (ref.date) tr.push({ type: "scatter", mode: "markers", name: "Reference date", x: [ref.date], y: [ref.v], marker: { color: cssVar("--ink"), size: 9, symbol: "diamond" }, _light: "#36454F", hovertemplate: `Reference: <b>%{y:.1f}</b><extra></extra>` });
      const shapes = [
        { type: "rect", xref: "paper", x0: 0, x1: 1, y0: p25, y1: p75, fillcolor: isDark() ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)", line: { width: 0 }, layer: "below", _lightFill: "rgba(0,0,0,0.05)" },
        ...(ref.v ? [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: ref.v, y1: ref.v, line: { color: ink3, width: 1.5, dash: "dash" } }] : []),
      ];
      Plotly.react(chartEl, tr, baseLayout({ yaxis: { tickformat: ".0f" }, extra: { shapes, showlegend: false, hovermode: "closest", margin: { l: 48, r: 16, t: 10, b: 36 } } }), PLOT_CONFIG);
    }
    function lineXY(k) {
      const x = [], y = [];
      D.forEach((d, n) => {
        if (V(k)[n] === null || !weekday[n]) return;
        if (x.length && isoMs(d) - isoMs(x[x.length - 1]) > 31 * dayMs) { x.push(d); y.push(null); }
        x.push(d); y.push(V(k)[n]);
      });
      return { x, y };
    }
    function drawTwoChart(sel) {
      const k = state.mode, { x, y } = lineXY(k);
      const tr = [{ type: "scatter", mode: "lines", name: ind.variants[k].label, x, y, line: { color: cssVar(SERIES_VARS[0]), width: 1.6 },
        hovertemplate: "%{x|%d %b %Y}: <b>%{y:.1f}</b><extra></extra>" }];
      if (sel) tr.push({ type: "scatter", mode: "markers+text", name: "Equilibrium dates", x: [D[sel.a], D[sel.b]], y: [V(k)[sel.a], V(k)[sel.b]],
        text: ["A", "B"], textposition: "top center", textfont: { color: cssVar("--ink") },
        marker: { color: cssVar("--ink"), size: 10, symbol: "diamond" }, hovertemplate: "%{x|%d %b %Y}: <b>%{y:.1f}</b><extra></extra>" });
      Plotly.react(chartEl, tr, baseLayout({ yaxis: { tickformat: ".0f" }, extra: { showlegend: false, hovermode: "closest", margin: { l: 48, r: 16, t: 10, b: 36 } } }), PLOT_CONFIG);
    }
    const caveats = h("div", { class: "chips-note rer-caveats" },
      h("p", {}, h("b", {}, "Read with care. "), "This is relative purchasing-power parity: it assumes the reference was an equilibrium and that nothing fundamental has changed since. Both are strong assumptions. ",
        "The terms of trade are about 45% above their 2004 level and energy has turned from a deficit into a surplus, changes that support a stronger peso than history suggests; productivity growth relative to partners (the Balassa–Samuelson effect) works the same way. Single dates can be overshoots: the index spiked after the 2002 and December 2023 devaluations, and was at its strongest at the end of convertibility in 2001. The range across reference choices is the honest answer, not any one number."),
      h("p", {}, "Model-based assessments (the IMF's External Balance Assessment) estimate the real exchange rate consistent with fundamentals and desirable policies. The IMF's May 2026 staff report judges Argentina's external position \"weaker than the level implied by medium-term fundamentals and desirable policies\", a preliminary assessment subject to exceptionally high uncertainty. ",
        h("a", { href: "https://www.imf.org/en/publications/cr/issues/2026/05/22/argentina-2026-article-iv-consultation-second-review-under-the-extended-arrangement-under-576253", target: "_blank", rel: "noopener" }, "IMF Country Report, May 2026"), "."));
    const refWrap = h("label", { class: "inline-select" }, "Reference: ", refSel, dateIn);
    const twoWrap = h("div", { class: "inline-select rer-two", hidden: true },
      h("label", {}, "A: ", inA), h("label", {}, "B: ", inB),
      h("label", { title: "Weight of non-tradables in the price index" }, "α: ", aIn, aOut));
    appendAll(card, h("div", { class: "controls" }, calcSeg, modeSeg, h("span", { class: "spacer" }), refWrap, twoWrap),
      outEl, chartEl, h("p", { class: "hint rer-legend" }, "Restore a reference: shaded is the middle half of the index since 2003 (25th to 75th percentile), dashed is the reference. Two dates: A and B are marked."),
      caveats, methodLine(ind),
      h("div", { class: "card-foot" }, h("span", {}, `Source: ${ind.source_label} · Latest: ${fmtDay(ind.last_obs)}`)));
    card.draw = () => draw();
    return card;
  }

  // ---------- card: dollar futures curve ----------
  function curveCard(ind) {
    const T = ind.table;
    const card = cardFrame(ind, true);
    let data = null, view = null;
    const chartEl = h("div", { class: "chart tall-ish", role: "img", "aria-label": "Dollar futures curve" });
    const hint = h("p", { class: "hint" });
    const sumEl = h("div", { class: "table-scroll flat" });
    const fmt = (v, d = 0) => (v === null || v === undefined || isNaN(v) ? "–" : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d }));
    const pct = (v) => (v === null || v === undefined || isNaN(v) ? "–" : `${v >= 0 ? "" : "−"}${fx(Math.abs(v), 1)}%`);
    const NAMES = ["Today", "A month ago", "Three months ago"];
    const DASH = ["solid", "dash", "dot"];

    function traces() {
      const ink = cssVar("--ink"), ink3 = cssVar("--ink-3");
      const tr = data.curves.map((c, i) => ({
        type: "scatter", mode: "lines+markers", name: `${NAMES[i] || c.date} (${fmtDay(c.date)})`,
        x: c.contracts.map((k) => k.expiry), y: c.contracts.map((k) => k.settlement),
        line: { color: cssVar(SERIES_VARS[0]), width: i ? 1.8 : 2.8, dash: DASH[i] }, marker: { size: i ? 4 : 6, color: cssVar(SERIES_VARS[0]) },
        _slot: 0, opacity: i ? 0.75 : 1,
        hovertemplate: `${NAMES[i] || c.date}: <b>%{y:,.2f}</b><extra></extra>`,
      }));
      const B = data.band || {};
      [["ceiling", "Band ceiling, projected"]].forEach(([k, label]) => {
        if (!B[k]) return;
        tr.push({ type: "scatter", mode: "lines", name: label, x: [B[k].date, ...B[k].projected.map((p) => p.expiry)], y: [B[k].value, ...B[k].projected.map((p) => p.value)],
          line: { color: ink3, width: 1.5, dash: "dash" }, _light: "#85909A", hovertemplate: `${label}: <b>%{y:,.0f}</b><extra></extra>` });
      });
      // REM survey: expected monthly average of the official rate (median, middle half as error bars), mid-month.
      const R = ind.forecast && ind.forecast.items[0];
      if (R) {
        const t0 = data.curves[0].date, pts = R.points.filter((q) => q.date.slice(0, 8) + "15" > t0.slice(0, 8) + "00" && q.med !== null);
        if (pts.length) tr.push({ type: "scatter", mode: "lines+markers", name: `REM survey, ${fmtMonth(ind.forecast.survey)} (monthly average, median)`,
          x: pts.map((q) => q.date.slice(0, 8) + "15"), y: pts.map((q) => q.med), line: { color: cssVar(SERIES_VARS[1]), width: 1.8, dash: "dash" },
          marker: { size: 6, symbol: "square", color: cssVar(SERIES_VARS[1]) }, _slot: 1,
          error_y: { type: "data", symmetric: false, array: pts.map((q) => (q.p75 !== null ? q.p75 - q.med : 0)), arrayminus: pts.map((q) => (q.p25 !== null ? q.med - q.p25 : 0)),
            color: cssVar(SERIES_VARS[1]), thickness: 1, width: 3 },
          hovertemplate: `REM, %{x|%b %Y} average: <b>%{y:,.0f}</b><extra></extra>` });
      }
      if (data.spot) tr.push({ type: "scatter", mode: "markers", name: `Official rate (${fmtDay(data.spot.date)})`, x: [data.spot.date], y: [data.spot.value],
        marker: { color: ink, size: 10, symbol: "diamond", line: { color: cssVar("--surface"), width: 1 } }, _light: "#36454F",
        hovertemplate: `Official rate: <b>%{y:,.2f}</b><extra></extra>` });
      return tr;
    }

    function draw() {
      if (!data) return;
      const legend = { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } };
      Plotly.react(chartEl, traces(), baseLayout({
        yaxis: { tickformat: ",.0f" }, xaxis: { hoverformat: "%b %Y" },
        extra: { showlegend: true, legend, hovermode: "closest", margin: { l: 64, r: 16, t: 60, b: 40 } },
      }), PLOT_CONFIG);
      const C = data.curves[0], spot = data.spot ? data.spot.value : null, t0 = new Date(C.date);
      const ceil = {}; ((data.band || {}).ceiling ? data.band.ceiling.projected : []).forEach((p) => { ceil[p.expiry] = p.value; });
      const rows = C.contracts.map((k) => {
        const days = (new Date(k.expiry) - t0) / 864e5;
        const dev = spot ? 100 * (k.settlement / spot - 1) : null;
        const ann = spot && days > 20 ? 100 * (Math.pow(k.settlement / spot, 365 / days) - 1) : null;
        return { k, days, dev, ann, room: ceil[k.expiry] ? 100 * (ceil[k.expiry] / k.settlement - 1) : null };
      });
      sumEl.replaceChildren(h("table", { class: "data sched" },
        h("thead", {}, h("tr", {}, h("th", {}, "Contract (expiry)"), h("th", {}, "Price"), h("th", {}, "vs official rate"), h("th", {}, "Annualized"),
          h("th", {}, "Projected ceiling above price"), h("th", {}, "Open interest, US$ m"))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "pres" }, fmtShortMonth(r.k.expiry)), h("td", {}, fmt(r.k.settlement, 2)),
          h("td", {}, pct(r.dev)), h("td", {}, pct(r.ann)), h("td", {}, pct(r.room)), h("td", {}, fmt(r.k.oi / 1000)))))));
      const last = rows[rows.length - 1];
      hint.textContent = `Settlement prices on ${fmtDay(C.date)}` + (spot ? `; official wholesale rate ${fmt(spot, 2)}.` : ".") +
        (last && last.dev !== null ? ` The longest contract (${fmtShortMonth(last.k.expiry)}) prices a ${pct(last.dev)} rise in the official rate (${pct(last.ann)} a year).` : "") +
        (ind.forecast ? ` Squares: the monthly average expected in the BCRA's REM survey of ${fmtMonth(ind.forecast.survey)} (median; bars: middle half of forecasters); futures settle on the month's last business day, so they run slightly above a monthly average when the rate is rising.` : "") +
        ((data.band || {}).ceiling ? ` The grey dashed line extends the band's ceiling at the pace of its last three months (${fx((100 * data.band.ceiling.monthly_pace), 2)}% a month): an illustration, not an announced path.` : "");
      view = { header: ["curve_date", "symbol", "expiry", "settlement", "open_interest_contracts"],
        rows: data.curves.flatMap((c) => c.contracts.map((k) => [c.date, k.symbol, k.expiry, k.settlement, k.oi])) };
    }
    const onPNG = () => exportPNG(ind, traces(), `Settlement prices, pesos per US dollar, ${fmtDay(data.curves[0].date)}`, { suffix: "", signed: false },
      `${ind.id}.png`, {});
    const onCSV = () => { if (view) downloadBlob(toCSV(view.header, view.rows), `${ind.id}.csv`, "text/csv"); };
    appendAll(card, hint, chartEl, sumEl,
      ind.note ? h("p", { class: "chips-note" }, ind.note) : null, methodLine(ind),
      h("div", { class: "card-foot" },
        h("span", {}, "Source: ", T.source_url ? h("a", { href: T.source_url, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label,
          ` · Latest: ${ind.last_obs ? fmtDay(ind.last_obs) : "–"}`),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: onPNG, title: "Download this chart as a 1200×800 PNG" }, "PNG"),
          h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download the curves as CSV" }, "CSV"))));
    card.draw = async () => {
      if (!data) {
        try { data = await getJSON(`${DATA}${T.path}${T.file}`); } catch (e) { console.error(e); chartEl.replaceChildren(h("div", { class: "error-box" }, "The futures curve could not be loaded.")); return; }
      }
      draw();
    };
    return card;
  }

  // ---------- card: tenure of officials (BCRA presidents, ministers of the economy) ----------
  function tenureCard(ind) {
    const T = ind.table;
    const card = cardFrame(ind, true);
    let data = null;
    const state = { range: "1983" };
    const chartEl = h("div", { class: "chart tall-ish", role: "img", "aria-label": `${ind.title} chart` });
    const hint = h("p", { class: "hint" });
    const keyEl = h("p", { class: "band-key" });
    const sumEl = h("div", { class: "table-scroll flat" });
    const rSeg = segmented([["1983", "Since 1983"], ["all", "All"]], state.range, (k) => { state.range = k; rSeg.update(k); draw(); }, "Period");
    const DAY = 864e5;
    const today = new Date().toISOString().slice(0, 10);
    const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / DAY);
    const fmtInt = (v) => Math.round(v).toLocaleString(LOCALE);
    const yrs = (d) => fx((d / 365.25), 1);
    const START_1983 = "1983-12-10";

    function rows() {
      return data.roles[T.role].rows.map((r, i) => {
        const p = termOf(r.start, "D");
        return { ...r, i, open: !r.end, days: daysBetween(r.start, r.end || today), pres: r.start >= START_1983 ? p : null };
      });
    }
    function view() { const all = rows(); return state.range === "1983" ? all.filter((r) => r.start >= START_1983) : all; }
    const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    const median = (xs) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
    const shortName = (n) => {
      const w = n.replace(/\s*\(.*?\)\s*/g, " ").trim().split(" ");
      let i = w.length - 1;
      while (i > 1 && /^(de|del|la|las|los|y)$/.test(w[i - 1])) i -= 2;   // "Marcó del Pont", "Martínez de Hoz"
      return w.slice(Math.max(1, i), w.length).join(" ");
    };

    function build(light) {
      const V = view();
      const many = V.length > 30;
      const colors = V.map((r) => (r.pres ? (light ? termLight(r.pres) : termColor(r.pres)) : light ? "#C9CED3" : cssVar("--ink-3")));
      const tr = [{
        type: "bar", x: V.map((r, j) => j), y: V.map((r) => r.days),
        marker: { color: colors, opacity: V.map((r) => (r.open ? 0.6 : 1)), pattern: { shape: V.map((r) => (r.open ? "/" : "")), fgcolor: light ? "#36454F" : cssVar("--ink"), size: 6 } },
        _lightColors: V.map((r) => (r.pres ? termLight(r.pres) : "#C9CED3")),
        text: V.map((r) => fmtInt(r.days)), textposition: "outside", cliponaxis: false, textangle: many ? -90 : 0,
        textfont: { size: many ? 9 : 11, color: light ? "#5A6872" : cssVar("--ink-2") },
        customdata: V.map((r) => [r.name, fmtDay(r.start), r.open ? "in office" : fmtDay(r.end), r.pres ? r.pres.short : "", yrs(r.days)]),
        hovertemplate: "<b>%{customdata[0]}</b><br>%{customdata[1]} – %{customdata[2]}<br>%{y:,} days (%{customdata[4]} years)<extra>%{customdata[3]}</extra>",
        showlegend: false,
      }];
      const done = V.filter((r) => !r.open).map((r) => r.days);
      const avg = mean(done);
      const shapes = avg === null ? [] : [{ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: avg, y1: avg, line: { color: light ? "#36454F" : cssVar("--ink"), width: 1.5, dash: "dash", _light: "#36454F" } }];
      const annotations = [];
      const xaxis = { type: "linear", tickmode: "array", tickvals: V.map((r, j) => j), ticktext: V.map((r) => (many ? shortName(r.name) : r.name)), tickangle: -60, tickfont: { size: many ? 9 : 11 }, range: [-0.7, V.length - 0.3] };
      return { V, tr, shapes, annotations, xaxis, avg, done };
    }

    function draw() {
      if (!data) return;
      const { V, tr, shapes, annotations, xaxis, avg, done } = build(false);
      Plotly.react(chartEl, tr, baseLayout({
        yaxis: { tickformat: ",.0f", title: { text: "Days in office", font: { size: 12 } } },
        extra: { xaxis, shapes, annotations, showlegend: false, bargap: 0.25, hovermode: "closest", margin: { l: 64, r: 16, t: 24, b: V.length > 30 ? 90 : 130 } },
      }), PLOT_CONFIG);
      // Party key (presidencies since 1983).
      const seen = new Map();
      V.forEach((r) => { if (r.pres && !seen.has(r.pres.party)) seen.set(r.pres.party, r.pres); });
      keyEl.replaceChildren(...[...seen.values()].map((p) => h("span", {}, h("span", { class: "band-sw", style: `background:${termColor(p)}` }), (PARTY_COLORS[p.party] || {}).label || p.party)),
        ...(V.some((r) => !r.pres) ? [h("span", {}, h("span", { class: "band-sw", style: `background:${cssVar("--ink-3")}` }),
          V.some((r) => !r.pres && r.start < START_1983) ? "Before December 1983" : "Interim presidents (December 2001)")] : []),
        h("span", {}, h("span", { class: "band-sw hatch" }), "In office (to today)"),
        avg !== null ? h("span", {}, h("span", { class: "band-sw dashline" }), `Average of completed tenures: ${fmtInt(avg)} days (${yrs(avg)} years)`) : null);
      // Summary line.
      const cur = V.find((r) => r.open);
      const role = data.roles[T.role].label;
      const plural = T.role === "bcra" ? "presidents of the BCRA" : "ministers of the economy";
      let msg = `${state.range === "1983" ? "Since December 1983" : `Since ${V[0].start.slice(0, 4)}`}: ${V.length} ${plural}` +
        (avg !== null ? `; completed tenures average ${fmtInt(avg)} days (${yrs(avg)} years), median ${fmtInt(median(done))}.` : ".");
      if (cur) {
        const longer = done.filter((d) => d < cur.days).length;
        msg += ` ${role} in office: ${cur.name}, ${fmtInt(cur.days)} days so far, longer than ${longer} of the ${done.length} completed tenures shown.`;
      }
      hint.textContent = msg;
      // By presidency (since 1983).
      const all = rows().filter((r) => r.pres);
      const byP = PRESIDENCIES.map((p) => {
        const rs = all.filter((r) => r.pres.id === p.id);
        const comp = rs.filter((r) => !r.open).map((r) => r.days);
        const long = rs.reduce((a, r) => (!a || r.days > a.days ? r : a), null);
        return { p, n: rs.length, avg: mean(comp), long, open: rs.some((r) => r.open) };
      }).filter((x) => x.n);
      sumEl.replaceChildren(h("table", { class: "data sched" },
        h("thead", {}, h("tr", {}, h("th", {}, "Appointed under"), h("th", {}, "Appointed"), h("th", {}, "Average tenure, days"), h("th", {}, "Longest"))),
        h("tbody", {}, byP.map((x) => h("tr", {}, h("td", { class: "pres" }, x.p.name), h("td", {}, String(x.n)),
          h("td", {}, x.avg === null ? "–" : fmtInt(x.avg)), h("td", {}, x.long ? `${x.long.name} (${fmtInt(x.long.days)}${x.long.open ? ", in office" : ""})` : "–"))))));
    }

    const onPNG = () => {
      const { V, tr, shapes, annotations, xaxis, avg } = build(true);
      exportPNG(ind, tr, `Days in office, ${state.range === "1983" ? "since December 1983" : `since ${V[0].start.slice(0, 4)}`}; dashed line: average of completed tenures (${fmtInt(avg)} days)`, { suffix: "", signed: false },
        `${ind.id}_${state.range}.png`, { shapes, annotations, xaxis: { ...xaxis, tickfont: { size: V.length > 30 ? 11 : 14 } }, margin: { b: 250 }, sourceY: -0.5 });
    };
    const onCSV = () => {
      const R = rows();
      downloadBlob(toCSV(["name", "start", "end", "days_in_office", "in_office", "appointed_under", "note"],
        R.map((r) => [r.name, r.start, r.end || "", r.days, r.open ? "yes" : "", r.pres ? r.pres.name : "", r.note || ""])), `${ind.id}.csv`, "text/csv");
    };
    appendAll(card, h("div", { class: "controls" }, h("span", { class: "spacer" }), rSeg), hint, methodLine(ind), keyEl, chartEl, sumEl,
      ind.note ? h("p", { class: "chips-note" }, ind.note) : null,
      h("div", { class: "card-foot" },
        h("span", {}, `Source: ${ind.source_label}`, data && data.latest ? ` · Latest change: ${fmtDay(data.latest)}` : ""),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: onPNG, title: "Download this chart as a 1200×800 PNG" }, "PNG"),
          h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download the full list as CSV" }, "CSV"))));
    card.draw = async () => {
      if (!data) {
        try { data = await getJSON(`${DATA}${T.path}${T.file}`); } catch (e) { console.error(e); chartEl.replaceChildren(h("div", { class: "error-box" }, "The list of officials could not be loaded.")); return; }
      }
      draw();
    };
    return card;
  }


  // ---------- card: REM forecasts for each calendar year, survey by survey ----------
  function remRevisionsCard(ind) {
    const T = ind.table;
    const card = cardFrame(ind, true);
    let data = null;
    const VARS = {
      cpi: { label: "Inflation (Dec/Dec)", units: "%", fmt: ",.1f", sfx: "%" },
      gdp: { label: "GDP growth", units: "%", fmt: ",.1f", sfx: "%" },
      fx: { label: "Exchange rate (Dec)", units: "Pesos per US dollar, December average", fmt: ",.0f", sfx: "", log: true },
      primary: { label: "Primary balance", units: "Billions of pesos", fmt: ",.0f", sfx: "" },
    };
    const state = { v: "cpi", range: "recent" };
    const chartEl = h("div", { class: "chart tall-ish", role: "img", "aria-label": `${ind.title} chart` });
    const hint = h("p", { class: "hint" });
    const vSeg = segmented(Object.entries(VARS).map(([k, v]) => [k, v.label]), state.v, (k) => { state.v = k; vSeg.update(k); draw(); }, "Variable");
    const rSeg = segmented([["recent", "Last 5 years"], ["all", "All"]], state.range, (k) => { state.range = k; rSeg.update(k); draw(); }, "Years");

    function build(light) {
      const V = VARS[state.v], D = data.vars[state.v] || {}, O = (ind.outcomes || {})[state.v] || {};
      let years = Object.keys(D).sort();
      if (state.range === "recent") years = years.slice(-5);
      const tr = [];
      years.forEach((y, n) => {
        const pts = D[y], slot = n % SERIES_VARS.length, c = light ? undefined : cssVar(SERIES_VARS[slot]);
        tr.push({ type: "scatter", mode: "lines+markers", name: y, x: pts.map((q) => q[0]), y: pts.map((q) => q[1]), line: { color: c, width: 2 }, marker: { size: 4, color: c }, _slot: slot,
          hovertemplate: `Survey of %{x|%b %Y}: <b>%{y:${V.fmt}}${V.sfx}</b><extra>${y}</extra>` });
        if (O[y] !== undefined) {
          const x0 = pts[0][0], x1 = pts[pts.length - 1][0];
          tr.push({ type: "scatter", mode: "lines+markers", name: `${y} actual`, showlegend: false, x: [x0, x1], y: [O[y], O[y]],
            line: { color: c, width: 1.2, dash: "dot" }, marker: { size: [0, 9], symbol: "star", color: c }, _slot: slot,
            hovertemplate: `Actual ${y}: <b>%{y:${V.fmt}}${V.sfx}</b><extra></extra>` });
        }
      });
      return { tr, V, years };
    }

    function draw() {
      if (!data) return;
      const { tr, V, years } = build(false);
      Plotly.react(chartEl, tr, baseLayout({
        yaxis: { tickformat: V.sfx === "%" ? ",.0f" : V.fmt, ticksuffix: V.sfx, ...(V.log ? { type: "log" } : {}), ...(V.sfx === "%" ? {} : { title: { text: V.units, font: { size: 11 } } }) },
        extra: { showlegend: true, legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } }, hovermode: "closest", margin: { l: 64, r: 16, t: 30, b: 36 } },
      }), PLOT_CONFIG);
      const O = (ind.outcomes || {})[state.v] || {}, D = data.vars[state.v] || {};
      const done = years.filter((y) => O[y] !== undefined);
      const msg = [`Each line is one year's forecast as it evolved; stars and dotted lines mark the outcome. Latest survey: ${fmtMonth(data.latest)}.`];
      if (done.length) {
        const y = done[done.length - 1], first = D[y][0];
        msg.push(` For ${y}, the first survey (${fmtShortMonth(first[0])}) expected ${first[1].toLocaleString(LOCALE, { maximumFractionDigits: 1 })}${V.sfx}; the outcome was ${O[y].toLocaleString(LOCALE, { maximumFractionDigits: 1 })}${V.sfx}.`);
      }
      if (V.log) msg.push(" Log scale.");
      hint.textContent = msg.join("");
    }
    const onPNG = () => { const { tr, V } = build(true);
      exportPNG(ind, tr, `${V.label}: REM median forecast for each year, by survey date; dotted: outcome`, { suffix: V.sfx, signed: false }, `${ind.id}_${state.v}.png`, { ylog: !!V.log }); };
    const onCSV = () => {
      const D = data.vars[state.v] || {}, O = (ind.outcomes || {})[state.v] || {};
      const rows = Object.keys(D).sort().flatMap((y) => D[y].map((q) => [y, q[0], q[1], q[2] ?? "", q[3] ?? "", O[y] ?? ""]));
      downloadBlob(toCSV(["target_year", "survey", "median", "p25", "p75", "outcome"], rows), `${ind.id}_${state.v}.csv`, "text/csv");
    };
    appendAll(card, h("div", { class: "controls" }, vSeg, h("span", { class: "spacer" }), rSeg), hint, methodLine(ind), chartEl,
      ind.note ? h("p", { class: "chips-note" }, ind.note) : null,
      h("div", { class: "card-foot" }, h("span", {}, `Source: ${ind.source_label} · Latest survey: ${ind.last_obs ? fmtMonth(ind.last_obs) : "–"}`),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: onPNG, title: "Download this chart as a 1200×800 PNG" }, "PNG"),
          h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download every survey's forecasts for this variable as CSV" }, "CSV"))));
    card.draw = async () => {
      if (!data) {
        try { data = await getJSON(`${DATA}${T.path}${T.file}`); } catch (e) { console.error(e); chartEl.replaceChildren(h("div", { class: "error-box" }, "The survey data could not be loaded.")); return; }
      }
      draw();
    };
    return card;
  }

  // ---------- card: Treasury placements, auction by auction ----------
  function placementsCard(ind) {
    const T = ind.table;
    const card = cardFrame(ind, true);
    let index = null;
    const cache = new Map();
    const load = (y) => { if (!cache.has(y)) cache.set(y, getJSON(`${DATA}${T.path}placements/${y}.json`)); return cache.get(y); };
    const state = { year: null, date: null };
    const ySel = h("select", { class: "select", "aria-label": "Year" });
    const dSel = h("select", { class: "select", "aria-label": "Settlement date" });
    ySel.addEventListener("change", () => { state.year = ySel.value; fillDates(); state.date = index.years[state.year].slice(-1)[0]; dSel.value = state.date; render(); });
    dSel.addEventListener("change", () => { state.date = dSel.value; render(); });
    const tableEl = h("div", { class: "table-scroll" });
    const hint = h("p", { class: "hint" });
    const n0 = (v) => (v === null || v === undefined ? "–" : Math.round(v).toLocaleString(LOCALE));
    const n1 = (v, d = 1) => (v === null || v === undefined ? "–" : v.toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d }));
    const KCOL = { fixed: "copper", cer: "gold", floating: "sage", dual: "lavender", dlinked: "teal", usd: "sky" };
    let view = null;
    function fillDates() {
      dSel.replaceChildren(...index.years[state.year].slice().reverse().map((d) => h("option", { value: d }, fmtDay(d))));
    }
    async function render() {
      const t = await load(state.year);
      const rows = t.auctions[state.date] || [];
      const head = h("tr", {}, ["Instrument", "Type", "Maturity", "Face value", "Cash value", "Price / 1,000", "Life, yrs", "TEM %", "TEA %", "Real yield %"].map((x) => h("th", {}, x)));
      const body = rows.map((r) => h("tr", {},
        h("td", { class: "pres", title: r.coupon || "" }, r.name),
        h("td", {}, h("span", { class: "sw", style: `background:${groupColor(KCOL[r.kind])}` }), " ", (index.kinds || {})[r.kind] || r.kind),
        h("td", {}, r.maturity ? fmtDay(r.maturity) : "–"),
        h("td", {}, n0(r.vn)), h("td", {}, n0(r.ve)), h("td", {}, n1(r.price)), h("td", {}, n1(r.life, 2)),
        h("td", {}, n1(r.tem, 2)), h("td", {}, n1(r.tea, 1)), h("td", {}, n1(r.real, 1))));
      const ars = rows.filter((r) => r.currency === "ARS").reduce((a, r) => a + (r.ve || 0), 0);
      const usd = rows.filter((r) => r.currency === "USD").reduce((a, r) => a + (r.ve || 0), 0);
      tableEl.replaceChildren(h("table", { class: "data plc" }, h("thead", {}, head), h("tbody", {}, body)));
      hint.textContent = `${fmtDay(state.date)}: ${rows.length} placement${rows.length === 1 ? "" : "s"}; cash raised ${n0(ars)} million pesos` +
        (usd ? ` and US$ ${n0(usd)} million (dollar and dollar-linked)` : "") + ". Amounts in millions of the instrument's currency; hover an instrument for its terms.";
      view = { header: ["settlement", "instrument", "type", "issue", "maturity", "terms", "currency", "face_value", "cash_value", "price_per_1000", "life_years", "tem_pct", "tea_pct", "real_yield_pct"],
        rows: rows.map((r) => [state.date, r.name, r.kind, r.issue, r.maturity, r.coupon, r.currency, r.vn, r.ve, r.price, r.life, r.tem, r.tea, r.real]) };
    }
    const onCSV = async () => {
      // The whole year, not just the date shown.
      const t = await load(state.year);
      const rows = Object.entries(t.auctions).flatMap(([d, rs]) => rs.map((r) => [d, r.name, r.kind, r.issue, r.maturity, r.coupon, r.currency, r.vn, r.ve, r.price, r.life, r.tem, r.tea, r.real]));
      downloadBlob(toCSV(view.header, rows), `${ind.id}_${state.year}.csv`, "text/csv");
    };
    appendAll(card, h("div", { class: "controls" }, h("label", { class: "base-pick" }, h("span", {}, "Year:"), ySel),
      h("label", { class: "base-pick" }, h("span", {}, "Settlement date:"), dSel)), hint, tableEl,
      ind.note ? h("p", { class: "chips-note" }, ind.note) : null,
      h("div", { class: "card-foot" },
        h("span", {}, "Source: ", T.source_url ? h("a", { href: T.source_url, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label,
          ` · Latest: ${ind.last_obs ? fmtDay(ind.last_obs) : "–"}`),
        h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download the year's placements as CSV" }, "CSV (year)"))));
    card.draw = async () => {
      if (!index) {
        try { index = await getJSON(`${DATA}${T.path}${T.file}`); } catch (e) { console.error(e); tableEl.replaceChildren(h("div", { class: "error-box" }, "The placements could not be loaded.")); return; }
        const ys = Object.keys(index.years).sort();
        ySel.replaceChildren(...ys.slice().reverse().map((y) => h("option", { value: y }, y)));
        state.year = ys[ys.length - 1]; ySel.value = state.year; fillDates();
        state.date = index.latest; dSel.value = state.date;
      }
      render();
    };
    return card;
  }

  // ---------- card: panel of components (EMAE by sector, UCII by block) ----------
  // Breaks the bar axis when one value dwarfs the rest (e.g. fishing +424%),
  // so the others stay readable. Returns the axis cap, or null if no break.
  function axisBreak(vals) {
    const abs = vals.filter((v) => v !== null).map(Math.abs).sort((a, b) => b - a);
    if (abs.length < 6 || abs[1] === 0 || abs[0] < 2.5 * abs[1]) return null;
    return abs[1] * 1.35;
  }

  function panelCard(ind) {
    const keys = Object.keys(ind.variants);
    const T = tfs(ind);
    const isRate = ind.measure === "rate";
    // What the ranked bars show: level (rates) or a change (m/m, y/y, YTD,
    // chosen with `bar_transforms`), optionally as incidence: the contribution
    // to the total's change in pp, when fixed-base weights exist.
    const W = ind.weights && ind.weights.values;
    const baseKind = ind.bar_transform || (isRate ? "level" : "yoy");
    const barKinds = (ind.bar_transforms && ind.bar_transforms.length ? ind.bar_transforms : [baseKind]).filter((k) => T[k] || INDEX_TRANSFORMS[k]);
    const specFor = (k) => T[k] || INDEX_TRANSFORMS[k];
    const bstate = { kind: barKinds.includes(baseKind) ? baseKind : barKinds[0], inc: false };
    let barSpec = specFor(bstate.kind);
    const defaults = [].concat(ind.default.variant).slice(0, MAX_PANEL_SERIES);
    const slots = new Map(defaults.map((k, i) => [k, i]));   // color follows the component, not its rank
    const state = { transform: ind.default.transform || baseKind, range: rangeFromDefault(ind) };
    const noun = ind.component_noun || "series";

    const card = cardFrame(ind, true);
    const barEl = h("div", { class: "chart tall", role: "img", "aria-label": `Latest ${barSpec.short || "level"} by ${noun}` });
    const lineEl = h("div", { class: "chart", role: "img", "aria-label": `Selected ${noun} over time` });
    const chipsEl = h("div", { class: "chips", role: "group", "aria-label": `${noun} to compare` });
    const chipsNote = h("span", { class: "chips-note" });
    const lineKinds = ind.transforms && ind.transforms.length ? Object.keys(T) : Object.keys(T).filter((k) => k !== "mom");
    const tSeg = segmented(lineKinds.map((k) => [k, T[k].label]), state.transform, (k) => { state.transform = k; drawLines(); }, "Transformation");
    const rSeg = segmented(rangeKeys(ind).map((k) => [k, k]), state.range, (k) => { state.range = k; drawLines(); }, "Time range");
    let table;

    // Incidence_k,t = w_k (X_k,t - X_k,b) / sum_j w_j X_j,b * 100, with b the
    // comparison month of the bar metric (previous month, a year earlier, or
    // the previous December). Contributions add up to the total's change.
    const pos = new Map(ind.dates.map((d, i) => [monthIndex(d), i]));
    const baseIdx = (kind, i) => pos.get(monthIndex(ind.dates[i]) - lagOf(kind, ind.dates[i], ind.frequency));
    function incidence(kind) {
      // Accumulated change: the same formula on year-to-date sums, a year back.
      const lv = Object.fromEntries(keys.map((k) => [k, kind === "acc" ? yearToDateSum(ind.dates, ind.variants[k].values) : ind.variants[k].values]));
      const out = Object.fromEntries(keys.map((k) => [k, ind.dates.map(() => null)]));
      for (let i = 0; i < ind.dates.length; i++) {
        const j = baseIdx(kind, i);
        if (j === undefined) continue;
        let den = 0, ok = true;
        for (const k of keys) { if (W[k] === undefined) continue; const p = lv[k][j]; if (p === null) { ok = false; break; } den += W[k] * p; }
        if (!ok || !den) continue;
        for (const k of keys) {
          const a = lv[k][i], p = lv[k][j];
          if (W[k] !== undefined && a !== null && p !== null) out[k][i] = (W[k] * (a - p)) / den * 100;
        }
      }
      return out;
    }
    const cache = new Map();
    function metricFor(kind, inc) {
      const key = `${kind}:${inc}`;
      if (!cache.has(key)) cache.set(key, inc ? incidence(kind)
        : Object.fromEntries(keys.map((k) => [k, transform(ind.dates, ind.variants[k].values, kind, ind.measure, ind.frequency)])));
      return cache.get(key);
    }
    // Bars use the most recent month every component shares.
    function refFor(m) {
      let i = ind.dates.length - 1;
      while (i > 0 && keys.some((k) => m[k][i] === null)) i--;
      return i;
    }
    let metric = metricFor(bstate.kind, false), refIdx = refFor(metric), refDate = ind.dates[refIdx];
    const fmtRef = (iso) => (ind.frequency === "D" ? fmtDay(iso) : fmtMonth(iso));
    const INC = { label: "Incidence (pp)", short: "Contribution to the total's change, percentage points", suffix: " pp", signed: true, change: true };
    const totalInc = () => keys.reduce((a, k) => a + (metric[k][refIdx] || 0), 0);
    const WORDS = { mom: ["Month-over-month change", "m/m"], yoy: ["Year-over-year change", "y/y"], ytd: ["Year-to-date change", "YTD"],
      acc: ["Accumulated change (year to date vs. same months a year earlier)", "accumulated"], level: ["Level", ""] };
    const ytdNote = () => (bstate.kind === "ytd" ? ` (since Dec ${Number(refDate.slice(0, 4)) - 1})` : "");
    const barTitleFor = () => isRate ? `${ind.units}, ${fmtRef(refDate)}`
      : bstate.inc ? `Incidence (contribution to total ${WORDS[bstate.kind][1]} change${ytdNote()}), ${fmtRef(refDate)}; total ${(totalInc() > 0 ? "+" : "") + fx(totalInc(), 2)} pp`
      : `${WORDS[bstate.kind][0]}${ytdNote()}, ${fmtRef(refDate)}`;
    let barTitle = barTitleFor();
    const barTitleEl = h("span", {}, `${barTitle}. `);
    function setBars() {
      metric = metricFor(bstate.kind, bstate.inc);
      refIdx = refFor(metric); refDate = ind.dates[refIdx];
      barSpec = bstate.inc ? INC : specFor(bstate.kind);
      barTitle = barTitleFor(); barTitleEl.textContent = `${barTitle}. `;
      bSeg && bSeg.update(bstate.kind);
      incBtn && incBtn.setAttribute("aria-pressed", String(bstate.inc));
      drawBars();
    }
    const bSeg = barKinds.length > 1 ? segmented(barKinds.map((k) => [k, specFor(k).label]), bstate.kind, (k) => { bstate.kind = k; setBars(); }, "Bar metric") : null;
    const realSeg = realSwitch(ind, () => { cache.clear(); setBars(); drawLines(); });
    const incBtn = W && !isRate ? h("button", { class: "btn toggle", type: "button", "aria-pressed": "false", title: "Show each component's contribution to the total's change",
      onclick: () => { bstate.inc = !bstate.inc; setBars(); } }, "Incidence (pp)") : null;

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
      const lightSeries = ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5", "#C9A227", "#C96B7E", "#3E9C9A", "#6C7A89"];
      const colors = order.map((k) => (slots.has(k) ? (forExport ? lightSeries[slots.get(k)] : cssVar(SERIES_VARS[slots.get(k)])) : muted));
      const inkColor = forExport ? "#36454F" : cssVar("--ink");
      const trace = {
        type: "bar", orientation: "h",
        y: order.map((k) => ind.variants[k].label),
        x: shown,
        marker: { color: colors },
        customdata: order.map((k, i) => [k, actual[i], W && W[k] !== undefined ? W[k] : null]),
        text: actual.map((v) => (bstate.inc ? (v > 0 ? "+" : "") + fx(v, 2) + " pp" : fmtNum(v, barSpec))),
        textposition: clipped.map((c) => (c ? "inside" : "outside")),
        insidetextanchor: "end",
        textfont: { family: cssVar("--font-ui") || "Calibri, Arial, sans-serif", size: forExport ? 15 : 12, color: clipped.map((c, i) => (c && slots.has(order[i]) ? "#FFFFFF" : inkColor)) },
        cliponaxis: false,
        hovertemplate: `%{y}: <b>%{customdata[1]:,${bstate.inc ? ".2f" : ".1f"}}${barSpec.suffix}</b>${W ? `<br>Weight: %{customdata[2]:.1f}% (${ind.weights.label || "weight"})` : ""}<extra></extra>`,
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
      if (card._realHint) card._realHint.textContent = [ind._real ? `Real terms: deflated by the ${ind.deflator.label}, in pesos of ${fmtMonth(ind.deflator.latest)}.` : "", partialNote(ind)].filter(Boolean).join(" ");
      const t = T[state.transform];
      const tr = lineTraces();
      Plotly.react(lineEl, tr, baseLayout({ pct: t, extra: { showlegend: true, legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } }, margin: { l: 52, r: 16, t: 30, b: 36 } } }), PLOT_CONFIG);
      table && table.refresh(tr[0] ? tr[0].x : [], tr.map((s) => ({ label: s.name, values: s.y })), t);
    }

    function drawAll() { drawChips(); drawBars(); drawLines(); }

    const seriesNote = isRate || ind.topic === "prices" ? "" : " · original series";
    const onPNG = () => {
      const t = T[state.transform];
      exportPNG(ind, lineTraces(), `${t.change ? t.short : ind.units}${seriesNote}`, t, `${ind.id}_${state.transform}.png`);
    };
    const onBarPNG = () => {
      const b = bars(true);
      exportPNG({ ...ind, title: `${ind.title}: ${barTitle.charAt(0).toLowerCase() + barTitle.slice(1)}` }, b.traces,
        isRate ? `Latest month, by ${noun}` : bstate.inc ? `Percentage points; weights: ${ind.weights.label}${seriesNote}`
          : bstate.kind === "mom" ? `Percent change vs. previous month${seriesNote}`
          : bstate.kind === "acc" ? `Year to date vs. the same months a year earlier, %${seriesNote}` : bstate.kind === "ytd" ? `Percent change vs. December of the previous year${seriesNote}` : `Percent change vs. same month a year earlier${seriesNote}`,
        barSpec, `${ind.id}_latest_${bstate.kind}${bstate.inc ? "_incidence" : ""}.png`,
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
    const weightsTable = W ? h("details", { class: "table-view" }, h("summary", {}, ind.weights.method === "fit" ? `Weights (${ind.weights.label}, recovered from the published indices)` : `Weights (${ind.weights.label} at constant prices)`),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, noun.charAt(0).toUpperCase() + noun.slice(1, -1).replace(/ie$/, "y")), h("th", {}, "Weight"))),
        h("tbody", {}, keys.filter((k) => W[k] !== undefined).sort((a, b) => W[b] - W[a]).map((k) => h("tr", {}, h("td", {}, ind.variants[k].label), h("td", {}, W[k].toFixed(1) + "%"))))))) : null;
    appendAll(card, 
      bSeg || incBtn || realSeg ? h("div", { class: "controls" }, bSeg, incBtn, realSeg) : null,
      partialNote(ind) || realSeg ? (() => { const p = h("p", { class: "hint" }); card._realHint = p; p.textContent = partialNote(ind); return p; })() : null,
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
  const GROUP_VARS = { copper: "--series-1", sky: "--series-2", sage: "--series-3", lavender: "--series-4",
    gold: "--series-5", rose: "--series-6", teal: "--series-7", slate: "--series-8" };
  const GROUP_LIGHT = { copper: "#B87333", sky: "#5B9BD5", sage: "#87A96B", lavender: "#8E7AB5",
    gold: "#C9A227", rose: "#C96B7E", teal: "#3E9C9A", slate: "#6C7A89", neutral: "#B0B7BD" };
  const groupColor = (c) => (GROUP_VARS[c] ? cssVar(GROUP_VARS[c]) : (isDark() ? "#5C6A74" : "#B0B7BD"));

  function contributionsCard(ind) {
    const C = ind.contributions;
    const DF = ind.frequency === "Q" ? "Q%q %Y" : "%b %Y";
    const fP = (iso) => fmtPeriod(iso, ind.frequency);
    // Shares (units "%") read as percent of the total; contributions as pp.
    // Units: "%" (shares), "pp" (contributions), or a custom suffix (e.g. "% GDP", " m").
    const pp = C.suffix ? { suffix: C.suffix.startsWith("%") || C.suffix.startsWith(" ") ? C.suffix : " " + C.suffix, signed: false }
      : C.units === "%" ? { suffix: "%", signed: false } : { suffix: " pp", signed: true };
    const sfx = pp.suffix;
    const pct = INDEX_TRANSFORMS.yoy;
    const gdpLabel = C.total.label;
    // Precomputed contributions (e.g. sources of base money): no component lines,
    // and the total is a change in the stock, not y/y growth.
    const PC = !!C.precomputed;
    const yoyWord = PC ? "" : " y/y";
    const state = { range: rangeFromDefault(ind) };
    const lineKeys = Object.keys(C.lines);
    // Lines keep the same color as their bar component (color follows the entity).
    const lc = (k) => C.lines[k].color || "neutral";
    const lineColor = (k) => (lc(k) === "neutral" ? cssVar("--ink-2") : groupColor(lc(k)));
    const lineLight = (k) => (lc(k) === "neutral" ? "#5A6872" : GROUP_LIGHT[lc(k)]);
    const shown = new Set(C.default_lines || lineKeys.slice(0, 3));

    const card = cardFrame(ind, true);
    const barEl = h("div", { class: "chart tall-ish", role: "img", "aria-label": `Contributions to ${C.total.label} growth` });
    const lineEl = h("div", { class: "chart", role: "img", "aria-label": "Components, year-over-year change" });
    const chipsEl = h("div", { class: "chips", role: "group", "aria-label": "Components to compare" });
    const rSeg = segmented(rangeKeys(ind).map((k) => [k, k]), state.range, (k) => { state.range = k; drawAll(); }, "Time range");
    const CY = C.yearly;
    const ySeg = CY ? segmented([["monthly", ind.frequency === "Q" ? "Quarterly" : "Monthly"], ["yearly", "Yearly"]], "monthly", (k) => { state.yearly = k === "yearly"; ySeg.update(k); drawAll(); }, "Frequency") : null;
    const hintEl = h("p", { class: "hint" });
    let table;

    // Yearly view: each group's yearly value (December of the twelve-month series).
    function yearlyTraces() {
      const start = startDate(ind.last_obs, RANGES[state.range]);
      const agg = (vals) => yearlyAgg(CY.dates, vals, CY.how);
      const tot = agg(CY.total);
      const keep = tot.years.map((y, i) => i).filter((i) => !start || `${tot.years[i].slice(0, 4)}-12-31` >= start);
      const x = keep.map((i) => tot.years[i]);
      const pick = (a) => x.map((l) => { const j = a.years.indexOf(l); return j < 0 ? null : a.values[j]; });
      const tr = C.groups.map((g, n) => ({
        type: "bar", name: g.label, x, y: pick(agg(CY.groups[n])),
        marker: { color: groupColor(g.color), line: { width: 0 } }, _lightColors: x.map(() => GROUP_LIGHT[g.color] || "#B0B7BD"),
        hovertemplate: `${g.label}: <b>%{y:,.1f}${sfx}</b><extra></extra>`,
      }));
      const ink = cssVar("--ink");
      const tl = CY.total_label || C.total.label;
      if (!C.hide_total) tr.push({ type: "scatter", mode: "markers+lines", name: tl, x, y: keep.map((i) => tot.values[i]),
        line: { color: ink, width: 1 }, marker: { color: ink, size: 8, symbol: "diamond", line: { color: cssVar("--surface"), width: 1 } }, _light: "#36454F",
        hovertemplate: `<b>${tl}: %{y:,.1f}${PC && !CY.total_label ? sfx : "%"}</b><extra></extra>` });
      return { tr, x, partial: tot.partial };
    }

    const visible = () => {
      const start = startDate(ind.last_obs, RANGES[state.range]);
      return C.dates.map((d, i) => i).filter((i) => !start || C.dates[i] >= start);
    };

    function barTraces() {
      const idx = visible(), x = idx.map((i) => C.dates[i]);
      if (C.style === "area") {
        // Filled areas: components that are mostly negative stack downward from zero.
        const median = (a) => { const v = a.filter((q) => q !== null).sort((p, q) => p - q); return v.length ? v[v.length >> 1] : 0; };
        return C.groups.map((g) => ({
          type: "scatter", mode: "lines", name: g.label, x, y: idx.map((i) => g.values[i]),
          stackgroup: median(g.values) < 0 ? "neg" : "pos", line: { width: 0.6, color: groupColor(g.color) },
          fillcolor: groupColor(g.color), _light: GROUP_LIGHT[g.color] || "#B0B7BD",
          hovertemplate: `${g.label}: <b>%{y:,.1f}${sfx}</b><extra></extra>`,
        }));
      }
      const tr = C.groups.map((g) => ({
        type: "bar", name: g.label, x, y: idx.map((i) => g.values[i]),
        marker: { color: groupColor(g.color), line: { width: 0 } }, _lightColors: idx.map(() => GROUP_LIGHT[g.color] || "#B0B7BD"),
        hovertemplate: `${g.label}: <b>%{y:,.1f}${sfx}</b><extra></extra>`,
      }));
      const ink = cssVar("--ink");
      if (!C.hide_total) tr.push({
        type: "scatter", mode: "markers+lines", name: PC ? C.total.label : `${C.total.label} growth`, x, y: idx.map((i) => C.total.values[i]),
        line: { color: ink, width: 1 }, marker: { color: ink, size: 7, symbol: "diamond", line: { color: cssVar("--surface"), width: 1 } },
        _light: "#36454F", hovertemplate: `<b>${C.total.label}: %{y:,.1f}${PC ? sfx : "%"}</b><extra></extra>`,
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
      if (state.yearly) {
        const { tr, x, partial } = yearlyTraces();
        hintEl.textContent = `Calendar years${CY.how === "sum" ? ": totals" : ""}. ${yearlyNote(CY.how, partial)}`;
        hintEl.hidden = false;
        Plotly.react(barEl, tr, baseLayout({
          pct: pp, xaxis: yearAxis(x.length),
          extra: { barmode: C.style === "area" ? "stack" : "relative", bargap: 0.25, showlegend: true, legend, margin: { l: 56, r: 16, t: 40, b: 36 } },
        }), PLOT_CONFIG);
        table && table.refresh(x, tr.map((t) => ({ label: `${t.name} (${t.type === "bar" ? sfx.trim() : "%"})`, values: t.y })), pp, "Y");
        return;
      }
      hintEl.hidden = true;
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
      if (lineKeys.length) Plotly.react(lineEl, lt, baseLayout({ pct, xaxis: { hoverformat: DF }, extra: { showlegend: true, legend, margin: { l: 52, r: 16, t: 30, b: 36 } } }), PLOT_CONFIG);
      const idx = visible();
      table && table.refresh(idx.map((i) => C.dates[i]), [
        ...(C.hide_total ? [] : [{ label: `${C.total.label} (${PC ? sfx.trim() : "y/y %"})`, values: idx.map((i) => C.total.values[i]) }]),
        ...C.groups.map((g) => ({ label: `${g.label} (${sfx.trim()})`, values: idx.map((i) => g.values[i]) })),
      ], pp, ind.frequency);
    }

    const latestI = C.dates.length - 1;
    const onPNG = () => state.yearly ? (() => { const yt = yearlyTraces(); return exportPNG(ind, yt.tr, `${ind.units} · yearly${yt.partial ? " · " + yearlyNote(CY.how, yt.partial) : ""}`, pp, `${ind.id}_yearly.png`, { barmode: C.style === "area" ? "stack" : "relative", xcategory: true }); })()
      : exportPNG(ind, barTraces(), `${PC ? ind.units : `Contributions to year-over-year ${C.total.label} growth, percentage points`} · latest: ${fP(C.dates[latestI])}`,
      pp, `${ind.id}_contributions.png`, { barmode: "relative" });
    const onLinePNG = () => exportPNG({ ...ind, title: C.lines_title || "Components, year-over-year change" }, lineTraces(), `Percent change vs. same ${ind.frequency === "Q" ? "quarter" : "month"} a year earlier · constant 2004 prices`, pct, `${ind.id}_lines_yoy.png`);
    const onCSV = () => {
      if (state.yearly) {
        const yt = yearlyTraces();
        return downloadBlob(toCSV(["year", ...yt.tr.map((t) => t.name)], yt.x.map((l, i) => [l, ...yt.tr.map((t) => (t.y[i] === null ? "" : +t.y[i].toFixed(4)))])), `${ind.id}_yearly.csv`, "text/csv");
      }
      const header = ["date", `${C.total.label} y/y %`, ...C.groups.map((g) => `${g.label} contribution (pp)`), ...lineKeys.map((k) => `${C.lines[k].label} y/y %`)];
      const rows = C.dates.map((d, i) => [d, C.total.values[i], ...C.groups.map((g) => g.values[i]), ...lineKeys.map((k) => C.lines[k].values[i])]
        .map((v, j) => (j === 0 || v === null ? v : +v.toFixed(4))));
      downloadBlob(toCSV(header, rows), `${ind.id}_contributions.csv`, "text/csv");
    };

    // Latest-quarter summary line.
    const parts = C.groups.map((g) => `${g.label} ${fmtNum(g.values[latestI], pp)}`).join(" · ");
    appendAll(card, 
      h("p", { class: "hint" }, C.hide_total ? `${fP(C.dates[latestI])}: ${parts}` : `${fP(C.dates[latestI])}: ${C.total.label} ${fmtNum(C.total.values[latestI], PC ? pp : pct)}${yoyWord} — ${parts}`),
      h("div", { class: "controls" }, ySeg, h("span", { class: "spacer" }), rSeg),
      hintEl, methodLine(ind), barEl,
      lineKeys.length ? h("p", { class: "hint", style: "margin-top:14px" }, `${C.lines_title || "Components, year-over-year change"}. `,
        h("button", { class: "btn", type: "button", onclick: onLinePNG, style: "padding:1px 8px;font-size:12px" }, "PNG")) : null,
      lineKeys.length ? chipsEl : null, lineKeys.length ? lineEl : null);
    table = tableView([], [], pp, ind.frequency);
    appendAll(card, table, ind.note ? h("p", { class: "chips-note" }, ind.note) : null, footer(ind, onPNG, onCSV));
    card.draw = drawAll;
    return card;
  }

  // ---------- headline tiles ----------
  function tile(ind, href) {
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
    return h("a", { class: "tile", href: href || `#ind-${ind.id}` },
      h("div", { class: "tile-label" }, ind.short_title),
      h("div", { class: "tile-value" + cls }, fmtNum(last.value, t)),
      h("div", { class: "tile-sub" }, `${fmtPeriod(last.date, ind.frequency)} · ${what}`),
      yoy ? h("div", { class: "tile-sub" }, isRate ? `${fmtNum(yoy.value, yoySpec)} vs. a year earlier` : `${fmtNum(yoy.value, yoySpec)} y/y (original series, ${fmtPeriod(yoy.date, ind.frequency)})`) : null);
  }

  // ---------- home: summary dashboard (data/dashboard.json, built by pipeline/dashboard.py) ----------
  function dashboardView(D, pageHref) {
    let mode = "mm";
    try { mode = localStorage.getItem("aem-dash-mode") || "mm"; } catch (e) {}
    const wrap = h("section", { class: "dash", "aria-label": "Summary of the latest data" });
    const MON = (iso) => new Date(iso + "T00:00:00Z").toLocaleString(LOCALE, { month: "short", timeZone: "UTC" });
    const YR = (iso) => iso.slice(2, 4);
    const QN = (iso) => `${LANG === "es" ? "T" : "Q"}${Math.floor((Number(iso.slice(5, 7)) - 1) / 3) + 1}`;
    const nf = (v, d) => Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d, maximumFractionDigits: d });
    const signed = (v, d) => { const r = Number(v.toFixed(d)); return `${r > 0 ? "+" : r < 0 ? "−" : ""}${nf(Math.abs(r), d)}`; };
    const isPctUnit = (u) => /^%/.test(u.trim());
    function fmtChange(r, kind, v) {
      if (kind === "pct") return `${signed(v, 1)}%`;
      if (isPctUnit(r.unit)) return `${signed(v, 1)} pp`;
      if (/bn/.test(r.unit)) return signed(v, 1);
      return signed(v, Math.abs(v) < 10 ? 1 : 0);
    }
    function fmtLevel(r) {
      if (/\(index\)/.test(r.unit)) return "";
      const d = Math.abs(r.level) >= 1000 ? 0 : Math.abs(r.level) >= 100 ? 0 : 1;
      return `${nf(r.level, d)}${r.unit}`;
    }
    // Cell color from the signed z-score: green better than usual, red worse, grey unusual with no direction.
    function cellStyle(r, z) {
      if (z === null || z === undefined || Math.abs(z) < 0.5) return "";
      const a = (0.12 + 0.5 * Math.min(1, (Math.abs(z) - 0.5) / 2.5)).toFixed(2);
      const v = r.good === "none" ? "--ink-3" : z > 0 ? "--pos" : "--neg";
      return `background:${alpha(cssVar(v), a)}`;
    }
    function spark(r) {
      const ys = r.spark; if (!ys || ys.length < 2) return h("span");
      const W = 90, H = 24, lo = Math.min(...ys), hi = Math.max(...ys), sp = hi - lo || 1;
      const pts = ys.map((y, i) => `${(i / (ys.length - 1) * W).toFixed(1)},${(H - 2 - (y - lo) / sp * (H - 4)).toFixed(1)}`).join(" ");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("aria-hidden", "true");
      const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      pl.setAttribute("points", pts); pl.setAttribute("fill", "none"); pl.setAttribute("stroke", "currentColor"); pl.setAttribute("stroke-width", "1.5");
      svg.append(pl);
      return svg;
    }
    const seg = segmented([["mm", "Month on month (s.a.)"], ["yy", "12-month change"]], mode, (k) => {
      mode = k; seg.update(k); try { localStorage.setItem("aem-dash-mode", k); } catch (e) {} render();
    }, "Change shown");
    const tableWrap = h("div", { class: "dash-scroll" });
    const foot = h("p", { class: "dash-foot" });

    function render() {
      const cols = D.columns;
      const partial = D.rows.find((r) => r.partial);
      const head = h("tr", {}, h("th", { class: "dash-name", scope: "col" }, "Indicator"),
        cols.map((c, i) => h("th", { scope: "col" }, `${MON(c)} ${YR(c)}`, partial && i === cols.length - 1 ? "*" : "")),
        h("th", { scope: "col", class: "dash-level" }, "Latest"), h("th", { scope: "col" }, "3 years"));
      const body = [];
      let group = null;
      for (const r of D.rows) {
        if (r.group !== group) { group = r.group; body.push(h("tr", { class: "dash-group" }, h("th", { colspan: cols.length + 3, scope: "colgroup" }, group))); }
        const V = r[mode];
        const label = h("a", { href: pageHref(r.topic, `ind-${r.ind}`) }, r.label);
        const marks = [mode === "mm" && r.mm_nsa ? h("sup", { title: "Not seasonally adjusted" }, "†") : null,
          r.real ? h("span", { class: "dash-tag" }, "real") : null,
          r.status !== "ok" ? h("span", { class: "dash-tag warn", title: r.status === "stale" ? "No new data for longer than usual" : "Last update failed; showing the last good data" }, r.status) : null];
        const cells = cols.map((c) => {
          if (!V) return h("td", { class: "na", title: "No seasonally adjusted series: see the 12-month view" }, "n/a");
          const x = V.values[c];
          if (!x) return h("td", { class: "empty" }, "");
          const [v, z] = x;
          const tip = `${r.label}, ${r.freq === "Q" ? QN(c) + " " + c.slice(0, 4) : MON(c) + " " + c.slice(0, 4)}: ${fmtChange(r, V.kind, v)}` +
            (z === null ? "" : ` · ${Math.abs(z) < 0.5 ? "a usual move" : `${z > 0 ? (r.good === "none" ? "high" : "better") : (r.good === "none" ? "low" : "worse")} than usual (${fx(Math.abs(z), 1)} s.d.)`} for this series`);
          return h("td", { style: cellStyle(r, z), title: tip }, fmtChange(r, V.kind, v), r.freq === "Q" ? h("span", { class: "dash-q" }, QN(c)) : null);
        });
        const lvlDate = r.freq === "Q" ? `${QN(r.level_date)} ${r.level_date.slice(0, 4)}` : `${MON(r.level_date)} ${r.level_date.slice(0, 4)}`;
        body.push(h("tr", {}, h("th", { class: "dash-name", scope: "row" }, label, ...marks), cells,
          h("td", { class: "dash-level" }, fmtLevel(r), h("span", { class: "dash-date" }, lvlDate)),
          h("td", { class: "dash-spark" }, spark(r))));
      }
      tableWrap.replaceChildren(h("table", { class: "dash-table" }, h("thead", {}, head), h("tbody", {}, body)));
      // On narrow screens open on the latest months (the indicator names stay pinned on the left).
      requestAnimationFrame(() => { tableWrap.scrollLeft = tableWrap.scrollWidth; });
      foot.replaceChildren(
        mode === "mm" ? "Month on month on seasonally adjusted series (quarter on quarter for quarterly data, in the quarter's last month); † not seasonally adjusted, read with care. "
          : "Change over twelve months (four quarters) on the original series. ",
        "Changes in rates are in percentage points; reserves in US$ billions. Color: how unusual the change is against the same change over the previous ",
        `${D.history_years} years — green better than usual, red worse, grey large with no better or worse direction (e.g. the exchange rate); no color, a usual move. `,
        partial ? `*${MON(D.columns[D.columns.length - 1])}: daily series averaged to ${fmtDay(partial.partial)}. ` : "",
        "Click a name for its chart.");
    }
    render();
    appendAll(wrap, h("div", { class: "dash-head" }, h("h2", {}, "The latest data at a glance"), seg), tableWrap, foot);
    return wrap;
  }

  // ---------- home: what's new and what's coming (data/updates.json) ----------
  function timelineView(U, pageHref) {
    const day = (iso) => new Date(iso + "T12:00:00Z").toLocaleDateString(LOCALE, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
    const per = (iso, f) => (f === "Q" ? fmtPeriod(iso.slice(0, 7) + "-01", "Q") : f === "S" ? fmtPeriod(iso.slice(0, 7) + "-01", "S") : fmtMonth(iso.slice(0, 7) + "-01"));
    const recent = U.recent.length ? h("ul", { class: "tl-list" }, U.recent.map((r) => h("li", {},
      h("span", { class: "tl-date" }, day(r.seen)),
      h("a", { href: pageHref(r.topic, `ind-${r.id}`) }, r.title), h("span", { class: "tl-sub" }, ` · ${per(r.period, r.frequency)}`))))
      : h("p", { class: "tl-none" }, "No new monthly or quarterly releases in the last three weeks.");
    const ahead = U.ahead.length ? h("ul", { class: "tl-list" }, U.ahead.map((r) => h("li", {},
      h("span", { class: "tl-date" }, day(r.date)),
      r.indicators.length ? h("a", { href: pageHref(r.indicators[0].topic, `ind-${r.indicators[0].id}`), title: r.name }, shortRelease(r.name)) : h("span", { title: r.name }, shortRelease(r.name)),
      h("span", { class: "tl-sub" }, ` · ${per(r.period, r.frequency)}`))))
      : h("p", { class: "tl-none" }, "No scheduled INDEC releases in the next three weeks.");
    return h("section", { class: "timeline", "aria-label": "Recent and upcoming releases" },
      h("div", {}, h("h3", {}, "New data"), recent),
      h("div", {}, h("h3", {}, "Coming up (INDEC calendar)"), ahead));
  }
  // "Índice de precios al consumidor (IPC)" -> "CPI"; other names keep their acronym or a short form.
  function shortRelease(name) {
    const MAP = { IPC: "Consumer prices (CPI)", EMAE: "Economic activity (EMAE)", "IPI manufacturero": "Industrial production (IPI)", UCII: "Capacity utilization",
      SIPM: "Wholesale prices", ICA: "International trade", "PIB trimestral": "GDP" };
    const m = /\(([^)]+)\)\s*$/.exec(name || "");
    if (m && MAP[m[1]]) return MAP[m[1]];
    if (/construcci/i.test(name)) return "Construction (ISAC)";
    if (/salarios/i.test(name)) return "Wages (INDEC index)";
    if (/pobreza/i.test(name)) return "Poverty and indigence";
    if (/Balanza de pagos/i.test(name)) return "Balance of payments";
    if (/Mercado de trabajo/i.test(name)) return "Labor market (EPH)";
    if (/generaci/i.test(name)) return "Labor share of income";
    if (/distribuci/i.test(name)) return "Income distribution (EPH)";
    return name;
  }

  // Count downloads, shares and embeds as events when analytics (GoatCounter) is on; nothing otherwise.
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest(".card-foot .btn, .dash .seg button");
    if (!b || !window.goatcounter || !window.goatcounter.count) return;
    const card = b.closest(".card");
    window.goatcounter.count({ path: `${b.textContent.trim().toLowerCase().replace(/\s+/g, "-")}${card ? "/" + card.id.replace(/^ind-/, "") : ""}`, title: b.textContent.trim(), event: true });
  });

  // Chart texts: replace every string in a card's data that has a translation
  // (titles, descriptions, notes, series and group labels, units, sources).
  function localize(obj) {
    if (!I18N || !I18N.content) return obj;
    const C = { ...I18N.ui, ...I18N.content };
    // Fields that hold keys, codes or data, never text.
    const SKIP = new Set(["id", "source_id", "values", "dates", "key", "kind", "topic", "measure", "frequency", "variant", "transform", "range",
      "default", "headline", "emphasis", "muted", "overlay_default", "composition", "anchor", "slug", "sign", "color", "style", "file", "path",
      "source", "status", "party", "status_id", "role", "ranges", "transforms", "bar_transforms", "bar_transform", "of", "rem", "freq", "good", "ind", "unit"]);
    const walk = (x) => {
      if (typeof x === "string") { const k = x.trim(); return C[k] !== undefined ? C[k] : C[k.split(/\s+/).join(" ")] !== undefined ? C[k.split(/\s+/).join(" ")] : x; }
      if (Array.isArray(x)) return x.length > 400 ? x : x.map(walk);   // long arrays are data, not text
      if (x && typeof x === "object") {
        for (const key of Object.keys(x)) {
          if (SKIP.has(key)) continue;
          x[key] = key === "component_noun" && typeof x[key] === "string" ? ((I18N.nouns || {})[x[key]] || x[key]) : walk(x[key]);
        }
        return x;
      }
      return x;
    };
    return walk(obj);
  }

  // ---------- Spanish pages: translate the interface as it renders ----------
  // Text nodes and labels are translated when they enter the page (exact strings, then
  // patterns); charts are translated as they are drawn (trace names, hover text,
  // annotations), with Spanish number separators and month names.
  function installTranslation() {
    const ATTRS = ["title", "aria-label", "placeholder"];
    const skip = (n) => n.closest && n.closest("svg, script, style, textarea, code, .no-tr");
    const doText = (t) => { const v = t.nodeValue; if (v && /[A-Za-z]/.test(v)) { const x = tr(v); if (x !== v) t.nodeValue = x; } };
    const doEl = (el) => {
      if (el.nodeType !== 1 || skip(el)) return;
      for (const a of ATTRS) if (el.hasAttribute(a)) { const v = el.getAttribute(a), x = tr(v); if (x !== v) el.setAttribute(a, x); }
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
        acceptNode: (n) => (n.nodeType === 1 && n.matches("svg, script, style, textarea, code, .no-tr") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        if (n.nodeType === 3) doText(n);
        else for (const a of ATTRS) if (n.hasAttribute(a)) { const v = n.getAttribute(a), x = tr(v); if (x !== v) n.setAttribute(a, x); }
      }
    };
    doEl(document.body);
    new MutationObserver((recs) => {
      for (const r of recs) {
        if (r.type === "characterData") { if (!skip(r.target.parentElement || document.body)) doText(r.target); }
        else if (r.type === "attributes") { if (ATTRS.includes(r.attributeName)) { const v = r.target.getAttribute(r.attributeName), x = tr(v); if (v && x !== v && !skip(r.target)) r.target.setAttribute(r.attributeName, x); } }
        else r.addedNodes.forEach((n) => (n.nodeType === 3 ? (!skip(n.parentElement || document.body) && doText(n)) : doEl(n)));
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    // Charts.
    const hov = I18N.hover || [];
    const trHover = (t) => {
      if (typeof t !== "string") return t;
      let o = t.replace(/Q%q/g, "T%q").replace(/<extra>([^<%]*)<\/extra>/g, (m, x) => `<extra>${tr(x)}</extra>`);
      for (const [a, b] of hov) o = o.split(a).join(b);
      return o;
    };
    const trTrace = (t) => ({ ...t, ...(t.name ? { name: tr(t.name) } : {}), ...(t.hovertemplate ? { hovertemplate: trHover(t.hovertemplate) } : {}),
      ...(Array.isArray(t.text) ? { text: t.text.map((x) => tr(x)) } : typeof t.text === "string" ? { text: tr(t.text) } : {}) });
    const trAxis = (a) => (a && a.title ? { ...a, title: typeof a.title === "string" ? tr(a.title) : { ...a.title, text: tr(a.title.text) } } : a);
    const trLayout = (l) => {
      if (!l) return l;
      const o = { ...l, separators: ",.", ...(l.annotations ? { annotations: l.annotations.map((a) => ({ ...a, text: tr(a.text) })) } : {}) };
      for (const k of Object.keys(o)) if (/^[xy]axis\d*$/.test(k)) o[k] = trAxis(o[k]);
      if (o.title && o.title.text) o.title = { ...o.title, text: o.title.text.replace(/>([^<>]+)</g, (m, x) => `>${tr(x)}<`) };
      return o;
    };
    const cfg = (c) => ({ ...(c || {}), locale: "es" });
    if (!window.Plotly) return;
    window.Plotly.register({ moduleType: "locale", name: "es", dictionary: { "Zoom in": "Ampliar", "Zoom out": "Reducir", "Pan": "Desplazar", "Zoom": "Zoom",
      "Reset axes": "Restablecer ejes", "Autoscale": "Escala automática", "Download plot as a png": "Descargar como PNG", "Double-click to zoom back out": "Doble clic para volver" },
      format: { days: ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"], shortDays: ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"],
        months: ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
        shortMonths: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"], date: "%d/%m/%Y", decimal: ",", thousands: "." } });
    for (const f of ["react", "newPlot"]) {
      const orig = window.Plotly[f].bind(window.Plotly);
      window.Plotly[f] = (el, data, layout, config) => orig(el, (data || []).map(trTrace), trLayout(layout), cfg(config));
    }
    const toImg = window.Plotly.toImage.bind(window.Plotly);
    window.Plotly.toImage = (fig, opts) => toImg({ ...fig, data: (fig.data || []).map(trTrace), layout: trLayout(fig.layout), config: cfg(fig.config) }, opts);
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

    if (LANG === "es") {
      try {
        I18N = await getJSON(`${DATA}i18n/es.json`);
        I18N._patterns = (I18N.patterns || []).map(([re, out]) => [new RegExp(`^${re}$`), out]);
        // Page and group names in the menu and on the home page.
        for (const t of manifest.topics) Object.assign(t, (I18N.topics || {})[t.id] || {});
        localize(manifest.indicators);
        installTranslation();
      } catch (e) { console.error("translations not loaded", e); }
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
    $("#build-meta").textContent = `Last data check: ${built.toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" })}`;

    const topicOf = new Map(manifest.indicators.map((m) => [m.id, m.topic]));
    const slugOf = new Map(manifest.topics.map((t) => [t.id, t.slug]));
    const pageHref = (topicId, anchor) => `${ROOT}${slugOf.get(topicId)}/${anchor ? "#" + anchor : ""}`;

    // Links shared before the site had one page per topic (…/#ind-emae,
    // …/#topic-real_sector) land on the home page: send them on.
    if (PAGE === "home") {
      const m = location.hash.match(/^#(ind|topic)-(.+)$/);
      const oldId = (id) => (manifest.topics.find((t) => (t.old_ids || []).includes(id)) || {}).id || id;
      const topicId = m && (m[1] === "ind" ? topicOf.get(m[2]) : oldId(m[2]));
      if (topicId && slugOf.has(topicId)) { location.replace(pageHref(topicId, m[1] === "ind" ? `ind-${m[2]}` : "")); return; }
    }

    buildNav($("#nav"), manifest, pageHref);

    // Charts are redrawn on a theme change (set once the page has charts).
    let redraw = () => redrawAll();
    $("#theme-toggle").addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("aem-theme", next); } catch (e) {}
      redraw();
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!document.documentElement.dataset.theme) redraw(); });

    const topicsEl2 = $("#topics");
    if (PAGE === "methodology" || PAGE === "about") return;   // static pages: nothing to load
    if (PAGE === "home") {
      // Home: the summary dashboard, then one card per topic.
      let dash = null;
      try { dash = localize(await getJSON(`${DATA}dashboard.json`)); } catch (e) { console.error(e); }
      let upd = null;
      try { upd = localize(await getJSON(`${DATA}updates.json`)); } catch (e) { console.error(e); }
      if (dash && dash.rows.length) { $("#tiles").className = "dash-host"; $("#tiles").replaceChildren(dashboardView(dash, pageHref)); }
      if (upd) $("#tiles").after(timelineView(upd, pageHref));
      else {
        const heads = await Promise.all(manifest.indicators.filter((m) => m.headline)
          .map((m) => getJSON(`${DATA}${m.id}.json`).catch((e) => { console.error(e); return null; })));
        $("#tiles").replaceChildren(...heads.filter(Boolean).map((ind) => tile(ind, pageHref(ind.topic, `ind-${ind.id}`))).filter(Boolean));
      }
      redraw = () => { if (dash && dash.rows.length) $("#tiles").replaceChildren(dashboardView(dash, pageHref)); };
      topicsEl2.replaceChildren(h("h2", { class: "home-sub" }, "Dashboards"), h("div", { class: "topic-grid" }, manifest.topics.map((t) => {
        const list = manifest.indicators.filter((m) => m.topic === t.id);
        if (!list.length) return null;
        return h("a", { class: "topic-card", href: pageHref(t.id) },
          h("h2", {}, t.title), h("p", {}, t.description || ""),
          h("p", { class: "topic-list" }, list.map((m) => m.short_title).join(" · ")));
      })));
      return;
    }

    if (PAGE === "embed") {
      // One card, for an iframe on another site: /embed/?id=<card>[&theme=light|dark]
      const q = new URLSearchParams(location.search), id = q.get("id");
      if (q.get("theme") === "light" || q.get("theme") === "dark") document.documentElement.dataset.theme = q.get("theme");
      const meta = manifest.indicators.find((m) => m.id === id);
      if (!meta) { topicsEl2.replaceChildren(h("div", { class: "error-box" }, "Chart not found.")); return; }
      let ind;
      try { ind = localize(await getJSON(`${DATA}${id}.json`)); } catch (e) { topicsEl2.replaceChildren(h("div", { class: "error-box" }, "Data could not be loaded.")); return; }
      const card = makeCard(ind);
      const full = new URL(pageHref(meta.topic, `ind-${id}`), location.href).href;
      card.append(h("p", { class: "embed-credit" }, h("a", { href: full, target: "_blank", rel: "noopener" }, "Argentina Economic Monitor"), " · Economic Order · ",
        h("a", { href: full, target: "_blank", rel: "noopener" }, "Full chart and data ↗")));
      topicsEl2.replaceChildren(h("div", { class: "cards" }, card));
      startDrawing();
      // Tell a parent page our height, for auto-resizing iframes.
      const post = () => { try { parent.postMessage({ aem: "height", id, height: document.documentElement.scrollHeight }, "*"); } catch (e) {} };
      new ResizeObserver(post).observe(document.body); post();
      return;
    }

    const topic = manifest.topics.find((t) => t.id === PAGE);
    const list = manifest.indicators.filter((m) => m.topic === PAGE);
    if (!topic || !list.length) { topicsEl2.replaceChildren(h("div", { class: "error-box" }, "Nothing to show on this page yet.")); return; }
    const inds = (await Promise.all(list.map((m) => getJSON(`${DATA}${m.id}.json`).then(localize).catch((e) => { console.error(e); return null; })))).filter(Boolean);

    // Registry order; panels and cards flagged `wide` span the full row.
    const cards = h("div", { class: "cards" }, inds.map(makeCard));
    topicsEl2.replaceChildren(h("section", { class: "topic", id: `topic-${topic.id}` }, cards));
    addEmbedButtons(cards, topic);
    $("#tiles").replaceChildren(...inds.filter((i) => i.headline).map((i) => tile(i)).filter(Boolean));
    const sn = $("#section-nav");
    sn.replaceChildren(...inds.map((i) => h("a", { href: `#ind-${i.id}` }, i.short_title)));
    sn.hidden = false;
    startDrawing();
  }

  function makeCard(ind) {
    return ind.kind === "panel" ? panelCard(ind)
        : ind.kind === "contributions" ? contributionsCard(ind)
        : ind.kind === "balance_sheet" ? balanceCard(ind)
        : ind.kind === "top10" ? top10Card(ind)
        : ind.kind === "schedule" ? scheduleCard(ind)
        : ind.kind === "placements" ? placementsCard(ind)
        : ind.kind === "curve" ? curveCard(ind)
        : ind.kind === "tenure" ? tenureCard(ind)
        : ind.kind === "rem_revisions" ? remRevisionsCard(ind)
        : ind.kind === "statement" ? statementCard(ind)
        : ind.kind === "rer_calc" ? rerCalcCard(ind)
        : variantsCard(ind, !!ind.wide);
  }

  // Each card gets an "Embed" button: an iframe of /embed/?id=<card> to paste into another site.
  function addEmbedButtons(container, topic) {
    container.querySelectorAll(".card").forEach((card) => {
      const id = card.id.replace(/^ind-/, ""), actions = card.querySelector(".card-foot .actions");
      if (!actions || !id) return;
      actions.append(h("button", { class: "btn", type: "button", title: "Copy a link to this chart (with a preview for social media)", onclick: (e) => shareCard(card, id, e.currentTarget) }, "Share"),
        h("button", { class: "btn", type: "button", title: "Embed this chart in another page", onclick: () => embedDialog(card, id) }, "Embed"));
    });
  }
  // Share: the card's share page (title, description and preview image for social media) redirects to the card.
  async function shareCard(card, id, btn) {
    const url = new URL(`${ROOT}share/${id}/`, location.href).href;
    const title = card.querySelector("h3").firstChild.textContent;
    if (navigator.share && matchMedia("(pointer: coarse)").matches) { try { await navigator.share({ title, url }); return; } catch (e) {} }
    const old = btn.textContent;
    try { await navigator.clipboard.writeText(url); btn.textContent = "Link copied"; } catch (e) { window.prompt("Copy this link:", url); }
    setTimeout(() => { btn.textContent = old; }, 1800);
  }
  function embedDialog(card, id) {
    const base = new URL(`${ROOT}embed/`, location.href).href;
    const src = `${base}?id=${encodeURIComponent(id)}`;
    const height = Math.max(420, Math.min(1400, Math.round(card.offsetHeight + 40)));
    const code = `<iframe src="${src}" width="100%" height="${height}" style="border:0;max-width:100%" loading="lazy" title="${card.querySelector("h3").textContent.replace(/"/g, "&quot;")} (Argentina Economic Monitor)"></iframe>`;
    const ta = h("textarea", { class: "embed-code", readonly: true, rows: 4, "aria-label": "Embed code" });
    ta.value = code;
    const status = h("span", { class: "embed-status", role: "status" });
    const close = () => dlg.remove();
    const dlg = h("div", { class: "embed-dialog", role: "dialog", "aria-modal": "true", "aria-label": "Embed this chart" },
      h("div", { class: "embed-box" },
        h("h4", {}, "Embed this chart"),
        h("p", {}, "Paste this code into your page. The chart stays interactive and updates with the data; it links back to the Monitor."),
        ta,
        h("div", { class: "embed-actions" },
          h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
            try { await navigator.clipboard.writeText(code); status.textContent = "Copied."; } catch (e) { ta.select(); status.textContent = "Press Ctrl+C to copy."; } } }, "Copy code"),
          h("a", { class: "btn", href: src, target: "_blank", rel: "noopener" }, "Preview"),
          status,
          h("button", { class: "btn", type: "button", onclick: close }, "Close"))));
    dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
    document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } });
    document.body.append(dlg);
    ta.focus(); ta.select();
  }

  // Draw each chart when it comes near the viewport, so long pages open fast.
  let redrawAll = () => {};
  function startDrawing() {
    const topicsEl2 = $("#topics");
    if (!window.Plotly) { topicsEl2.prepend(h("div", { class: "error-box" }, "The charting library could not be loaded.")); return; }
    // Draw each chart when it comes near the viewport, so long pages open fast.
    const drawn = new Set();
    const drawCard = (c) => { if (c.draw) { c.draw(); drawn.add(c); } };
    const all = [...document.querySelectorAll(".card")];
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => entries.forEach((e) => {
        if (e.isIntersecting && !drawn.has(e.target)) { drawCard(e.target); io.unobserve(e.target); }
      }), { rootMargin: "600px 0px" });
      all.forEach((c) => io.observe(c));
    } else all.forEach(drawCard);
    // The cards were built after load, so jump to a linked card now.
    if (location.hash) { const el = document.getElementById(location.hash.slice(1)); if (el) { drawCard(el); el.scrollIntoView(); } }
    redrawAll = () => drawn.forEach((c) => c.draw());
  }

  document.addEventListener("DOMContentLoaded", main);
})();
