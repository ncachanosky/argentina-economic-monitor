# Changelog

Versions mark changes to the code and the site, not data updates: the data are
refreshed twice a day between versions. Each tag is a snapshot of the code and
of the data stored on that date (`data/vintages/` keeps every published value).
Numbering: minor versions (1.1, 1.2) add cards or features; patch versions
(1.0.1) fix errors.

## Unreleased

Changes since the last release, recorded as they are made. At the next release this
section becomes its notes.

### Added
- Long Run page: GDP per capita since 1820, Argentina and the world, from the Maddison Project
  Database (releases 2023, 2020, 2018 and 2013, the latest by default; a second release can be
  drawn for comparison). Views: level (log scale), world percentile (share of countries or of
  world population poorer than Argentina; all countries with data, or fixed samples with data
  since 1950 or since 1913) and relative to selected countries and regions (Western Europe 12,
  Latin America); optional base year = 100. Sixteen numbered events of Argentina's economic
  history. Argentina extended past 2022 with INDEC's real GDP growth (dotted). Original
  sources listed for the countries drawn, as the releases' citation policy asks. Five long series
  moved here from other pages, in menu order: trade since 1910 (from International Trade),
  consumer prices since 1943 (Prices), Central Bank financing of the government, two cards
  (Money & Central Bank), and the minimum wage since 1965 (Labor). Their old pages end with a
  line pointing to Long Run, and old links to them (/prices/#ind-cpi_long) go to their new
  place. Registry: `also_on` can show a card on a second page; `see_also` adds a pointer to a
  page.
- Revision tracker: "How Much GDP Gets Revised" (GDP page) and "How Much EMAE Gets Revised"
  (Monthly Activity). First print against today's figure for every quarter or month since
  mid-2016 (q/q or m/m seasonally adjusted, or y/y original), one period's path across
  releases, and the average size and direction of revisions and the sign changes. GDP from
  all 41 releases of INDEC's national-accounts workbooks since September 2016, read once
  (`pipeline/gdp_vintages.py`, stored in data/manual); EMAE from the 121 archived reports;
  later releases from the series the update stores. New methodology section.
- Tracking the current quarter: before GDP is published, an error bar on the quarter in
  progress and a line saying how close INDEC's first GDP print has landed to the EMAE bar
  with as many months published (two quarters in three, nine in ten), from the archived EMAE
  releases since 2016 (about ±1.0, ±0.8 and ±0.4 points with one, two and three months). A
  factor-model nowcast was tested and did no better against the first print, so it was not
  added. The card's summary line is now translated on Spanish pages.
- Recession probability: the real-time line is now the model as it read when each EMAE
  release came out, estimated on that release's data as published (since August 2016), each
  reading kept as it was. Its track record (a median of five months of data to signal a
  recession and three to signal its end, none missed, four false months) replaces the
  earlier one, which re-estimated the model on today's revised data and flattered it.
- RSS feeds, in English and Spanish: a weekly summary every Monday at about 07:30 Buenos Aires
  time (`feed.xml`: last week's new data grouped by page, with the latest m/m and y/y changes
  where the home table has them, the cards that are late, and the coming week's releases) and
  one item per release (`releases.xml`). Linked in each page's header for feed readers and in
  the footer. Links carry `?ref=rss`, so visits from the feeds show in the site's analytics.
- Monthly Activity: Probability of Recession (EMAE-based, Monitor estimate), below the EMAE
  card. A two-regime Markov-switching model of the seasonally adjusted EMAE's three-month
  growth (switching mean, common variance, 2020 outliers), shown as the real-time track record
  (re-estimated each month since 2008 on data up to that month) and today's full-sample
  estimate. Shaded: recessions dated on EMAE with the Bry-Boschan rule; outlined: a decline too
  recent to date. The card says what "recession" means here (a regime of falling activity, not
  two quarters of falling GDP), its track record (a median of four months of data to signal a
  recession, two to signal its end; one false month since 2008) and a cross-check from a factor
  model of EMAE, industry, construction and registered employment. New methodology section.
  Re-estimated only when EMAE changes; statsmodels added to the requirements.

### Changed
- CPI data table: a fixed table of the headline's monthly, year-to-date and 12-month inflation,
  the same whatever the chart shows; oldest month first (it opens at the latest months), with
  a rule at each year change. Other cards can opt in with `data_table` in the registry.
- Where would the exchange rate be (two dates): the span behind the yearly productivity
  change reads in years and months ("10 years and 10 months") instead of decimal years.
- Institutions: the V-Dem cards are one card with a View menu: Argentina's six indices, or any
  one index for Argentina and the comparison countries (electoral democracy and legislative
  constraints on the executive are new in this view). The economic freedom cards are likewise
  one card: the summary rating by country (with Argentina's rank), Argentina's summary and five
  areas, or any one area by country (new). Switching views keeps the time range and the
  countries shown. V-Dem lines now break at missing years (no legislature under military rule)
  instead of joining across them. Links to the old cards still open the right view.
- Eleven pairs of related cards share one card with a switch: capacity utilization (total, by
  industry), GDP (quarterly, since 1993), banks' public-sector exposure (system, by bank
  group), concentration (ten largest, HHI), real and dollar wages, Central Bank financing of the
  government (share of assets, real pesos), exports and imports (level, growth), trade prices
  (exports, imports), spending and revenue drivers, and debt / GDP (ratio, why it changes).
- Prices: a year × month view of the CPI (switch on the CPI card): monthly, year-to-date or 12-month
  inflation for headline, core, regulated and seasonal prices, shaded on a log scale, with each year's
  December-to-December inflation (year to date for the current year) and its average monthly rate
  (geometric mean).
  CSV of the view. New card kind `year_month`.
- Long Run: The Economy under Each Presidency, a table of sixteen measures (inflation, GDP per
  capita, real wages and the minimum wage, unemployment, poverty, the real exchange rate, the
  exchange-rate gap, reserves, country risk, the primary balance, economic freedom, V-Dem) for
  each term since December 1983, from the cards' own data and the site's term convention (the
  handover month belongs to the outgoing president, explained in the card's note). No score,
  ranking or colors. Each row links to its card. Methodology: "The economy under each presidency".
- Long Run: a Table view on the GDP per capita card: Argentina's GDP per capita and world rank in
  benchmark years (1870, 1913, 1929, 1950, 1975, 2000, 2015, the release's last year, and the
  latest year of the INDEC extension), as % of nine comparison countries or regions, each column
  chosen from a menu; or levels. Shares the card's release selector; years are tagged with the
  chart's numbered events.
- International page: Argentina against every country covered by the IMF's World Economic Outlook
  and the World Bank (about 195), annual: growth, GDP per capita (PPP), inflation, investment,
  general government revenue, expenditure, balance and debt, current account, trade openness, FDI,
  unemployment, Gini, and an institutions section (economic freedom, V-Dem, Social Mobility Index).
  IMF projections dashed. Compare with a region, sub-region or bloc (its median and largest
  economies, any country addable); the choice applies to every card. A snapshot table at the top;
  each card also ranks the group for a chosen year. New sources: IMF SDMX API, World Bank API;
  the EFW, V-Dem and SMI adapters also write all-country tables.
- Exported PNG charts carry the Economic Order logo next to the credit line (bottom right).
- International: general government revenue, expenditure and balance share one card with a switch;
  the view, range, year and added countries carry over between them.
- Institutions: the Latin American Left-Leaning Populism Index (Cachanosky, Bastos and Padilla,
  V2025.1), 22 countries, 2000-2019. Three views: over time (any measure, Argentina and chosen
  countries, regional median), a map of the region by year with a year slider and Play, and a
  country's policy path (economic against institutional populist policy, colored by head of
  government). Hover shows each year's head of government (added from public records) and party.
  The card states that the site's editor is one of the index's authors.
- Fiscal: the National Budget card (renamed from "Month by Month") gains a Year by year view: each
  line for every calendar year since 2015 and the latest twelve months, as % of GDP, real change on
  the previous year, or millions of pesos, including the Monitor's adjustments. Registry option
  `year_table` for statement cards.
- Economic freedom and V-Dem cards: Argentina's line in one color by default; "Color by president" turns the
  presidency colors on (and stays on across the card's views). Registry option `term_toggle`.

## 1.2.0 (2026-11-12)

### Added
- Institutions: Conditions for Social Mobility, the Archbridge Institute's Social Mobility Index
  (2026 edition, 132 countries, data for 2024). Three views: a map of Latin America shaded by
  any of the index's ten measures (overall, three pillars, six sub-pillars), the ranking for
  the region or the world, and Argentina's profile by sub-pillar against the regional median,
  with the 25 components. Argentina: 5.75 of 10, 61st of 132, 10th of 21 in the region. The
  edition file is kept in data/manual; the update warns when Archbridge publishes a newer one.
  Framed as conditions for mobility, not mobility outcomes. A tile at the top of the
  Institutions page shows Argentina's score and ranks.
- Today's releases: on days with a scheduled release, a strip above the home dashboard names
  each one, its publisher and the month it covers, links to its chart and says whether the new
  figures are already on the Monitor ("Due today" / "On the Monitor"). Buenos Aires date; no
  time of day.
- BCRA release dates: the update now reads the BCRA's calendar of reports
  (bcra.gob.ar/calendario-de-informes) for the REM survey, the banking report and the FX
  market report, stored in data/calendar_bcra.json. They appear on the home page's "Coming up"
  list and on the release calendar page, with a publisher tag (INDEC or BCRA). They are not tied
  to cards' freshness tags, so they never mark a chart "Late".
- Last-value labels: line charts write each line's latest value just right of its last point,
  in the line's color, pushed apart when close (up to eight lines; none on bars or bands).
- Summary boxes on every dashboard page: Money and Central Bank, Banks, Exchange Rate,
  International Trade, Balance of Payments, Labor and Social, and Institutions now open
  with three or four boxes, like Activity, GDP, Prices and Fiscal.
- Monetary aggregates: the CPI's change over the same period as a dashed reference (a
  vertical line on the bars, a line on the chart), with a button to hide it. When the
  latest month has no CPI yet, the latest CPI month is used and named.
- Every card now carries an update status ("Up to date" in green; "Late" or "Stale" when
  not), including cards without an INDEC release calendar and cards with their own layout.
- Cards built from sources other than INDEC and datos.gob.ar files now also say where their
  data were read from and when they were last checked: the BCRA's API and files, A3
  Mercados, ArgentinaDatos, the REM, the Secretaría de Finanzas and the US Treasury. Cards with their
  own layout (futures curve, balance sheets, debt schedule, placements, REM revisions,
  tenure) show the line too (issue #3). The remaining cards now have it as well: bank
  archives, hand-kept files (linked on GitHub), the Fraser Institute and V-Dem files, and
  the cards the Monitor computes (monthly nominal GDP, EMAE turning points, seasonally
  adjusted EMAE by sector), which name the files they are built from.
- Country risk: Brazil, Chile, Colombia, Mexico, Peru and Uruguay drawn with Argentina on
  the same card (log scale), with Ecuador, Bolivia, Paraguay and Panama one click away.
  From the Banco Central de la República Dominicana's daily file of JP Morgan's EMBI Global
  Diversified subindices, since 2007; that file's Argentina column is also the fallback if
  ArgentinaDatos fails. The Latin America aggregate is left out because of breaks when
  countries enter or leave the index (issue #3).
- Three measures of inflation: 2Y and 5Y ranges.
- `python -m pipeline.update --only <source>` updates a single source (for local runs).

### Changed
- Institutions: simpler defaults for first-time readers. Consumer confidence opens with the
  national index only; Economic Freedom with Argentina and the Latin American median;
  V-Dem (Argentina) with liberal democracy only; the four V-Dem comparison cards with
  Argentina only. The other lines stay one click away.
- Last-value labels show two decimals for indices below 1 (V-Dem).
- Home table: every row now has a number in "Latest", in bold with its date below: the level
  where the series has one, otherwise the latest change in the view shown (m/m, q/q or
  y/y). On phones the sparkline column gives way so the latest month stays in view, and
  the group names (Activity, Prices...) stay visible when the table is scrolled sideways.
- Log-scale charts spanning less than three orders of magnitude label the axis at 1, 2 and
  5 times powers of ten instead of every unit, with finer steps (1,000, 1,200, 1,500...)
  when the window is narrow, such as two years of the exchange rate.

### Fixed
- Tracking the current quarter: marked "Late" although it had August's EMAE. The card is
  quarterly and its last quarter is dated by its first month (July), so the release check
  thought August was missing; it now uses the latest EMAE month.
- Dollar futures curve: did not load. A3 Mercados stopped sending the implied rate, and
  the missing values were written as NaN, which is not valid JSON. They are now null.
- CPI: the REM expectations disappeared in the "By presidency" view; they are now drawn
  in every view.
- Downloaded PNGs: a long source note ran into the "Argentina Economic Monitor" brand in the
  bottom-right corner (48 of 141 cards). The note now wraps to the space left of the brand
  and the bottom margin grows to fit. On Spanish pages, "Latest observation" in the note
  is now translated, and log-scale exports get the same tick labels as the page.
- Real exchange rate (ITCRM and bilateral indices): the BCRA renamed its workbook
  (ITCRMSerie.xlsx to itcrm-serie.xlsx) and left the old file frozen on October 16, so
  the series stopped updating. The link is now read from the BCRA's page.

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
