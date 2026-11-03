# NYC After-Hours Lighting: Implementation Plan

Oct 20, 2026 · companion to *NYC After-Hours Lighting Project Plan.md* (revised Oct 20)

The proposal sets the questions, data and phases. This document turns it into work you can start on Monday: a fact-check of every number the proposal relies on, a first estimate built from verified inputs, the repo and database layout, and a week-by-week task list where every task ends in a check you can run. Budget: 10 weeks × 8–10 h ≈ 90 hours.

The six design changes from the first review have been applied to the proposal: the shutdown reframe, five ComStock counties, the bill number, monthly Black Marble, CO2 fallbacks and the LL84 check. Savings now come before forecasting, and the forecasting work starts with the 2020 counterfactual.

---

## 1. Fact-check log

Each statistic was checked against a primary source where one could be found, or computed directly from public data. "Computed" means calculated for this plan from the raw files: NYISO 2025 monthly CSVs (24 zips from mis.nyiso.com) and NYC Open Data API queries.

### 1.1 Corrected or replaced

| Claim in the Sep 29 draft | What the sources say | Source | Change |
| --- | --- | --- | --- |
| The bill is "Intro 896 and Intro 1039, reintroduced Aug 2026" | Those were earlier versions (Int 1039-2023, Int 896-2024 by Moya). The current bill is **Int 0998-2026**, introduced Aug 13, 2026 by Harvey Epstein with 16 sponsors, in the Committee on Environmental Protection and Waterfronts. No hearing yet | [Legistar](https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=8169984&GUID=B394ED89-C70F-43FF-8922-D468D4758EF0) | Replaced throughout |
| The bill covers "private business and retail buildings" | It covers buildings whose main use is Group **B, E or M**, so **educational buildings are now included**. Interior and exterior, year-round, night | Legistar summary | Scenario (b) adds Group E |
| "REBNY has opposed it" | Couldn't confirm a REBNY position on Int 0998-2026. REBNY opposed a 2015 lights-out bill | [The Real Deal, 2015](https://therealdeal.com/2015/04/28/rebny-to-battle-lights-out-bill-wednesday/) | Softened; check hearing testimony |
| France saves "about 1,000 GWh a year" | ADEME estimated **2 TWh/yr** before the rule took effect (2013); the ministry cited ~1 TWh in 2018. **Neither is a measured result.** The rule is an *arrêté* (not a decree) of 25 Jan 2013, in force July 2013 | [Assemblée nationale Q84260](https://www.assemblee-nationale.fr/dyn/14/questions/QANR5L14QE84260.pdf); [Connexion](https://www.connexionfrance.com/article/Practical/Environment/Lights-out-for-French-businesses-every-night) | Shown as a 1–2 TWh range of estimates |
| France "enforced from 2018" | In force from July 1, 2013; updated by an arrêté of Dec 27, 2018 | Same | Corrected |
| 2021 Lights Out laws "passed Dec 2021" | Passed Dec 15, 2021, but numbered **Local Laws 30 and 31 of 2022**. Migration windows: Apr 1–May 31 and Aug 15–Nov 15 (the NYC Bar report says Nov 1; check the law text). Sensors: 25%/40%/75%/100% of city buildings by 2023/2025/2027/2030 | [intro.nyc LL31](https://intro.nyc/local-laws/2022-31), [LL30](https://intro.nyc/local-laws/2022-30) | Cited by number |
| LL88 covers "50,000+ sq ft" | Over 50,000 gross sq ft, *or* two or more buildings on one lot totaling over 100,000 sq ft. The Jan 1, 2025 lighting deadline came from LL134 of 2016 | [LL88 text](https://www.nyc.gov/assets/buildings/pdf/ll88of2009.pdf), [LL134](https://www.nyc.gov/html/gbee/downloads/pdf/ll134of2016.pdf) | Added the multi-building rule |
| "55% of large lit buildings used occupancy sensors" in 2012 | Correct, from the 2012 CBECS (16% of all lit buildings). A secondary summary of CBECS 2018 says ~46% of floor space, up from 41% | [EIA](https://www.eia.gov/todayinEnergy/detail.php?id=31272); [LightNow](https://www.lightnowblog.com/2022/01/national-building-stock-study-reveals-ongoing-lighting-upgrade-opportunity) | Kept; 2018 figure flagged as secondary |
| Pull "the New York County aggregate" from ComStock | Zone J is all five counties | — | Five counties |
| Aggregate daily VNP46A2 to monthly | NASA publishes **VNP46A3** monthly composites (snow-free, with a quality flag); blackmarblepy 2026.6.1 supports A1–A4 | [blackmarblepy source](https://github.com/worldbank/blackmarblepy) | Use A3; keep A2 for spring 2019/2020 |
| Satellite passes "about 1:30 a.m. local" | 1:30 a.m. *local solar time* ≈ 1:25 a.m. EST ≈ 2:25 a.m. EDT | NASA | Caveat added |
| LL84 monthly data for the difference-in-differences | `fvp3-gcb2` holds **CY2018–2021, 2023, 2024**: no 2022, no 2025. The annual dataset goes up to report year 2024 | Computed (Open Data API, Oct 20, 2026) | Causal stretch on hold |
| "Supabase or Neon" free hosting | Supabase free: 500 MB, pauses after 1 week without activity. Neon free: **1 GB per project**, scale-to-zero, PostGIS included | [Supabase](https://supabase.com/pricing), [Neon](https://neon.com/pricing) | Neon |
| Savings should "sit inside the 2020 shutdown drop" | Logically wrong: lights left on in empty buildings aren't in the drop | — | Replaced with sanity bounds |

### 1.2 Confirmed as stated

| Claim | Source |
| --- | --- |
| Lighting ≈ 17% of US commercial electricity in 2018, ≈ 208 billion kWh | [EIA FAQ](https://www.eia.gov/tools/faqs/faq.php?id=99&t=3) (CBECS 2018) |
| LL84 thresholds: private 25,000+ sq ft, city-owned 10,000+ sq ft | NYC Open Data dataset description |
| LL97 grid coefficient 0.000288962 tCO2e/kWh, 2024–2029 (falls to ~0.000145 for 2030–2034) | [NYC DOB](https://www.nyc.gov/assets/buildings/pdf/presentations/AEE_March-2024.pdf), [NY Engineers](https://www.ny-engineers.com/blog/new-local-law-97-rules-for-nyc-building-owners-what-has-changed). One source labels it per kBtu; the DOB formula uses per kWh |
| NY PAUSE took effect Mar 22, 2020; NYC Phase 1 June 8; Phase 2 June 22 | News coverage at the time |

### 1.3 New numbers added (computed)

| Number | Value | How |
| --- | --- | --- |
| Zone J annual load, 2025 | **50,104 GWh** | Sum of `palIntegrated` N.Y.C., 8,760 hours, no gaps |
| Zone J mean / min / max, 2025 | 5,720 / 3,679 / 10,825 MW | Same |
| Mean load 1–5 a.m. vs 1–5 p.m. | **4,768 MW** vs 6,309 MW (ratio 0.76) | Hour-beginning 01–04 vs 13–16, local |
| 1–5 a.m. floor by month | 4,078 MW (May) to 6,388 MW (Jul) | Summer nights carry cooling load, which is why temperature control matters |
| Weekday vs weekend nights | 4,792 vs 4,707 MW (**~85 MW apart**) | A first hint that overnight load barely tracks the work week |
| Day-ahead price, N.Y.C. | **$49.37/MWh** at 1–5 a.m. vs $65.41 all hours | `damlbmp_zone`, 2025 |
| NYC commercial floor area (PLUTO `comarea`) | **1.83 billion sq ft** (MN 815M, BK 406M, QN 346M, BX 197M, SI 65M) | PLUTO 26v2 via Open Data API |
| Office / retail floor area (any building) | 675M / 294M sq ft | Same |
| Floor area by building class: O (office) / K (store) / W (educational) | 483M / 166M / 250M sq ft → **~0.9 billion sq ft ≈ the bill's B/E/M scope** | Same |
| US commercial lighting intensity | 208B kWh ÷ 96B sq ft ≈ **2.2 kWh/sq ft/yr** (≈ 0.25 W/sq ft averaged over the year) | CBECS 2018 |
| DOE reference office lighting schedule, overnight | **5%** of installed lighting | [DOE TSD, 2012](https://www1.eere.energy.gov/buildings/appliance_standards/pdfs/ashrae_final_rule_tsd_app04a_characteristics_2012_05_01.pdf) |
| NY household electricity use | 566 kWh/month (≈ 6.8 MWh/yr), 2023 | [EIA Table 5A](https://www.eia.gov/electricity/sales_revenue_price/pdf/table_5A.pdf) |
| NY commercial retail price | ≈ 18–21¢/kWh statewide in 2025; Con Ed territory is higher | EIA Electric Power Monthly via NYSERDA (only partly confirmed; get the exact figure in week 1) |

### 1.4 Still unverified (check in week 1)

- **NYISO IMER history:** the emissions page confirms zonal marginal rates for the day-ahead and real-time markets, but not how far back they go or whether you can download them in bulk. The average-rate calculator is limited to one day per request.
- **ComStock lighting-controls upgrade:** NREL published *ComStock Measure Documentation: Interior Lighting Controls* (NLR/TP-5500-96597, 2026). Check whether the public release includes it as an upgrade, with county aggregates.
- **ComStock building-type coverage:** I couldn't find a published "share of floor area modeled" figure. Measure it yourself against PLUTO.
- **The CBECS 2018 occupancy-sensor share** (46% of floor space) is from a secondary summary. Confirm it in EIA's 2018 tables.
- **Meteostat station ID for Central Park:** likely `72505` (KNYC); confirm on Meteostat.
- **NYC reopening Phases 3 and 4** (July 6 and July 20, 2020): confirm before hard-coding.
- **LL88 and dwelling units:** confirm in Admin Code §28-310 whether lighting inside dwelling units is excluded (it doesn't affect B/E/M scenarios much).
- **After-hours field evidence:** a University of Washington study found lights on in *empty* offices 40–50% of the time ([UW](https://sustainability.uw.edu/blog/node/205)). Masoso & Grobler (2010, *Energy and Buildings* 42:173–177) report that 56% of office energy was used outside working hours, mostly from lights left on. That finding is from offices in Botswana and South Africa, and I confirmed it only secondhand, so cite it as context, not as an NYC input.

---

## 2. First estimate (built from verified inputs)

The reporting rule says to report the number whatever it is, so it helps to know the likely size in advance. Every input below is either cited above or marked as an assumption.

| Input | Value | Basis |
| --- | --- | --- |
| Floor area in the bill's scope (Groups B/E/M) | 0.9 billion sq ft | PLUTO building classes O + K + W |
| Installed interior lighting density | 0.8 W/sq ft | **Assumption.** Consistent with CBECS's 0.25 W/sq ft annual average at ~30% use |
| Overnight on-fraction | 5% (low) to 25% (high) | DOE schedule (low); **assumption** informed by field studies of empty offices (high) |
| **Overnight lighting load** | **36–180 MW** | 0.9B × 0.8 W × fraction |
| Share of the 2025 1–5 a.m. floor (4,768 MW) | **0.8–3.8%** | Computed |
| Annual energy at 6 h/night | 79–394 GWh/yr | × 2,190 h |
| Share of Zone J 2025 load (50,104 GWh) | **0.16–0.79%** | Computed |

If a policy removes half of that:

| Measure | Low | High |
| --- | --- | --- |
| Energy | 39 GWh/yr | 197 GWh/yr |
| Wholesale value ($49.37/MWh, 2025 overnight day-ahead) | $1.9M | $9.7M |
| Retail value (~20¢/kWh) | $7.9M | $39M |
| CO2 (LL97 factor, 0.289 t/MWh) | 11,400 t | 57,000 t |
| NY households' annual use (6.8 MWh each) | ~5,800 | ~29,000 |

**What this means for the project:**

- **The citywide grid share will be small,** probably under 1% of annual load. The brief should lead with absolute tons, per-building dollars and the bird case. This is the reporting rule working as intended, not a weakness.
- **The overnight on-fraction drives the answer.** It changes the result five-fold, more than everything else combined. In ComStock it comes from schedules, not meters. That's why it sits at the top of the tornado chart, and why it's worth asking the NYC Bird Alliance and the coalition for field audit data.
- **Weekday and weekend nights differ by only about 85 MW in 2025.** That's in the same range as the low end of this estimate. It suggests (doesn't prove) that whatever burns overnight doesn't follow the work week, which fits the hypothesis.
- **Leave exterior lighting and signs out of the headline for now.** The bill covers them, but this estimate doesn't include them. Add them from ComStock's exterior lighting end use in Phase 3.
- **If your measured number lands outside roughly 20–600 GWh/yr, treat it as a bug until you've proven otherwise.**

---

## 3. Priorities (as now reflected in the proposal)

| Tier | Phases | When it's done, you have |
| --- | --- | --- |
| **Core** (must ship) | 0–3: grid, weather, ComStock, PLUTO/LL84 → questions 1, 2 and 4, with a bottom-up savings range | The missing number, ready for the brief |
| **Strong** | Black Marble map (question 3), 2020 counterfactual, Tableau dashboard | A public, visual, refreshing product |
| **Stretch** | Production 1–7-day forecast and backtest vs NYISO; LL84 difference-in-differences or regression discontinuity (only once CY2025 monthly data is published) | Data science portfolio depth |

---

## 4. Repo, database and tooling

### 4.1 Repo layout

```
nyc-lights/
├── README.md                    # statement, 3 headline numbers, dashboard link, diagram
├── pyproject.toml               # pinned deps (uv); Python 3.12
├── .env.example                 # PG_URL_LOCAL, PG_URL_REMOTE, NYC_APP_TOKEN, BLACKMARBLE_TOKEN, GSHEETS_SA_JSON
├── .github/workflows/
│   ├── nightly.yml              # grid + weather (+ forecast) + sheet extract
│   └── ci.yml                   # lint + unit tests + dbt build on a throwaway PostGIS container
├── ingest/
│   ├── common.py                # db engine, upsert helper, logging, retry
│   ├── nyiso.py                 # palIntegrated, isolf, damlbmp/realtime zone CSVs
│   ├── weather.py               # Meteostat, KNYC
│   ├── ll84.py                  # sodapy: annual (5zyy-y8am) + monthly (fvp3-gcb2)
│   ├── pluto.py                 # MapPLUTO 26v2 → ogr2ogr / geopandas
│   ├── comstock.py              # OEDI county aggregates, 5 counties
│   ├── blackmarble.py           # VNP46A3 monthly; VNP46A2 for spring 2019/2020
│   └── emissions.py             # IMER export + static LL97/eGRID factors
├── dbt/                         # models/raw, staging, mart; tests/
├── models/
│   ├── features.py
│   ├── savings.py               # scenarios + Monte Carlo ranges + tornado
│   ├── counterfactual_2020.py
│   ├── forecast.py              # seasonal naive, regression+ARIMA errors, LightGBM
│   └── backtest.py
├── dashboard/
│   ├── export_sheets.py         # gspread writer
│   ├── cells.geojson            # static map geometry
│   └── README.md                # sheet → Tableau field map, workbook link
├── report/
│   ├── index.qmd  methods.qmd  results.qmd  limitations.qmd
│   └── brief.qmd                # 2-page PDF
├── notebooks/                   # exploration only; nothing downstream reads these
└── docs/
    ├── access_log.md            # week-1 source checks
    └── data_dictionary.md
```

### 4.2 Database

Keep the heavy spatial data local. Neon's free tier gives 1 GB per project, but PLUTO with geometry plus raster cells can use most of that, and the nightly job doesn't need them.

| Where | What lives there |
| --- | --- |
| **Local** Postgres.app + PostGIS | Everything: raw, staging, mart, including PLUTO geometry and raster cells |
| **Neon** (free; PostGIS) | Time series (load, forecast, LBMP, weather, forecasts) plus the *finished* spatial and savings marts, pushed with `pg_dump -t 'mart.*' \| psql` after each manual rebuild |

**Core tables** (all timestamps `timestamptz` in UTC; Eastern time is derived only in marts):

```sql
raw.nyiso_load        (zone text, ts_utc timestamptz, load_mw numeric, source_file text, loaded_at timestamptz,
                       PRIMARY KEY (zone, ts_utc))
raw.nyiso_load_fcst   (zone, issued_date date, ts_utc, load_mw,  PRIMARY KEY (zone, issued_date, ts_utc))
raw.nyiso_lbmp        (zone, market text /*DAM|RT*/, ts_utc, lbmp numeric, PRIMARY KEY (zone, market, ts_utc))
raw.weather_hourly    (station, ts_utc, temp_c, rhum, dwpt_c, PRIMARY KEY (station, ts_utc))
raw.ll84_annual       (property_id, report_year, nyc_borough_block_and_lot text, ... all source columns as text)
raw.ll84_monthly      (property_id, calendar_year, month text /* e.g. '20-Feb' */, electricity_use_kbtu text, ...)
raw.pluto             (bbl, landuse, bldgclass, bldgarea, comarea, officearea, retailarea, numfloors, yearbuilt, geom)
raw.comstock_county   (county_fips, building_type, ts_local, upgrade int, enduse text, kwh numeric, sqft numeric)
raw.blackmarble_month (cell_id, month date, radiance numeric, qa_flag smallint, geom)

staging.ll84_bbl      -- split multi-BBL entries → one row per BBL, area-weighted
staging.calendar      -- date, dow, fed_holiday, covid_phase (pre/pause/ph1–4/post), dst flag
staging.data_quality  -- per source: expected vs actual hours, dup keys, nulls, last loaded_at

mart.hourly_load      -- ts_utc, ts_local, hour_local, load_mw, temp_c, hdh, cdh, calendar flags
mart.overnight_daily  -- date, floor_mw (1–5 a.m. mean), day_mw (1–5 p.m.), ratio, 28-day rolling
mart.lighting_share   -- county, building_type, hour, lighting_kwh_per_sqft, share_of_bldg_load
mart.cells            -- cell_id, geom, radiance_median_2025, commercial_floor_share, residential_share, boro_pct_rank
mart.cell_shutdown    -- cell_id, radiance Δ (Apr–May 2020 vs 2019)
mart.building_eui     -- bbl, property_type, sqft, elec_kwh_per_sqft, ll88_covered, bill_group (B|E|M|null), bill_exempt
mart.savings          -- scenario, variant (off|dim30), uptake, bound (p10|p50|p90), mwh, usd_wholesale, usd_retail,
                         tco2_imer, tco2_ll97, tco2_egrid
mart.counterfactual   -- ts_utc, actual, predicted, lo, hi
mart.forecasts        -- issued_at, target_ts, model, horizon_h, yhat, lo, hi
```

### 4.3 Time handling (the bug most likely to bite)

- `palIntegrated` has a local "Time Stamp" (hour beginning) plus an EST/EDT column. The fall-back hour appears twice, once per flag; convert to UTC once, in ingest. `damlbmp_zone` has **no** time-zone column, so for the repeated November hour, assign EDT then EST by row order, and test it.
- "1–5 a.m." means **local** hours beginning 01–04. DST nights have 3 or 5 such hours, so compute floors as means.
- Use `palIntegrated` (hourly, validated by NYISO), not the 5-minute `pal` series.

---

## 5. Week-by-week

Hours are estimates; the right-hand column is your proof that a task is finished.

### Week 1: Setup and access checks (≈9 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 1.1 | `git init`, repo skeleton, README with the statement, push to GitHub (public) | 1 | Repo visible |
| 1.2 | Postgres.app + PostGIS locally; Neon project with `CREATE EXTENSION postgis`; `dbt init`; schemas on both | 2 | `dbt debug` passes against both targets |
| 1.3 | Tokens: NASA Earthdata (as `BLACKMARBLE_TOKEN`), NYC Open Data app token, Google service account with a Sheet shared to it; add as GitHub secrets | 1 | A secrets-check step in a dummy workflow passes |
| 1.4 | **Access checks** for each item in §1.4: IMER history, the ComStock release and lighting-controls upgrade, the Meteostat ID, whether LL84 CY2025 is out, the exact EIA NY commercial price | 4 | `docs/access_log.md` table: source → works? → coverage → caveats |
| 1.5 | Legistar: Int 0998-2026 status, committee, any hearing date; set a reminder to re-check in weeks 5 and 9 | 0.5 | Recorded in README "Why now" |
| 1.6 | Decide on IMER vs fallback for the central CO2 figure | 0.5 | Logged in README |

### Weeks 2–3: Ingestion (≈18 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 2.1 | `ingest/common.py`: engine from env, `upsert(df, table, keys)` with `ON CONFLICT DO NOTHING`, retry with backoff, logging | 2 | Unit test: upserting the same frame twice leaves the row count unchanged |
| 2.2 | NYISO backfill 2016→today: `palIntegrated`, `isolf` archives, `damlbmp` and `realtime` zone CSVs. Monthly zips, cached on disk | 4 | ~87.6k load rows; 2025 total = 50,104 GWh (matches §1.3) |
| 2.3 | Weather backfill; interpolate single missing hours, flag longer gaps | 1.5 | <0.5% interpolated, flagged |
| 2.4 | PLUTO 26v2 via ogr2ogr; LL84 annual (report years 2022–2024) + monthly (2018–2021, 2023–2024) | 3 | Over 95% of LL84 rows match a PLUTO BBL after splitting multi-BBL rows; `comarea` total = 1.83B sq ft |
| 2.5 | ComStock: 5 counties × building types × end uses (interior lighting, exterior lighting, interior equipment, total); baseline plus lighting controls if available | 3 | Modeled floor area per county compared with PLUTO; unmatched types listed |
| 2.6 | Black Marble: VNP46A3 2019→latest (keep `qa_flag`; use flag 0 only for the map); VNP46A2 daily for Mar–Jun 2019 and 2020. Check that pixels line up with a basemap (people often misplace the sinusoidal grid when reprojecting) | 3 | Times Square is the brightest cell in Midtown; Central Park is dark |
| 2.7 | `nightly.yml`: cron 10:00 UTC; ingest yesterday's grid, LBMP and weather; then `dbt test`; fail loudly | 1.5 | Green 3 nights in a row |

### Weeks 3–5: SQL and exploration (≈18 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 3.1 | `staging.calendar` with COVID phases (PAUSE 2020-03-22; NYC Ph1 06-08; Ph2 06-22; Ph3/Ph4 confirmed in week 1) | 1 | Flags join with no nulls |
| 3.2 | `mart.hourly_load` with degree hours (base 65°F / 18.3°C) | 1.5 | dbt tests pass |
| 3.3 | `mart.overnight_daily` + 28-day rolling floor; weekday vs weekend vs holiday nights | 2.5 | **Question 1 query**; 2025 reproduces 4,768 MW, and weekday/weekend 4,792/4,707 MW |
| 3.4 | Shutdown, first pass: Apr–May 2020 vs the same weeks of 2019, temperature-binned, **day and night separately** | 2 | Two-panel chart; the day–night contrast written down |
| 3.5 | `mart.lighting_share`: share at 1–5 a.m. vs 1–5 p.m. by type and county; 24-hour profile per type | 2.5 | **Question 2 query**; compare the overnight fraction with DOE's 5% |
| 3.6 | `mart.cells`: area-weighted commercial floor share per cell; 2025 median radiance; percentile rank within each borough | 3 | **Question 3 query**: top 25 bright commercial cells vs bright residential cells |
| 3.7 | `mart.cell_shutdown`: Δ radiance spring 2020 vs 2019 | 2 | One map plus the median Δ for commercial vs residential cells |
| 3.8 | `mart.building_eui`: drop outliers within each property type (1st–99th percentile of EUI); `ll88_covered` (>50k sq ft or >100k multi-building); `bill_group` from PLUTO class/LL84 type → B/E/M; `bill_exempt` | 2 | Floor area by `bill_group` ≈ O/K/W totals in §1.3 |
| 3.9 | Exploration notebook → 6 draft charts | 1.5 | Saved to `report/figs/` |

**Checkpoint, end of week 5:** questions 1–3 answered by queries. Write a one-paragraph "what we know so far" in the README. Re-check Legistar.

### Weeks 5–7: Savings, the core deliverable (≈14 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 4.1 | Floor area by (county, ComStock type, LL88 flag, bill group) | 2 | Crosswalk in the data dictionary |
| 4.2 | Overnight lighting kWh/sq ft by type and county from ComStock (interior, plus exterior separately) | 1 | Feeds `savings.py` |
| 4.3 | Scenarios: (a) LL88 full compliance (baseline vs lighting controls for buildings over 50k sq ft, minus assumed existing compliance, seeded from CBECS's 55%/46%); (b) Int 0998-2026 for B/E/M, **off** and **dimmed to 30%**; (c) last-one-out at 25/50/75% uptake | 3 | One row per scenario × variant × uptake |
| 4.4 | Value it: $49.37/MWh overnight day-ahead (2025; recompute per year) to the retail rate from week 1; tCO2 × IMER, LL97 and eGRID | 1.5 | $ and tCO2 columns filled |
| 4.5 | Monte Carlo over the on-fraction, lighting density, existing compliance, policy removal share and floor-area match → P10/P50/P90; tornado | 3 | Tornado PNG |
| 4.6 | Sanity bounds: savings < in-scope overnight lighting < Zone J floor × commercial share; compare with the §2 estimate (20–600 GWh/yr) and with France's 1–2 TWh scaled by floor area | 1.5 | All hold, or the report says why not |
| 4.7 | *(Stretch, on hold)* Difference-in-differences or regression discontinuity once CY2025 monthly LL84 is published | 2+ | Event-study plot, or a one-line note |

### Weeks 6–8: Counterfactual and forecasting (≈14 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 5.1 | Features: hour × day-of-week, holiday, temperature splines or HDH/CDH, 24/168-hour lags, temperature × hour | 2 | `features.py` unit-tested on a DST week |
| 5.2 | **Counterfactual 2020:** fit Jan 2016–Feb 2020, predict Mar–Jul 2020; hourly gap with a bootstrap interval; day vs night | 4 | Headline: "overnight load fell X% (CI) vs Y% daytime" → `mart.counterfactual` |
| 5.3 | *(If time)* Production models: seasonal naive; SARIMAX with exogenous variables; LightGBM quantile | 3 | `mart.forecasts` rows |
| 5.4 | *(If time)* Rolling-origin backtest, 12 months, 1–7-day horizons; MAPE + 80% coverage, overall and 1–5 a.m.; vs the NYISO forecast | 3 | Results table committed |
| 5.5 | *(If time)* Nightly forecast from a saved model; weekly retrain | 2 | Forecast rows appear nightly |

### Weeks 8–9: Dashboard (≈10 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 6.1 | `export_sheets.py`: last 14 days hourly (actual + forecast if built); monthly overnight ratio; 2020 counterfactual (daily); `mart.cells` attributes; `mart.savings`; backtest summary. Each tab < 50k cells | 2 | Sheet populated nightly |
| 6.2 | `cells.geojson` static geometry joined in Tableau on `cell_id` | 1 | Map renders |
| 6.3 | Tableau Public, 4 views: **When**, **Where** (brightness + the 2020 change layer), **What it's worth** (scenario / off-vs-dim / uptake parameters → MWh, $, tCO2, households at 6.8 MWh), **Trust** | 5 | Published; checked on a phone |
| 6.4 | Clicking a cell shows its building mix by type and aggregate EUI, never a "wasteful building" label | 0.5 | Copy reviewed |
| 6.5 | 7-night unattended run | 1.5 | 7 consecutive dated refreshes |

### Weeks 9–10: Publish (≈9 h)

| # | Task | h | Done when |
| --- | --- | --- | --- |
| 7.1 | Quarto site: methods, 2020 counterfactual, savings ranges, limitations (the triangulation stated up front) | 4 | Every number links to its query or table |
| 7.2 | Two-page brief on Int 0998-2026: central + range, the scenario table with the dimming option, tons at the LL97 factor, the bird case, one paragraph of limitations. Re-check Legistar first | 2.5 | A non-technical friend can say back the headline in one sentence |
| 7.3 | Reproducibility: fresh clone → `make all` with a local Postgres | 1.5 | Works from scratch in a fresh container |
| 7.4 | Resume bullet + LinkedIn post (draft; you send) | 1 | — |

**Total ≈ 92 h**, of which about 8 h (5.3–5.5) can be cut without hurting the core.

---

## 6. Testing and quality checks

- **Data:** dbt `unique`/`not_null` on every key; a custom `no_hour_gaps(zone)` test; row-count tests against source manifests; a freshness test (latest `ts_utc` < 36 h old) that fails the nightly job.
- **Regression anchors:** tests that 2025 Zone J = 50,104 GWh ± 0.1%, the 2025 1–5 a.m. mean = 4,768 MW ± 1, and PLUTO 26v2 `comarea` = 1.83B sq ft. These catch silent changes in ingest or time zones.
- **Time:** unit tests for the spring and fall DST nights (3 and 5 hours in the 1–5 a.m. window), and for the repeated November hour in both `palIntegrated` (has a flag) and `damlbmp` (no flag).
- **Spatial:** commercial shares between 0 and 1; the eyeball checks.
- **Models:** fixed seeds; the backtest and counterfactual are scripts with committed outputs.
- **Savings:** property tests: savings rise with uptake, "dim" saves less than "off", and nothing exceeds in-scope overnight lighting.
- **CI:** a `postgis/postgis` service container, `dbt build` on a 1-month fixture, pytest on every push.

---

## 7. Remaining decisions

1. **Hearing timing:** no hearing is scheduled for Int 0998-2026 as of Oct 20, 2026. If one is set before mid-December, ship the brief from the Core tier, before the dashboard.
2. **Outreach:** contact Council Member Epstein's office, the NYC Bird Alliance and the Lights Out coalition by about week 5. Field audits of after-hours lighting are the best available check on the input that drives the whole estimate.
