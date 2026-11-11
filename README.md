# NYC After Dark: a more cost effective City That Never Sleeps

This deep-research agent answers questions regarding NYC's overnight electricity usage, specifically in regards to how much of it is avoidable lighting in empty commercial buildings. Key information, such as energy usage by area, changes in energy usage over time, the brightness of certain areas via satellite view, and the commercial share of said energy usage, is displayed on a map of New York City.

The aim of this project is to inform users of the wasteful energy usage in New York City. While commercial buildings such as office spaces and stores must be well lit during the day, often these spaces go entirely unused at night; despite this, in many cases, the lights stay on. As such, this agent is directed towards two audiences: **policymakers**, in order to encourage policies such as motion activated lighting or other lower energy alternatives, and **normal New Yorkers**, in hopes that the last one to leave a space, whether it be an office, a classroom, or apartment might choose to turn off the lights where they otherwise might not.

## Data

### NYISO
- Measures hourly electricity usage for NYC and day-ahead wholesale prices for NYC (covers January 2016 to now)
- Public CSV files (one per month):
  - http://mis.nyiso.com/public/csv/palIntegrated/YYYYMM01palIntegrated_csv.zip (load)
  - http://mis.nyiso.com/public/csv/damlbmp/YYYYMM01damlbmp_zone_csv.zip (price)
- build/fetch_nyiso.py [first YYYY-MM] [last YYYY-MM] (24 MB)
- Tables: nyiso_load, nyiso_price, calendar; views nyiso_hourly, overnight_daily

### NREL ComStock (U.S. Dept of Energy)
- Modeled hourly lighting and electricity for NYC commercial buildings by borough and building type, both in their current state and with a set of upgrades that reduce energy usage (lighting controls, daylight sensors, occupancy sensors)
- OEDI public S3 bucket: https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/comstock_amy2018_release_3/timeseries_aggregates/by_county/ (NYC counties G3600050, G3600470, G3600610, G3600810, G3600850)
- build/fetch_comstock.py (150 CSVs, 2.6 GB)
- Tables: comstock_hourly, comstock_annual, type_crosswalk

### NYC Open Data
- MapPLUTO, includes data for every tax lot (floor area by use, building class, location) and Local Law 84 energy benchmarking (measures of annual electricity for large buildings from 2022-2024)
- Socrata API:
  - https://data.cityofnewyork.us/resource/64uk-42ks.csv (MapPLUTO)
  - https://data.cityofnewyork.us/resource/5zyy-y8am.csv (LL84)
- build/fetch_open_data.py (198 MB; optional NYC_APP_TOKEN raises the rate limit)
- Tables: pluto, ll84, hex_cells (lots presented as hexagons for map)

### NASA Black Marble (VIIRS satellite)
- Measured nighttime light from space over NYC from 2013-2025 (VNP46A4), monthly from March to June 2019, March to June 2020, and October 2025 to September 2026 (VNP46A3)
  - While data is available every month, downloading all data would be roughly 18GB of data; the chosen months represent yearly trends for 2013 to 2025 and specific data for March to June 2019 and 2020 to compare the impact of the Covid-19 pandemic, when many commercial buildings were vacant. The most recent 12 months of available data was also included
- NASA LAADS DAAC API, tile h10v04:
  - https://ladsweb.modaps.eosdis.nasa.gov/api/v2/content
- build/fetch_blackmarble.py (33 HDF5 files, 4.1 GB; needs a free NASA Earthdata token as BLACKMARBLE_TOKEN in .env)
- Tables: nightlights, hex_nightlights

data/nyc_overnight.duckdb is in the repo (51 MB); open it with duckdb data/nyc_overnight.duckdb or duckdb.connect(..., read_only=True) in Python
data/schema.md lists every table and column used

## Sample Queries

For each query, a Lead agent might ask to clarify a vague question. Once a clear question is established, the Lead splits the question into research tasks and sends it to Researchers, which each answer an aspect of the question using the relevant data source. There are a total of 4 Researcher types (grid, lighting, buildings, nightlights) and up to 8 researchers per question, which get sent in varying quantities depending on the complexity of the question and time spent to formulate a response. Research concludes either when all agents have completed their tasks or they elapse 3 minutes, at which point the existing researh is sent to a Writer agent. The Writer turns the findings into a coherent response, picks the relevant information to display on the map, and provides sources based on the queries run by the Researchers. Each response also explains gaps in data or knowledge

### What was the highest usage night in 2026 so far?

**The agent** provides a response of July 3, 2026, with 1-5 AM electricity load averaging 7,994 MW (or 32,000 MWh) over 4 hours. The highest usage hour was 1 AM, with usage at 8,387 MW.

**Data used by the model:**
- Table: overnight_daily (a view; floor_mw is the 1–5 a.m. average load), built from nyiso_load
- Upstream file: NYISO hourly actual load for Zone J (New York City), http://mis.nyiso.com/public/csv/palIntegrated/YYYYMM01palIntegrated_csv.zip (July 2026 is 20260701palIntegrated_csv.zip)

**Without the agent:** the knowledge stops at January 2025 and no real-time data is available; additionally, without a grid or city specified, the model is unable to give a specific response

### How much would installing motion sensor lighting reduce NYC's overnight energy costs?

**The agent** estimates commercial building power used would fall from 225 MW to 197 MW per night (1-5 AM) with the Lighting Controls retrofit proposed by ComStock upgrade 44; this is an estimated reduction of 12.3%, saving 40,600 MWh a year. At wholesale price, the reduced cost is $2.07M (2025 prices) to $2.27M (2026 prices) per year. While retail rates are not available in the data, the savings would likely be an uncomputable larger amount.

**Data used by the model:**
- Savings (modeled): comstock_annual, upgrade 0 (buildings as they are) vs. upgrade 44 (Lighting Controls), summed over NYC's five counties. Upstream: NREL ComStock 2025 release 3 (AMY2018) county aggregates on the OEDI public S3 bucket, https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/comstock_amy2018_release_3/timeseries_aggregates/by_county/.
- Price (measured): overnight_daily.floor_price_usd_mwh (1–5 a.m. average), built from nyiso_price. Upstream: NYISO day-ahead zonal prices, zone N.Y.C., http://mis.nyiso.com/public/csv/damlbmp/YYYYMM01damlbmp_zone_csv.zip.
- Households and CO2 (cited constants): constants table: ny_household_kwh_per_month = 566 kWh (EIA Table 5A) gives about 6,170 households; co2_ll97_t_per_kwh = 0.000289 t/kWh (NYC Local Law 97) gives about 12,100 t CO2e a year. Each row carries its source URL.

**Without the agent:** An estimate of $110-180M a year from invented inputs, covering homes and streetlights as well, but gives no sources. The model relies on assumed retail prices, residential common areas, and assumed waste shares (but cannot support these estimates with evidence). It also lists implementation challenges.

### How has NYC overnight energy costs changed since 2020? When were they at their highest?

**The agent** provides a response of $109M in 2020, $224M in 2021, $465M in 2022 (the highest cost year), $170M in 2023, $204M in 2024, $355M in 2025, and $347M in 2026 (so far). Average overnight costs peaked at $65.8/MWh (yearly average cost) in 2022, and the most expensive month was January 2026 ($151/MWh). The most expensive nights were January 27-28, 2026 ($600/MWh). These costs differ from those solely pertaining to light costs, and includes additional energy costs such as heating, refrigeration, appliances, etc.

**Data used by the model:**
- Table: nyiso_hourly (a view joining nyiso_load, nyiso_price and calendar); cost = load_mw × lbmp_usd_mwh summed over the 1–5 a.m. hours (hour_local 1–4). Peak month and nights come from the same view grouped by month and by date (or overnight_daily).
- Upstream files: NYISO palIntegrated (hourly load, Zone J) and damlbmp zone files (day-ahead price, zone N.Y.C.), the same monthly zips as Questions 1 and 2.

**Without the agent:** Accurately provides response of 2022 with explanations of 2020 being cheap, 2022 experiencing a gas price spike (followed by drop in 2023). Price ranges are estimated and information ends at 2024. January 2026, the peak month, is not observed.
