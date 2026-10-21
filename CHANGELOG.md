# Changelog

Versions mark changes to the code and the site, not data updates: the data are
refreshed twice a day between versions. Each tag is a snapshot of the code and
of the data stored on that date (`data/vintages/` keeps every published value).
Numbering: minor versions (1.1, 1.2) add cards or features; patch versions
(1.0.1) fix errors.

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
