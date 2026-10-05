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
  const DATA = ROOT + "data/";
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
    ytd:   { label: "YTD %", short: "Year to date: change since December of the previous year, %", suffix: "%", signed: true, change: true },
    acc:   { label: "Accum. y/y %", short: "Accumulated: year to date vs. the same months a year earlier, %", suffix: "%", signed: true, change: true },
  };
  const RATE_TRANSFORMS = {
    level: { label: "Level", short: null, suffix: "%", signed: false, change: false },
    yoy:   { label: "y/y (pp)", short: "Year-over-year change, percentage points", suffix: " pp", signed: true, change: true },
    mom:   { label: "m/m (pp)", short: "Month-over-month change, percentage points", suffix: " pp", signed: true, change: true },
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
  const fmtMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
  const fmtShortMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  // Period label: "Jul 2026" for monthly data, "Q3 2026" for quarterly.
  const fmtDay = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const fmtPeriod = (iso, freq) => {
    if (freq === "D") return fmtDay(iso);
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
    if (kind === "level") return values.slice();
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
      yaxis: { gridcolor: light.grid, linecolor: light.rule, zerolinecolor: light.ink2, ticksuffix: specOf(pct).suffix, automargin: true, ...(extras.ylog ? { type: "log", ticksuffix: "" } : {}) },
      shapes: (extras.shapes || []).map((sh) => ({ ...sh, ...(sh._lightFill ? { fillcolor: sh._lightFill } : {}), line: { ...(sh.line || {}), color: (sh.line && sh.line._light) || light.ink3 } })),
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
    const day = new Date(nr.date + "T12:00:00Z").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
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
    const pName = (m) => (Q ? `Q${(m - 1) / 3 + 1}` : new Date(Date.UTC(2000, m - 1, 1)).toLocaleDateString("en-US", { month: "short", timeZone: "UTC" }));
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
    const f = (v) => v.toFixed(1) + "%";
    const tbody = h("tbody", {}, keys.map((k) => {
      const d = Math.round((w.new[k] - w.old_at_link[k]) * 10) / 10 || 0;
      return h("tr", {}, h("td", { class: "pres" }, w.labels[k]), h("td", {}, f(w.old_at_link[k])), h("td", {}, f(w.new[k])), h("td", {}, (d > 0 ? "+" : "") + d.toFixed(1) + " pp"));
    }));
    return h("details", { class: "table-view" }, h("summary", {}, "Show basket weights"),
      h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Division"), h("th", {}, `2004/05 basket, at ${fmtShortMonth(ind.derived.link)} prices`), h("th", {}, "2017/18 basket"), h("th", {}, "Change"))), tbody)),
      h("p", { class: "chips-note" }, `2004/05 weights recovered from the published indices (they reproduce the official index within ${ind.derived.replication_max_error_pct.toFixed(2)}% since ${fmtShortMonth(ind.derived.link)}).`));
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
    const seg = segmented([["nominal", "Nominal"], ["real", "Real"]], "nominal", (k) => {
      for (const kk of Object.keys(ind.variants)) ind.variants[kk].values = k === "real" ? real[kk] : nominal[kk];
      ind._real = k === "real"; seg.update(k); onChange();
    }, "Nominal or real terms");
    seg.title = `Real: deflated by the ${D.label}, in pesos of ${fmtMonth(D.latest)}`;
    return seg;
  }
  const unitsOf = (ind) => (ind._real ? ind._realUnits : ind.units);
  // The latest month of a monthly average built from daily data may be partial.
  const partialNote = (ind) => (ind.derived && ind.derived.partial_month
    ? `${fmtMonth(ind.derived.partial_month.slice(0, 7) + "-01")} is partial: data through ${fmtDay(ind.derived.partial_month)}.` : "");

  // ---------- card: one series with variants ----------
  function variantsCard(ind, wide) {
    const variantKeys = Object.keys(ind.variants);
    const T = tfs(ind);
    const isRate = ind.measure === "rate";
    const DF = ind.frequency === "Q" ? "Q%q %Y" : ind.frequency === "D" ? "%d %b %Y" : "%b %Y";   // hover date format
    const fP = (iso) => fmtPeriod(iso, ind.frequency);
    const state = {
      variant: ind.default.variant,
      transform: ind.default.transform || "level",
      range: rangeFromDefault(ind),
      base: ind.default.base ? "custom" : "published",   // "published" | "term:<id>" | "custom"
      customBase: ind.default.base || null,               // "YYYY-MM-01"
      byPres: false,
      episodes: false,            // mark dated shocks (registry `episodes`)
    };
    const card = cardFrame(ind, !!wide);
    const chartEl = h("div", { class: "chart" + (wide ? " tall-ish" : ""), role: "img", "aria-label": `${ind.title} chart` });
    const hint = h("p", { class: "hint" });
    const statsEl = h("div", { class: "term-stats", hidden: true });

    // Overlay cards draw every variant at once; the default one leads and is the
    // series used by the presidency view.
    const vSeg = variantKeys.length > 1 && !ind.overlay ? segmented(variantKeys.map((k) => [k, ind.variants[k].label]), state.variant, (k) => { state.variant = k; if (k === "original" && state.transform === "mom") state.transform = "yoy"; draw(); }, "Series") : null;
    const tSeg = segmented(Object.entries(T).map(([k, t]) => [k, t.label]), state.transform, (k) => { state.transform = k; draw(); }, "Transformation");
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
    const baseWrap = isRate ? null : h("label", { class: "base-pick" }, h("span", {}, "Base = 100:"), baseSel, customIn);

    const EP = ind.episodes || [];
    const epBtn = EP.length ? h("button", { class: "btn toggle", type: "button", "aria-pressed": "false", title: "Mark dated shocks on the chart and show presidency averages without them",
      onclick: () => { state.episodes = !state.episodes; draw(); } }, "Episodes") : null;
    const epKey = EP.length ? h("ol", { class: "episode-key", hidden: true }, EP.map((e) =>
      h("li", {}, h("b", {}, e.label), ` (${fmtShortMonth(e.start)}${e.end !== e.start ? "–" + fmtShortMonth(e.end) : ""})`, e.description ? `: ${e.description}.` : ""))) : null;
    // Months inside any episode (for averages that leave them out).
    const inEpisode = (iso) => EP.some((e) => iso >= e.start && iso <= e.end);
    const realSeg = realSwitch(ind, () => draw());
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

      if (!state.byPres) {
        const keys = ind.overlay ? [state.variant, ...variantKeys.filter((k) => k !== state.variant)] : [state.variant];
        const start = viewStart(ind, state.range);
        keys.forEach((k, slot) => {
          const s = k === state.variant ? main : series(k);
          // Overlays keep empty months in range so a gap in one series shows as a gap.
          const ii = ind.overlay ? ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && (!x.length || ind.dates[i] <= x[x.length - 1])) : idx;
          const xx = ii.map((i) => ind.dates[i]), yy = ii.map((i) => s.y[i]);
          const color = cssVar(SERIES_VARS[slot]);
          const hover = `%{x|${DF}}: <b>%{y:${yFmt()}}${sfx}</b><extra>${s.v.label}</extra>`;
          traces.push(state.transform === "mom"
            ? { type: "bar", x: xx, y: yy, name: s.v.label, marker: { color }, hovertemplate: hover, _slot: slot }
            : { type: "scatter", mode: "lines", x: xx, y: yy, name: s.v.label, connectgaps: false,
                line: slot ? { color, width: 1.8, dash: "dash" } : { color, width: 2 }, hovertemplate: hover, _slot: slot });
        });
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
      tSeg.update(state.transform, { mom: state.variant === "original" });
      rSeg.update(state.range);
      presBtn && presBtn.setAttribute("aria-pressed", String(state.byPres));
      epBtn && epBtn.setAttribute("aria-pressed", String(state.episodes));
      if (epKey) epKey.hidden = !state.episodes;
      baseSel.disabled = state.transform !== "level";
      customIn.disabled = state.transform !== "level";

      const { traces, shapes, annotations, main, t, x, y } = build();
      const msgs = [];
      if (isRate && state.transform !== "level") msgs.push("Changes in a rate are shown in percentage points.");
      if (state.variant === "original" && T.mom) msgs.push(`${T.mom.label.split(" ")[0]} changes are only meaningful on seasonally adjusted data.`);
      const defaultBase = ind.default.base && state.base === "custom" && state.customBase === ind.default.base;
      if (state.transform !== "level" && state.base !== "published" && !defaultBase) msgs.push("Rebasing applies to levels only; percent changes are unaffected.");
      if (state.transform === "level" && state.base !== "published" && !main.rebased) msgs.push("No data for that base month; showing the series as published.");
      if (ind.overlay && state.byPres) msgs.push(`By presidency shows the ${main.v.label.toLowerCase()} series.`);
      if (isLog()) msgs.push("Log scale: equal distances are equal percentage changes.");
      if (ind._real) msgs.push(`Real terms: deflated by the ${ind.deflator.label}, in pesos of ${fmtMonth(ind.deflator.latest)}; months after that have no CPI yet.`);
      if (partialNote(ind)) msgs.push(partialNote(ind));
      hint.textContent = msgs.join(" ");

      Plotly.react(chartEl, traces, baseLayout({
        pct: t,
        // Plain numbers on log axes unless the values are tiny (long-run price indices).
        yaxis: isLog() ? { type: "log", ticksuffix: "", exponentformat: Math.min(...y.filter((q) => q > 0)) < 0.01 ? "power" : "none", tickformat: Math.min(...y.filter((q) => q > 0)) < 0.01 ? undefined : ",~r" } : { type: "linear" },
        extra: {
          bargap: 0.2, shapes, annotations, barmode: "group",
          showlegend: (state.byPres && state.transform !== "mom") || (ind.overlay && !state.byPres),
          legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } },
          margin: { l: 52, r: 16, t: state.byPres ? 30 : 10, b: 36 },
        },
      }), PLOT_CONFIG);
      drawStats(main, t);
      const suffix = t.change ? " (" + t.label + ")" : main.rebased ? ` (${main.units})` : "";
      const cols = overlayKeys().map((k) => {
        const s = k === state.variant ? main : series(k);
        return { label: `${s.v.label}${suffix}`, values: x.map((d) => s.y[ind.dates.indexOf(d)]) };
      });
      table && table.refresh(x, cols, t);
    }

    const overlayKeys = () => (ind.overlay && !state.byPres ? [state.variant, ...variantKeys.filter((k) => k !== state.variant)] : [state.variant]);
    const subtitle = (main, t) => `${overlayKeys().map((k) => ind.variants[k].label).join(" vs. ")} · ${t.change ? t.short : main.units}`;
    const fileTag = () => `${ind.id}_${state.variant}_${state.transform}${baseMonth() ? "_base" + baseMonth().slice(0, 7) : ""}${state.byPres ? "_presidencies" : ""}`;
    const onPNG = () => {
      const { traces, shapes, annotations, main, t } = build();
      exportPNG(ind, traces, subtitle(main, t), t, `${fileTag()}.png`, { shapes, annotations, ylog: isLog(), barmode: "group" });
    };
    const onCSV = () => {
      const { main, t, x, y } = build();
      const ks = overlayKeys();
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
      h("div", { class: "controls" }, vSeg, tSeg, h("span", { class: "spacer" }), ind.view_start ? null : rSeg),
      // A short fixed window (view_start) has no use for rebasing or presidencies.
      ind.view_start ? null : h("div", { class: "controls" }, realSeg, baseWrap, presBtn, epBtn),
      trackerLine(), summaryEl, hint, bandKey, epKey, chartEl, statsEl);
    drawSummary();
    table = tableView([], [], false, ind.frequency);
    appendAll(card, table, annualTable(ind), weightsTable(ind), ind.note ? h("p", { class: "chips-note" }, ind.note) : null, footer(ind, onPNG, onCSV));
    card.draw = draw;
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
      : bstate.inc ? `Incidence (contribution to total ${WORDS[bstate.kind][1]} change${ytdNote()}), ${fmtRef(refDate)}; total ${(totalInc() > 0 ? "+" : "") + totalInc().toFixed(2)} pp`
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
      const lightSeries = ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5"];
      const colors = order.map((k) => (slots.has(k) ? (forExport ? lightSeries[slots.get(k)] : cssVar(SERIES_VARS[slots.get(k)])) : muted));
      const inkColor = forExport ? "#36454F" : cssVar("--ink");
      const trace = {
        type: "bar", orientation: "h",
        y: order.map((k) => ind.variants[k].label),
        x: shown,
        marker: { color: colors },
        customdata: order.map((k, i) => [k, actual[i], W && W[k] !== undefined ? W[k] : null]),
        text: actual.map((v) => (bstate.inc ? (v > 0 ? "+" : "") + v.toFixed(2) + " pp" : fmtNum(v, barSpec))),
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
        type: "scatter", mode: "markers+lines", name: PC ? C.total.label : `${C.total.label} growth`, x, y: idx.map((i) => C.total.values[i]),
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
      if (lineKeys.length) Plotly.react(lineEl, lt, baseLayout({ pct, xaxis: { hoverformat: DF }, extra: { showlegend: true, legend, margin: { l: 52, r: 16, t: 30, b: 36 } } }), PLOT_CONFIG);
      const idx = visible();
      table && table.refresh(idx.map((i) => C.dates[i]), [
        { label: `${C.total.label} (${PC ? "%" : "y/y %"})`, values: idx.map((i) => C.total.values[i]) },
        ...C.groups.map((g) => ({ label: `${g.label} (pp)`, values: idx.map((i) => g.values[i]) })),
      ], pp);
    }

    const latestI = C.dates.length - 1;
    const onPNG = () => exportPNG(ind, barTraces(), `${PC ? ind.units : `Contributions to year-over-year ${C.total.label} growth, percentage points`} · latest: ${fP(C.dates[latestI])}`,
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
      h("p", { class: "hint" }, `${fP(C.dates[latestI])}: ${C.total.label} ${fmtNum(C.total.values[latestI], pct)}${yoyWord} — ${parts}`),
      h("div", { class: "controls" }, h("span", { class: "spacer" }), rSeg),
      barEl,
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

    const nav = $("#nav");
    for (const t of manifest.topics) {
      if (!manifest.indicators.some((m) => m.topic === t.id)) continue;
      nav.append(h("a", { href: pageHref(t.id), "aria-current": t.id === PAGE ? "true" : null }, t.title));
    }
    nav.append(h("a", { href: `${ROOT}methodology/`, "aria-current": PAGE === "methodology" ? "true" : null }, "Methodology"));

    // Charts are redrawn on a theme change (set once the page has charts).
    let redraw = () => {};
    $("#theme-toggle").addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("aem-theme", next); } catch (e) {}
      redraw();
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!document.documentElement.dataset.theme) redraw(); });

    const topicsEl2 = $("#topics");
    if (PAGE === "methodology") return;   // static page: nothing to load
    if (PAGE === "home") {
      // Home: latest headline readings and one card per topic; no charts to draw.
      const heads = await Promise.all(manifest.indicators.filter((m) => m.headline)
        .map((m) => getJSON(`${DATA}${m.id}.json`).catch((e) => { console.error(e); return null; })));
      $("#tiles").replaceChildren(...heads.filter(Boolean).map((ind) => tile(ind, pageHref(ind.topic, `ind-${ind.id}`))).filter(Boolean));
      topicsEl2.replaceChildren(h("div", { class: "topic-grid" }, manifest.topics.map((t) => {
        const list = manifest.indicators.filter((m) => m.topic === t.id);
        if (!list.length) return null;
        return h("a", { class: "topic-card", href: pageHref(t.id) },
          h("h2", {}, t.title), h("p", {}, t.description || ""),
          h("p", { class: "topic-list" }, list.map((m) => m.short_title).join(" · ")));
      })));
      return;
    }

    const topic = manifest.topics.find((t) => t.id === PAGE);
    const list = manifest.indicators.filter((m) => m.topic === PAGE);
    if (!topic || !list.length) { topicsEl2.replaceChildren(h("div", { class: "error-box" }, "Nothing to show on this page yet.")); return; }
    const inds = (await Promise.all(list.map((m) => getJSON(`${DATA}${m.id}.json`).catch((e) => { console.error(e); return null; })))).filter(Boolean);

    // Registry order; panels and cards flagged `wide` span the full row.
    const cards = h("div", { class: "cards" }, inds.map((ind) =>
      ind.kind === "panel" ? panelCard(ind)
        : ind.kind === "contributions" ? contributionsCard(ind)
        : variantsCard(ind, !!ind.wide)));
    topicsEl2.replaceChildren(h("section", { class: "topic", id: `topic-${topic.id}` }, cards));
    $("#tiles").replaceChildren(...inds.filter((i) => i.headline).map((i) => tile(i)).filter(Boolean));
    const sn = $("#section-nav");
    sn.replaceChildren(...inds.map((i) => h("a", { href: `#ind-${i.id}` }, i.short_title)));
    sn.hidden = false;

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
    redraw = () => drawn.forEach((c) => c.draw());
  }

  document.addEventListener("DOMContentLoaded", main);
})();
