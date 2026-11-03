# Project 2 Scope: How much energy does NYC waste overnight?

Nov 3, 2026 · due Nov 11 · scoped-down version of *NYC After-Hours Lighting Project Plan.md* for "Project 2: A Data Exploration Agent"

The 10-week plan stays as the long-term version. This document cuts it down to what one agent can answer well in eight days, using only data that's been checked and is small enough to commit to the repo. **Updated Nov 3:** a map is now part of the chat UI, Black Marble satellite data is in scope, and §"Time coverage" says what the agent can and can't compare across years.

## The question, made answerable

**"How much energy is wasted in NYC overnight?"** is vague in exactly the way the Deep Research path wants an agent to catch. "Wasted" can mean three things, and the agent should ask which one the user means before answering (or answer all three, clearly separated):

| Level | What it measures | Data | Is it waste? |
| --- | --- | --- | --- |
| **1. Overnight load** | All NYC electricity between 1 and 5 a.m. | NYISO Zone J hourly load | No. It includes homes, subways, hospitals and data centers. It's the ceiling |
| **2. Commercial lighting at night** | Interior (and exterior) lighting in commercial buildings between 1 and 5 a.m. | ComStock baseline × NYC building stock | Candidate waste. Some of it is egress, security and night shifts |
| **3. Avoidable lighting** | Level 2 minus what sensors or a lights-out rule would leave on | ComStock **Lighting Controls** scenario (upgrade 44), plus a lights-out bound | **The answer to "wasted"**: energy that could be saved, given as a range |

The agent's headline answer is level 3 as a range in MWh/yr, dollars and tons of CO2, with levels 1 and 2 shown as context so the number can't be misread. Black Marble adds an independent, *measured* view of where the city stays lit at night and how that has changed.

## Path: Deep Research, with researchers that run SQL

| Agent | Job | Tools |
| --- | --- | --- |
| **Lead** | Asks for clarification on vague questions ("waste of what? which buildings? which year?"). Plans, fans out to researchers in parallel, checks for gaps (e.g., floor area in ComStock doesn't match PLUTO) and sends researchers back, up to a turn cap (~3 rounds) | None. It just delegates |
| **Grid Researcher** | Overnight floor, night-to-day ratio, weekday vs weekend nights, the 2020 shutdown, trends 2016→now, overnight prices | `run_sql` on the `nyiso_*` tables; `fetch_latest_nyiso(date)` for yesterday's live file |
| **Lighting Researcher** | Overnight lighting by building type and borough, baseline vs lighting controls | `run_sql` on the `comstock_*` tables |
| **Buildings Researcher** | Floor area by type and borough, reported electricity per sq ft, LL88-covered and bill-covered buildings; cross-checks ComStock | `run_sql` on the `pluto` and `ll84` tables |
| **Night Lights Researcher** | Measured nighttime brightness by hexagon and borough; changes 2013→2025, spring 2019 vs 2020, the latest 12 months; bright *and* commercial areas | `run_sql` on the `nightlights_*` and `hex_*` tables |
| **Writer** | Turns the findings into an answer with a range and a short "what this data can't tell you" section; cites every number to a table and query; **calls `show_on_map`** to draw the evidence | `show_on_map` |

All researchers query one DuckDB file that ships in the repo. Queries take milliseconds, so LLM calls are what use up the graders' 5-minute limit.

**Fallback:** if the multi-agent part runs out of time, the same DuckDB file plus a written schema-and-definitions document (the offline build step) is a valid Text2SQL submission.

## The map, inside the chat UI

**Layout:** chat on the left, a dark map of NYC on the right (MapLibre GL JS + the CARTO Dark Matter basemap, free with attribution). On a phone, the map stacks above the chat.

**How the agent drives it:** the Writer calls `show_on_map(layer, period, filter, title)`. Because that's a tool call, it already shows up in the Project 1 `/chat` response's `tool_calls`. The front end watches for it and redraws the map, so the response format doesn't change.

**One shared hexagon grid:** the offline build assigns everything to H3 resolution-8 hexagons (about 0.7 km² each, roughly 1,500 across NYC). Then:
- `hex_grid.geojson` (geometry only, ~1 MB) ships to the browser.
- The `hex_*` tables in DuckDB hold the values. The map and the agent's SQL read the same numbers, so they can't disagree.

| Layer | What glows | Source | Label shown on the map |
| --- | --- | --- | --- |
| `est_overnight_lighting` | Modeled 1–5 a.m. commercial lighting (MW per hexagon) | Each PLUTO lot's floor area × ComStock overnight W/sq ft for its type and borough, summed per hexagon | "Estimated from building type and size, not measured" |
| `avoidable_lighting` | Level 3 per hexagon (controls or lights-out) | Same, using upgrade 44 / the lights-out bound | "Modeled savings" |
| `night_lights` | Measured radiance from space, for a chosen period | Black Marble, mean of pixels in each hexagon | "Measured by satellite at ~1:30 a.m. solar time; includes streets and signs" |
| `night_lights_change` | Brighter or dimmer between two periods (2019→2025, spring 2019→spring 2020) | Black Marble difference | "Measured change" |
| `commercial_share` | Share of floor area that's commercial | PLUTO | — |

**Rules:** show hexagons, never individual buildings; clicking a hexagon shows its building mix by type, total floor area and layer values. A period slider appears only for Black Marble layers, since those are the only layers that change over time.

## Datasets

All the sources below were checked on Oct 20 and Nov 3, 2026. Before building anything, put your entry on the course sign-up sheet.

| # | Dataset | What it gives the agent | Raw → shipped | Access |
| --- | --- | --- | --- | --- |
| 1 | **NYISO integrated hourly load, Zone J** (`palIntegrated`), **2016 → latest** | Level 1; the 2020 shutdown; trends | ~130 small monthly zips → ~95k rows, ~2 MB | ✅ Yesterday's file (Nov 2, 2026) already posted; archive goes back past 2016 |
| 2 | **NYISO day-ahead zone prices, N.Y.C.** (`damlbmp_zone`), 2016 → latest | Dollar value of a saved MWh | Same, ~2 MB | ✅ 2025: $49.37/MWh between 1 and 5 a.m. |
| 3 | **NREL ComStock 2025 release 3, county aggregates**: 5 NYC counties × 15 building types, upgrades 0 (baseline) and 44 (lighting controls) | Levels 2 and 3 | 150 CSVs, ~2.7 GB → hour × day type × month × county × type, ~5 MB | ✅ Public OEDI S3 bucket; columns confirmed |
| 4 | **MapPLUTO 26v2** (attributes + lat/lon) | Floor area by class and use; lot → hexagon assignment | ~860k lots → ~40 MB Parquet | ✅ Open Data API (`64uk-42ks`) |
| 5 | **LL84 benchmarking, annual**, 2022–2024 | The **only metered, building-level energy** in the project. Uses: (a) check ComStock's modeled kWh/sq ft against reported medians by type; (b) a second estimate that scales ComStock's overnight lighting share to metered totals; (c) size the energy in types ComStock doesn't model (labs, data centers, museums, colleges). 2024: 30,081 properties, 32.7 TWh, about two-thirds of Zone J | ~103k rows → a few MB | ✅ Open Data API (`5zyy-y8am`) |
| 6 | **NASA Black Marble**: **VNP46A4 annual** 2013–2025, plus **VNP46A3 monthly** for Mar–Jun 2019, Mar–Jun 2020 and the latest 12 months (Oct 2025–Sep 2026) | Measured nighttime light over time; map layers | NYC sits in one tile (h10v04): ~13 × 180 MB + ~20 × 100 MB ≈ **4.3 GB** → clipped to NYC and summarized per hexagon, ~5 MB | ✅ Sep 2026 tile is live (released ~3–5 weeks after the month ends); needs a free NASA Earthdata token |
| 7 | **Reference constants** (a small cited table) | CO2 per kWh (LL97: 0.000288962 t), NY household use (566 kWh/month), France estimate (1–2 TWh/yr), the bill (Int 0998-2026) | Hand-built CSV with a source URL per row | ✅ |

**Total shipped: about 60 MB** (DuckDB + hexagon GeoJSON), committed to the repo. Six data sources cover the "one per team member" rule for teams of up to six. Raw downloads (~7 GB) stay out of git, in `data/raw/`.

Left out of this scope: weather, the forecasting model, LL84 monthly data, and the Postgres/dbt/Tableau stack.

## Time coverage: what the agent can compare across time

| Dataset | Most recent data | Comparing across time |
| --- | --- | --- |
| NYISO load and prices | Yesterday (via the live tool); the shipped snapshot runs to the deploy date | ✅ Strong: hourly since 2016, including the 2020 shutdown |
| Black Marble | ~1 month behind (Sep 2026 as of Nov 3) | ✅ Yearly 2013–2025; monthly for the chosen windows. **The only measured view of lighting over time** |
| LL84 | 2024 data; 2025 not yet published | ⚠️ 2022–2024 only. Can't yet show before vs after the Jan 2025 LL88 deadline |
| PLUTO | 2026 release | ⚠️ One snapshot; the building stock barely changes year to year |
| ComStock | One modeled typical year (2018 weather) of today's stock | ❌ No year-to-year comparison. Compares hours, day types, seasons and scenarios only |

**What the agent should say:** the level 1 overnight floor can be compared across years using measured data. Levels 2 and 3 apply a fixed, modeled lighting share, so "was more energy wasted in 2025 than 2019?" can't be answered from ComStock. For change over time, the agent turns to Black Marble's measured brightness and says that brightness isn't energy.

## Feasibility

**Overall: feasible in 8 days, with the map taking ~1.5 days and Black Marble ~1 day.** Every source has been accessed. The risks are interpretation and time, not data.

| Area | Status | Notes |
| --- | --- | --- |
| Data access | ✅ | All sources reached. Only Black Marble needs an account (free NASA Earthdata) |
| Large one-time downloads | ⚠️ ~7 GB total | ComStock (~2.7 GB) and Black Marble (~4.3 GB). Run them in the background on day 1; commit only the summaries |
| Black Marble processing | ⚠️ New territory | HDF5 tiles on a sinusoidal grid: use blackmarblepy to clip to NYC, check that the pixels line up with a basemap (Times Square bright, Central Park dark), and keep only good-quality pixels (`qa_flag` 0) |
| Matching building types | ⚠️ Main judgment call | Use ComStock's own `floor_area_represented` for the citywide estimate. Use PLUTO only to spread it across the map and to cross-check. ComStock has no museums, data centers or labs; the agent says so |
| Defining "waste" | ✅ | The three levels, plus the Lead's clarifying question |
| It's a simulation | ⚠️ | The Writer always labels ComStock numbers "modeled" and Black Marble "measured brightness, not energy" |
| Map front end | ✅ Standard | Static GeoJSON + values from `/chat` tool calls; no tile server needed |
| Deployment | ✅ | Cloud Run from GitHub; request timeout 300 s; DuckDB + GeoJSON in the container |

### An early result worth knowing now

From a small read of the Manhattan large-office file (2018 weather year):

- Manhattan large offices (525M sq ft represented) burn **~102 MW of interior lighting between 1 and 5 a.m.** on a January weekday, **half** of their 1–5 p.m. lighting (205 MW).
- In the **Lighting Controls** scenario, overnight lighting falls only ~9% (to 92 MW), while afternoon lighting falls ~33%.

That's one type, one borough and one day: a lead, not a finding. If it holds, (1) level 2 will come out above the earlier first estimate of 36–180 MW, and (2) sensors mostly cut daytime waste, so level 3 needs a lights-out bound too. That contrast could be the most interesting thing the agent says.

## What the agent must say it can't answer

- **Which specific building leaves its lights on.** No dataset measures one building at night; Black Marble pixels are ~500 m wide and include streets and signs.
- **Measured lighting energy.** Lighting energy is modeled; grid load is measured but not broken down by end use; satellite brightness is measured but isn't energy.
- **Year-to-year change in waste** from ComStock (see Time coverage).
- **Signs, billboards and facade floodlights** separately from other light, plus residential lighting and streetlights.
- **Tonight.** The newest grid data is yesterday's; the newest satellite data is about a month old.

## README sample queries (draft; fill in answers from the built DuckDB)

| Query | Expected answer | Source |
| --- | --- | --- |
| What was NYC's average electricity load between 1 and 5 a.m. in 2025, and how does that compare with 2019? | 2025: 4,768 MW; 2019: TBD | `nyiso_load` |
| How much electricity do Manhattan's large offices use for interior lighting overnight on a winter weekday, and how much would lighting controls save? | ~102 MW → ~92 MW (confirm over a full winter month) | `comstock_lighting` |
| Did commercial areas get darker at night in spring 2020 than in spring 2019? Show me where. | TBD; the map shows `night_lights_change` | `nightlights_monthly`, `hex_values` |
| *Can't-answer check:* Which building in Midtown leaves its lights on all night? | Declines; explains why and shows the hexagon-level map instead | — |

For each, the README also needs the same model's answer with no tools and no system prompt.

## Build order

1. **Setup:** sign-up sheet, NASA Earthdata token, a GCP project with a hello-world Cloud Run deploy from GitHub, and the course's Deep Research starter unpacked.
2. **Data build** (`build/`): start the ComStock and Black Marble downloads in the background; pull NYISO, PLUTO and LL84; aggregate everything into `data/nyc_overnight.duckdb` + `data/hex_grid.geojson` + `schema.md`. Spot-check against the verified numbers.
3. **Researchers:** `run_sql` + `fetch_latest_nyiso`, one system prompt per researcher, findings returned as `{claim, number, query, table}`.
4. **Lead + Writer:** clarification, parallel fan-out, gap check, turn cap, citations, `show_on_map`. Keep the `/chat` shape, with an `agent` field on every sub-agent tool call.
5. **Front end:** chat + map, what it explores, who it's for, how to use it, example questions.
6. **Deploy and README:** check answers come back in under 5 minutes; the sample queries with tools vs no tools; `submission.json` (`"path": "research"`).

## Saved for later

- **Prevention research:** how the waste could be prevented: sensors vs lights-out rules vs dimming, LL88 compliance, France's experience, costs and payback. A web-search "Policy Researcher" can slot into the same Lead/Researcher/Writer setup.
