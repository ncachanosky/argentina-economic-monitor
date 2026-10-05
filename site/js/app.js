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

  const TRANSFORMS = {
    level: { label: "Level", short: "Level", unit: null, pct: false },
    yoy:   { label: "y/y %", short: "Year-over-year change, %", unit: "%", pct: true },
    mom:   { label: "m/m %", short: "Month-over-month change, %", unit: "%", pct: true },
  };
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
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const isDark = () => {
    const t = document.documentElement.dataset.theme;
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const fmtMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
  const fmtShortMonth = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  const fmtNum = (v, pct) => v === null || v === undefined || Number.isNaN(v) ? "–" :
    (pct ? (v > 0 ? "+" : "") + v.toFixed(1) + "%" : v.toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 }));
  const monthIndex = (iso) => { const [y, m] = iso.split("-").map(Number); return y * 12 + (m - 1); };

  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  // ---------- transformations ----------
  function transform(dates, values, kind) {
    if (kind === "level") return values.slice();
    const lag = kind === "yoy" ? 12 : 1;
    const mi = dates.map(monthIndex);
    const pos = new Map(mi.map((m, i) => [m, i]));
    return values.map((v, i) => {
      const j = pos.get(mi[i] - lag);
      const prev = j === undefined ? null : values[j];
      return v === null || prev === null || prev === 0 ? null : (v / prev - 1) * 100;
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
      yaxis: { gridcolor: grid, linecolor: rule, zerolinecolor: ink2, zerolinewidth: 1, automargin: true, ticksuffix: opts.pct ? "%" : "", ...opts.yaxis },
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
  async function exportPNG(ind, traces, subtitle, pct, filename) {
    const light = { ink: "#36454F", ink2: "#5A6872", ink3: "#85909A", grid: "#ECE9E3", rule: "#E3E0D9" };
    const lightSeries = ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5"];
    const font = '"Calibri", "Carlito", "Segoe UI", Arial, sans-serif';
    const data = traces.map((t, i) => {
      const c = { ...t };
      const color = t._slot !== undefined ? lightSeries[t._slot] : lightSeries[0];
      if (c.type === "bar") c.marker = { ...(c.marker || {}), color: Array.isArray(c.marker && c.marker.color) ? c.marker.color.map((x) => x === "__muted__" ? "#C9CED3" : x) : color };
      if (c.line) c.line = { ...c.line, color, width: 2.5 };
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
      showlegend: data.length > 1,
      legend: { orientation: "h", x: 0, y: 1.02, xanchor: "left", yanchor: "bottom", font: { size: 16, color: light.ink } },
      xaxis: { type: data[0] && data[0].orientation === "h" ? "linear" : "date", gridcolor: light.grid, linecolor: light.rule, ticks: "outside", tickcolor: light.rule },
      yaxis: { gridcolor: light.grid, linecolor: light.rule, zerolinecolor: light.ink2, ticksuffix: pct ? "%" : "", automargin: true },
      annotations: [
        { text: `Source: ${ind.source_label}. Latest observation: ${fmtMonth(ind.last_obs)}.`, xref: "paper", yref: "paper", x: 0, y: -0.13, xanchor: "left", yanchor: "top", showarrow: false, font: { size: 15, color: light.ink3 } },
        { text: "<b>Argentina Economic Monitor</b> · Economic Order", xref: "paper", yref: "paper", x: 1, y: -0.13, xanchor: "right", yanchor: "top", showarrow: false, font: { size: 15, color: "#B87333" } },
      ],
      bargap: 0.25,
    };
    if (data[0] && data[0].orientation === "h") { layout.xaxis.ticksuffix = pct ? "%" : ""; layout.yaxis.ticksuffix = ""; layout.margin.l = 260; }
    const url = await Plotly.toImage({ data, layout }, { format: "png", width: EXPORT.width, height: EXPORT.height });
    const a = h("a", { href: url, download: filename });
    document.body.append(a); a.click(); a.remove();
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

  function footer(ind, onPNG, onCSV) {
    const src = Object.values(ind.variants)[0].source_url;
    return h("div", { class: "card-foot" },
      h("span", {}, "Source: ", src ? h("a", { href: src, target: "_blank", rel: "noopener" }, ind.source_label) : ind.source_label,
        ` · Latest: ${fmtShortMonth(ind.last_obs)}`),
      h("div", { class: "actions" },
        h("button", { class: "btn", type: "button", onclick: onPNG, title: "Download this chart as a 1200×800 PNG" }, "PNG"),
        h("button", { class: "btn", type: "button", onclick: onCSV, title: "Download the data in this view as CSV" }, "CSV"),
        h("a", { class: "btn", href: `${DATA}${ind.id}.csv`, download: `${ind.id}_all.csv`, title: "All variants, levels, as published" }, "All data")));
  }

  function tableView(dates, cols, pct) {
    const details = h("details", { class: "table-view" }, h("summary", {}, "Show data table"));
    const fill = () => {
      if (details.dataset.filled) return;
      const n = dates.length, start = Math.max(0, n - 36);
      const tbody = h("tbody");
      for (let i = n - 1; i >= start; i--) {
        tbody.append(h("tr", {}, h("td", {}, fmtShortMonth(dates[i])), ...cols.map((c) => h("td", {}, fmtNum(c.values[i], pct)))));
      }
      details.append(h("div", { class: "table-scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", {}, "Month"), ...cols.map((c) => h("th", {}, c.label)))), tbody)));
      details.dataset.filled = "1";
    };
    details.addEventListener("toggle", fill);
    details.refresh = (d, c, p) => { dates = d; cols = c; pct = p; details.querySelector(".table-scroll")?.remove(); delete details.dataset.filled; if (details.open) fill(); };
    return details;
  }

  // ---------- card: one series with variants ----------
  function variantsCard(ind, wide) {
    const variantKeys = Object.keys(ind.variants);
    const state = {
      variant: ind.default.variant,
      transform: ind.default.transform || "level",
      range: rangeFromDefault(ind),
    };
    const card = cardFrame(ind, !!wide);
    const chartEl = h("div", { class: "chart", role: "img", "aria-label": `${ind.title} chart` });
    const hint = h("p", { class: "hint" });

    const vSeg = variantKeys.length > 1 ? segmented(variantKeys.map((k) => [k, ind.variants[k].label]), state.variant, (k) => { state.variant = k; if (k === "original" && state.transform === "mom") state.transform = "yoy"; draw(); }, "Series") : null;
    const tSeg = segmented(Object.entries(TRANSFORMS).map(([k, t]) => [k, t.label]), state.transform, (k) => { state.transform = k; draw(); }, "Transformation");
    const rSeg = segmented(Object.keys(RANGES).map((k) => [k, k]), state.range, (k) => { state.range = k; draw(); }, "Time range");
    let table;

    function current() {
      const v = ind.variants[state.variant];
      const y = transform(ind.dates, v.values, state.transform);
      const start = startDate(ind.last_obs, RANGES[state.range]);
      const idx = ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && y[i] !== null);
      return { v, x: idx.map((i) => ind.dates[i]), y: idx.map((i) => y[i]), t: TRANSFORMS[state.transform] };
    }

    function traces() {
      const { v, x, y, t } = current();
      const color = cssVar(SERIES_VARS[0]);
      const hover = `%{x|%b %Y}: <b>%{y:,.1f}${t.pct ? "%" : ""}</b><extra>${v.label}</extra>`;
      if (state.transform === "mom") {
        return [{ type: "bar", x, y, name: v.label, marker: { color }, hovertemplate: hover, _slot: 0 }];
      }
      return [{ type: "scatter", mode: "lines", x, y, name: v.label, line: { color, width: 2 }, hovertemplate: hover, _slot: 0 }];
    }

    function draw() {
      vSeg && vSeg.update(state.variant);
      tSeg.update(state.transform, { mom: state.variant === "original" });
      rSeg.update(state.range);
      hint.textContent = state.variant === "original" ? "m/m changes are only meaningful on seasonally adjusted data." : "";
      const t = TRANSFORMS[state.transform];
      const tr = traces();
      Plotly.react(chartEl, tr, baseLayout({ pct: t.pct, extra: { bargap: 0.2 } }), PLOT_CONFIG);
      const { v, x, y } = current();
      table && table.refresh(x, [{ label: `${v.label}${t.pct ? " (" + t.label + ")" : ""}`, values: y }], t.pct);
    }

    const onPNG = () => {
      const { v, t } = current();
      exportPNG(ind, traces(), `${v.label} · ${t.pct ? t.short : ind.units}`, t.pct, `${ind.id}_${state.variant}_${state.transform}.png`);
    };
    const onCSV = () => {
      const { v, x, y, t } = current();
      downloadBlob(toCSV(["date", `${ind.short_title} — ${v.label}${t.pct ? " — " + t.short : " — " + ind.units}`], x.map((d, i) => [d, y[i] === null ? "" : +y[i].toFixed(4)])),
        `${ind.id}_${state.variant}_${state.transform}.csv`, "text/csv");
    };

    card.append(
      h("div", { class: "controls" }, vSeg, tSeg, h("span", { class: "spacer" }), rSeg),
      hint, chartEl);
    table = tableView([], [], false);
    card.append(table, footer(ind, onPNG, onCSV));
    card.draw = draw;
    return card;
  }

  // ---------- card: panel of components (EMAE by sector) ----------
  function panelCard(ind) {
    const keys = Object.keys(ind.variants);
    const defaults = [].concat(ind.default.variant).slice(0, MAX_PANEL_SERIES);
    const slots = new Map(defaults.map((k, i) => [k, i]));   // color follows the sector, not its rank
    const state = { transform: ind.default.transform || "yoy", range: rangeFromDefault(ind) };

    const card = cardFrame(ind, true);
    const barEl = h("div", { class: "chart tall", role: "img", "aria-label": "Latest year-over-year change by sector" });
    const lineEl = h("div", { class: "chart", role: "img", "aria-label": "Selected sectors over time" });
    const chipsEl = h("div", { class: "chips", role: "group", "aria-label": "Sectors to compare" });
    const chipsNote = h("span", { class: "chips-note" });
    const tSeg = segmented([["yoy", TRANSFORMS.yoy.label], ["level", TRANSFORMS.level.label]], state.transform, (k) => { state.transform = k; drawLines(); }, "Transformation");
    const rSeg = segmented(Object.keys(RANGES).map((k) => [k, k]), state.range, (k) => { state.range = k; drawLines(); }, "Time range");
    let table;

    // Latest y/y for every sector, at the most recent month all sectors share.
    const yoys = Object.fromEntries(keys.map((k) => [k, transform(ind.dates, ind.variants[k].values, "yoy")]));
    let refIdx = ind.dates.length - 1;
    while (refIdx > 0 && keys.some((k) => yoys[k][refIdx] === null)) refIdx--;
    const refDate = ind.dates[refIdx];

    function freeSlot() { const used = new Set(slots.values()); for (let i = 0; i < MAX_PANEL_SERIES; i++) if (!used.has(i)) return i; return -1; }

    function toggle(k) {
      if (slots.has(k)) { if (slots.size > 1) slots.delete(k); }
      else { const s = freeSlot(); if (s >= 0) slots.set(k, s); }
      drawAll();
    }

    function barTraces(forExport) {
      const order = keys.slice().sort((a, b) => yoys[a][refIdx] - yoys[b][refIdx]);
      const muted = forExport ? "__muted__" : (isDark() ? "#4A5761" : "#C9CED3");
      return [{
        type: "bar", orientation: "h",
        y: order.map((k) => ind.variants[k].label),
        x: order.map((k) => yoys[k][refIdx]),
        marker: { color: order.map((k) => slots.has(k) ? (forExport ? ["#B87333", "#5B9BD5", "#87A96B", "#8E7AB5"][slots.get(k)] : cssVar(SERIES_VARS[slots.get(k)])) : muted) },
        customdata: order,
        hovertemplate: "%{y}: <b>%{x:+.1f}%</b><extra></extra>",
      }];
    }

    function lineTraces() {
      const t = TRANSFORMS[state.transform];
      const start = startDate(ind.last_obs, RANGES[state.range]);
      return [...slots.entries()].sort((a, b) => a[1] - b[1]).map(([k, slot]) => {
        const y = state.transform === "yoy" ? yoys[k] : ind.variants[k].values;
        const idx = ind.dates.map((d, i) => i).filter((i) => (!start || ind.dates[i] >= start) && y[i] !== null);
        return {
          type: "scatter", mode: "lines", name: ind.variants[k].label,
          x: idx.map((i) => ind.dates[i]), y: idx.map((i) => y[i]),
          line: { color: cssVar(SERIES_VARS[slot]), width: 2 }, _slot: slot,
          hovertemplate: `%{y:,.1f}${t.pct ? "%" : ""}<extra>${ind.variants[k].label}</extra>`,
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
      chipsNote.textContent = `Compare up to ${MAX_PANEL_SERIES} sectors. Click a bar or a name to add or remove it.`;
    }

    function drawBars() {
      Plotly.react(barEl, barTraces(false), baseLayout({
        pct: false,
        xaxis: { type: "linear", ticksuffix: "%", zeroline: true, zerolinecolor: cssVar("--ink-2") },
        yaxis: { ticksuffix: "", automargin: true, gridcolor: "rgba(0,0,0,0)" },
        extra: { hovermode: "closest", margin: { l: 10, r: 16, t: 10, b: 36 }, bargap: 0.3 },
      }), PLOT_CONFIG);
    }

    function drawLines() {
      tSeg.update(state.transform); rSeg.update(state.range);
      const t = TRANSFORMS[state.transform];
      const tr = lineTraces();
      Plotly.react(lineEl, tr, baseLayout({ pct: t.pct, extra: { showlegend: true, legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: cssVar("--ink") } }, margin: { l: 52, r: 16, t: 30, b: 36 } } }), PLOT_CONFIG);
      table && table.refresh(tr[0] ? tr[0].x : [], tr.map((s) => ({ label: s.name, values: s.y })), t.pct);
    }

    function drawAll() { drawChips(); drawBars(); drawLines(); }

    const onPNG = () => {
      const t = TRANSFORMS[state.transform];
      exportPNG(ind, lineTraces(), `${t.pct ? t.short : ind.units} · original series`, t.pct, `${ind.id}_${state.transform}.png`);
    };
    const onBarPNG = () => exportPNG({ ...ind, title: `${ind.title}: year-over-year change, ${fmtMonth(refDate)}` }, barTraces(true), "Percent change vs. same month a year earlier · original series", true, `${ind.id}_latest_yoy.png`);
    const onCSV = () => {
      const tr = lineTraces();
      const all = [...new Set(tr.flatMap((s) => s.x))].sort();
      const maps = tr.map((s) => new Map(s.x.map((d, i) => [d, s.y[i]])));
      downloadBlob(toCSV(["date", ...tr.map((s) => `${s.name}${state.transform === "yoy" ? " (y/y %)" : ""}`)], all.map((d) => [d, ...maps.map((m) => (m.has(d) ? +m.get(d).toFixed(4) : ""))])),
        `${ind.id}_${state.transform}.csv`, "text/csv");
    };

    card.append(
      h("p", { class: "hint" }, `Year-over-year change, ${fmtMonth(refDate)}. `,
        h("button", { class: "btn", type: "button", onclick: onBarPNG, style: "padding:1px 8px;font-size:12px" }, "PNG")),
      barEl,
      h("div", { class: "controls", style: "margin-top:14px" }, tSeg, h("span", { class: "spacer" }), rSeg),
      chipsEl, chipsNote, lineEl);
    table = tableView([], [], true);
    card.append(table, footer(ind, onPNG, onCSV));
    card.draw = () => {
      drawAll();
      if (!barEl.dataset.bound) {
        barEl.on("plotly_click", (ev) => { const k = ev.points && ev.points[0] && ev.points[0].customdata; if (k) toggle(k); });
        barEl.dataset.bound = "1";
      }
    };
    return card;
  }

  // ---------- headline tiles ----------
  function tile(ind) {
    const hl = ind.headline;
    const v = ind.variants[hl.variant];
    const series = transform(ind.dates, v.values, hl.transform);
    const last = lastValid(ind.dates, series);
    if (!last) return null;
    const t = TRANSFORMS[hl.transform];
    const orig = ind.variants.original;
    const yoy = orig ? lastValid(ind.dates, transform(ind.dates, orig.values, "yoy")) : null;
    const cls = t.pct ? (last.value > 0 ? " pos" : last.value < 0 ? " neg" : "") : "";
    return h("a", { class: "tile", href: `#ind-${ind.id}` },
      h("div", { class: "tile-label" }, ind.short_title),
      h("div", { class: "tile-value" + cls }, fmtNum(last.value, t.pct)),
      h("div", { class: "tile-sub" }, `${fmtShortMonth(last.date)} · ${hl.transform === "mom" ? "m/m" : t.label}, ${v.label.toLowerCase()}`),
      yoy ? h("div", { class: "tile-sub" }, `${fmtNum(yoy.value, true)} y/y (original series, ${fmtShortMonth(yoy.date)})`) : null);
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
      // Panels span the full width; place single-series cards first so they pair up.
      const ordered = [...list.filter((i) => i.kind !== "panel"), ...list.filter((i) => i.kind === "panel")];
      // With an odd number of single-series cards, the first one spans the full row.
      const singles = list.filter((i) => i.kind !== "panel").length;
      ordered.forEach((ind, n) => cards.append(ind.kind === "panel" ? panelCard(ind) : variantsCard(ind, singles % 2 === 1 && n === 0)));
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
