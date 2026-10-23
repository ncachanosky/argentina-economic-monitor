# Changelog

Versions mark changes to the code and the site, not data updates: the data are
refreshed twice a day between versions. Each tag is a snapshot of the code and
of the data stored on that date (`data/vintages/` keeps every published value).
Numbering: minor versions (1.1, 1.2) add cards or features; patch versions
(1.0.1) fix errors.

## Unreleased

Changes since the last release, recorded as they are made. At the next release this
section becomes its notes.

## 1.1.0 (2026-10-23)

### Added
- Yearly view: GDP by industry and investment by type (contributions to annual growth),
  the net investment position by sector and what firms and households hold abroad
  (end-of-year stocks).
- Quarterly and yearly views of what creates base money (each period's change, over the
  stock at the end of the previous period).
- The national budget by presidency: each line's real change over each term (its last
  twelve months against the twelve months before the term began).
- New chart: the BCRA's credit to the government in real terms, and how it changes.
- Interest rates: the policy rate (until it was discontinued in July 2025).
- EMAE by sector, seasonally adjusted by the Monitor (X-13ARIMA-SEATS; not official data),
  with month-on-month, year-to-date, year-over-year and by-presidency views. The method is
  on the methodology page.
- New chart: why debt / GDP changes, split into new debt, the exchange rate, real GDP
  growth and inflation.
- International reserves: all three net measures drawn by default.

### Changed
- Trade by partner (exports, imports and balance) is read from the tables of INDEC's own
  release (ica_cuadros and its annex) for the current and previous year, so the cards update
  on release day instead of waiting a month for datos.gob.ar, whose file stays the source
  for earlier months and the fallback. From January 2025 the United States includes its
  associated territories (Puerto Rico above all), as INDEC's tables do: about 2.4% more
  than the United States alone. Noted on the cards and in the methodology.
- The footer shows the site's version, linked to the release notes.
- The beta label and the beta note on the home page are removed.
- The table by presidency shows in every view of a chart, not only "By presidency"; the
  seasonally adjusted views (EMAE, GDP, IPI, ISAC) also show the original series as a
  faint dashed line.
- Revenue and spending, by presidency: all four lines with the terms shaded behind, and
  a table of each line's average by term.
- Financial balance (cash, accrual, operational): terms shaded behind the lines instead
  of a by-presidency view.
- Real exchange rate calculator: both questions show the same four results (the change in
  the real exchange rate, the rate that restores the first date or reference, the required
  change in relative productivity, the terms of trade).
- Poverty and indigence: ranges "Since 2016" (INDEC's current method) and "Max".

### Fixed
- Trade by partner: switching between exports, imports and the balance keeps the chosen
  frequency and range.
- Contribution charts: the component lines below no longer leave an empty chart in the
  yearly view.

## 1.0.0 (2026-10-20)

First release.

### Coverage
- 135 charts in 11 topics: monthly activity, national accounts, international
  trade, balance of payments, prices, money and the central bank, the exchange
  rate, banks, labor and poverty, fiscal accounts and debt, and institutions.
- Constructed series: a long-run CPI spliced across bases (with a private
  composite for 2007–2015), long-run GDP chain-linked across the 1993 and 2004
  bases, fiscal balances over twelve-month GDP, real wages, and a real exchange
  rate calculator.
- Release calendar, freshness tag on every chart, and a link to the source
  file each chart was read from.
- Full English and Spanish sites; `/llms.txt` summary of the latest values.

### Data sources and checks
- Series are read from the publishers' own files where there is a reader:
  INDEC (CPI, EMAE and its sectors, wages, IPI, ISAC, national accounts, foreign
  trade, wholesale prices, capacity utilization, the generation-of-income
  account, the Gini), the Secretaría de Trabajo (registered employment),
  Seguridad Social (RIPTE) and the BCRA.
- Every other datos.gob.ar series is read from its full CSV download
  (`registry/datos_catalog.json`); the time-series API is only a last fallback.
- Each update is compared with the stored history before publication; a
  series that fails keeps its last good data. A changed or missing source file
  opens a GitHub issue.
- Discontinued series are kept as fixed snapshots in `data/manual/`.
