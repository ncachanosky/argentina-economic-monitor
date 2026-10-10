# Hand-maintained series

| File | Contents | Provided by |
|---|---|---|
| `cpi_private_composite.csv` | Monthly CPI index, Jan 1988 - Aug 2026. Equal to INDEC's CPI before 2007; a composite of private estimates (IPC Congreso-based) for the years INDEC's figures were manipulated and the 2015-16 blackout. The site uses it only for Jan 2007 - Apr 2016. | N. Cachanosky |
| `china_swap_cny.csv` | China (PBoC) swap booked in BCRA gross reserves, CNY bn, from each dated row on. 2014-15 tranches from announcements; Feb-Sep 2015 read from the BCRA weekly balance (no announcements). Update only if the agreement's amount changes. | sourced per row |
| `bcra_fx_repos.csv` | BCRA repos with foreign banks, USD m outstanding from each dated row on (`value`); `short_usd_m`: the part with an original maturity of one year or less (deducted in the IMF-style line). Add a row whenever the BCRA contracts, rolls over or repays a repo; the pipeline warns when the weekly balance's repo line moves without a matching row. | sourced per row |
| `indec_poverty.csv` | INDEC poverty and indigence, total urban agglomerates, % (`value`: persons in poverty; `households`; `indigence`; `indigence_households`), by semester (H1 dated 1 January, H2 1 July). H1 2003 - H1 2013 and H2 2016 on, as INDEC published them (2007-2013 under the intervention; H2 2007 not estimated; nothing published H2 2013 - H1 2016). Add a row each March and September from Cuadro 1 of INDEC's report. | sourced per row |
| `poverty_cedlas.csv` | CEDLAS comparable poverty (`value`) and indigence, persons, %, by semester, H2 2003 - H1 2017: INDEC's 2016 method applied to the household survey with baskets repriced by private CPIs in 2007-2015 (Tornarolli 2018, CEDLAS WP 226, Anexo B). Static. | sourced per row |
| `poverty_uca.csv` | UCA Observatorio de la Deuda Social, poverty (`value`) and indigence, persons, %, annual (its own survey, EDSA, third quarter; dated 1 July), 2004-2024; before 2010 a backward reconstruction from the EPH. Add a row each December. | sourced per row |
| `officials.csv` | Presidents of the BCRA (`role` bcra, since 1935) and ministers of the economy (`economia`, since 1946): `role,name,start,end,note,source`, one row per tenure, `end` empty for the official in office. Not a `date,value` file: read by the `officials` adapter. When an official changes, close the row and add the new one; the pipeline warns when the BCRA's board page or the Ministry of Economy's site no longer names the official in office. | N. Cachanosky (compiled list); corrections noted per row |
| `imf_purchases.csv` | IMF purchases (+) and repurchases (-) under the April 2025 EFF, USD m, on the date of each operation. The IMF-style line deducts their running sum. | sourced per row |

Format: `date,value` first (extra columns allowed: sources, notes, other
values read as `<file>#<column>`). Monthly files use month-start dates; the
net reserves files are dated by event. Changes are
picked up by the next pipeline run and recorded in `data/vintages/`.

## EMAE vintages (emae_vintages/, emae_trend_vintages.csv)

`emae_vintages/` holds INDEC's EMAE informes técnicos as published, one PDF per
monthly release from August 2016 (the September 2018 release is missing).
`emae_trend_vintages.csv` is Cuadro 2 of each release (original, seasonally
adjusted and trend-cycle indices, 2004 = 100, and their changes), one row per
release and month, written by `python -m pipeline.emae_vintages
data/manual/emae_vintages data/manual/emae_trend_vintages.csv`. From October
2026 the daily update stores each new release itself (data/vintages).


## Country-risk corrections (riesgo_corrections.csv)

Hand-checked fixes to the EMBI history that ArgentinaDatos compiles from Ámbito
(adapter `pipeline/sources/riesgo.py`). One row per day: `date`, `value`, `note`.
An empty `value` drops the day (the source has no real quote for it); a value
replaces the source's. Every row needs a note saying what was wrong and how it
was checked. Large one-day moves the source got right stay as published (for
example 30 July 2014).
