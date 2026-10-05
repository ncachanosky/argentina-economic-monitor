# Argentina Economic Monitor

Interactive, daily-updated charts of Argentina's economy, from
Economic Order. Data are pulled from official sources every day,
validated, stored with their full revision history, and published as a
static site on GitHub Pages.

## How it works

```
registry/series.yaml  ──►  pipeline.update  ──►  data/vintages/*.csv  ──►  pipeline.export  ──►  site/ (GitHub Pages)
   what to track          fetch + validate       append-only history        JSON + CSV            static HTML/JS
```

- **`registry/series.yaml`** is the single source of truth. Each *indicator*
  is one chart card with one or more *variants* (original, seasonally
  adjusted, trend-cycle, or sector components), each mapped to a source id.
- **`pipeline/update.py`** fetches every series, validates it, and appends
  only new or revised observations to `data/vintages/<id>.csv`
  (`vintage, date, value`). Failures are soft: a broken series keeps its
  last good data and is flagged as stale on the site.
- **`pipeline/export.py`** writes `manifest.json` plus one JSON and CSV per
  indicator for the front end, and the full vintage history per indicator.
- **`site/`** is plain HTML/CSS/JS with Plotly. Transformations (y/y, m/m)
  are computed in the browser. Every chart exports a branded 1200×800 PNG
  and a CSV of the current view.
- **`.github/workflows/update.yml`** runs twice a day (07:30 and 17:30
  Buenos Aires), commits any new data, and redeploys.

## Presidential terms

`registry/series.yaml` lists presidential terms (`presidencies`). Series
cards can color the line by term, show per-term statistics (start, end,
change, average, min, max), and rebase the index to the start of any term or
to a custom month. Convention: the handover month belongs to the outgoing
president, so for Milei (inaugurated 10 Dec 2023) Dec 2023 = 100 and his
first month is Jan 2024.

Line colors follow party (`party_colors` in the registry): Peronism (FpV /
FdT) light blue, PRO gold, La Libertad Avanza purple.

## Quarterly data and contributions

Indicators can be monthly (`frequency: M`) or quarterly (`Q`). Quarterly
cards show q/q instead of m/m, and presidency rules apply to quarters (the
handover quarter belongs to the outgoing president). `index_base: "2004"`
shows levels as an index (that year's average = 100) while the "All data"
CSV keeps source units. `kind: contributions` computes each component's
contribution to y/y growth of a total (fixed-base accounts are additive),
with one residual group closing the gap.

## Release calendar

`registry/releases.yaml` holds INDEC's published release dates; each card
shows the next one. INDEC issues its calendar by semester, so when the
listed dates run out the daily run warns in its summary. Add the next
semester's dates from INDEC's *Calendario de difusión*.

## Revision history (real-time data)

Because the store only records changes, the value of any observation *as it
was known on a given day* is recoverable:

```python
from pipeline import store
store.as_of("143.3_NO_PR_2004_A_31", vintage="2026-11-30")   # EMAE s.a., as known then
store.vintages("143.3_NO_PR_2004_A_31")                       # every date it changed
```

History starts on the first pipeline run; earlier vintages are not
reconstructed.

## Adding a series

1. Find the id with the datos.gob.ar search API, e.g.
   `https://apis.datos.gob.ar/series/api/search?q=ISAC`.
2. Add an indicator (or a variant) to `registry/series.yaml`.
3. Run `python -m pipeline.verify` to check every id against the live API.
4. Push. The workflow fetches the new series and redeploys.

New sources (BCRA, etc.) need an adapter in `pipeline/sources/` exposing
`fetch(ids) -> FetchResult`.

## Local development

```bash
pip install -r requirements.txt
python -m pytest -q
python -m pipeline.update            # needs internet access to apis.datos.gob.ar
python -m pipeline.export            # writes site/data/
python -m http.server -d site 8000   # open http://localhost:8000
```

## Branding

Economic Order palette and type, defined as CSS tokens in
`site/css/theme.css`. Chart series use a fixed order (copper, sky, sage,
lavender); terracotta is reserved for highlights because it is too close to
copper to tell apart as a series.

## Data license

Source data: INDEC and other official agencies via
[datos.gob.ar](https://datos.gob.ar/), CC BY 4.0.
