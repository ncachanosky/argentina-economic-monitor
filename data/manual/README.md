# Hand-maintained series

| File | Contents | Provided by |
|---|---|---|
| `cpi_private_composite.csv` | Monthly CPI index, Jan 1988 - Aug 2026. Equal to INDEC's CPI before 2007; a composite of private estimates (IPC Congreso-based) for the years INDEC's figures were manipulated and the 2015-16 blackout. The site uses it only for Jan 2007 - Apr 2016. | N. Cachanosky |

Format: `date,value`, one row per month, month-start dates. Changes are
picked up by the next pipeline run and recorded in `data/vintages/`.
