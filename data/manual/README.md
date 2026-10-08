# Hand-maintained series

| File | Contents | Provided by |
|---|---|---|
| `cpi_private_composite.csv` | Monthly CPI index, Jan 1988 - Aug 2026. Equal to INDEC's CPI before 2007; a composite of private estimates (IPC Congreso-based) for the years INDEC's figures were manipulated and the 2015-16 blackout. The site uses it only for Jan 2007 - Apr 2016. | N. Cachanosky |
| `china_swap_cny.csv` | China (PBoC) swap booked in BCRA gross reserves, CNY bn, from each dated row on. 2014-15 tranches from announcements; Feb-Sep 2015 read from the BCRA weekly balance (no announcements). Update only if the agreement's amount changes. | sourced per row |
| `bcra_fx_repos.csv` | BCRA repos with foreign banks, USD m outstanding from each dated row on (`value`); `short_usd_m`: the part with an original maturity of one year or less (deducted in the IMF-style line). Add a row whenever the BCRA contracts, rolls over or repays a repo; the pipeline warns when the weekly balance's repo line moves without a matching row. | sourced per row |
| `indec_poverty.csv` | INDEC poverty and indigence, total urban agglomerates, % (`value`: persons in poverty; `households`; `indigence`; `indigence_households`), by semester (H1 dated 1 January, H2 1 July). H1 2003 - H1 2013 and H2 2016 on, as INDEC published them (2007-2013 under the intervention; H2 2007 not estimated; nothing published H2 2013 - H1 2016). Add a row each March and September from Cuadro 1 of INDEC's report. | sourced per row |
| `imf_purchases.csv` | IMF purchases (+) and repurchases (-) under the April 2025 EFF, USD m, on the date of each operation. The IMF-style line deducts their running sum. | sourced per row |

Format: `date,value` first (extra columns allowed: sources, notes, other
values read as `<file>#<column>`). Monthly files use month-start dates; the
net reserves files are dated by event. Changes are
picked up by the next pipeline run and recorded in `data/vintages/`.
