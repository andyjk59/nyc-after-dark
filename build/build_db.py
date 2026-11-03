"""Build data/nyc_overnight.duckdb, data/hex_grid.geojson and data/schema.md from data/raw/.

    uv run --group build python -I build/build_db.py

Reads only what the fetch_*.py scripts downloaded. Every table the agent can query is described in
TABLES below; the same descriptions are written to schema.md, which the researchers' prompts use.
"""

import io
import json
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import duckdb
import h3
import h5py
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "nyc_overnight.duckdb"
HEX_RES = 8  # ~0.74 km2 per hexagon
NYC_BBOX = {"lat": (40.47, 40.93), "lon": (-74.28, -73.68)}

BOROUGHS = pd.DataFrame({
    "borough": ["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"],
    "pluto_code": ["MN", "BX", "BK", "QN", "SI"],
    "county_fips": ["36061", "36005", "36047", "36081", "36085"],
    "comstock_county": ["G3600610", "G3600050", "G3600470", "G3600810", "G3600850"],
})

# PLUTO floor-area category -> the ComStock building types whose overnight lighting intensity it gets.
# Used only to spread ComStock's borough totals across the map; citywide totals come from ComStock.
CROSSWALK = [
    ("office", "PLUTO officearea, any building", ["SmallOffice", "MediumOffice", "LargeOffice"]),
    ("retail", "PLUTO retailarea, any building", ["RetailStandalone", "RetailStripmall"]),
    ("hotel", "bldgarea of lots with bldgclass H* (minus office/retail area)", ["SmallHotel", "LargeHotel"]),
    ("hospital", "bldgarea of lots with bldgclass I1 (minus office/retail area)", ["Hospital"]),
    ("outpatient", "bldgarea of lots with bldgclass I2-I9 (minus office/retail area)", ["Outpatient"]),
    ("school", "bldgarea of lots with bldgclass W1-W3, W8 (minus office/retail area)", ["PrimarySchool", "SecondarySchool"]),
    ("warehouse", "PLUTO strgearea, any building", ["Warehouse"]),
]

CONSTANTS = [
    ("co2_ll97_t_per_kwh", 0.000288962, "tCO2e per kWh", "NYC Local Law 97 grid electricity coefficient, 2024-2029",
     "https://www.nyc.gov/assets/buildings/pdf/presentations/AEE_March-2024.pdf"),
    ("co2_ll97_2030_t_per_kwh", 0.000145, "tCO2e per kWh", "LL97 coefficient proposed for 2030-2034",
     "https://www.ny-engineers.com/blog/new-local-law-97-rules-for-nyc-building-owners-what-has-changed"),
    ("ny_household_kwh_per_month", 566, "kWh per month", "Average NY residential customer use, 2023 (EIA Table 5A)",
     "https://www.eia.gov/electricity/sales_revenue_price/pdf/table_5A.pdf"),
    ("us_commercial_lighting_share", 0.17, "fraction", "Lighting share of US commercial electricity, 2018 (CBECS)",
     "https://www.eia.gov/tools/faqs/faq.php?id=99&t=3"),
    ("cbecs_large_bldg_occupancy_sensor_share", 0.55, "fraction", "Share of large (50k+ sq ft) lit buildings with occupancy sensors, CBECS 2012",
     "https://www.eia.gov/todayinEnergy/detail.php?id=31272"),
    ("france_lights_out_savings_low_gwh", 1000, "GWh per year", "French ministry estimate (2018) for the non-residential lights-out rule; not measured",
     "https://www.connexionfrance.com/article/Practical/Environment/Lights-out-for-French-businesses-every-night"),
    ("france_lights_out_savings_high_gwh", 2000, "GWh per year", "ADEME ex-ante estimate (2013) for the same rule; not measured",
     "https://www.assemblee-nationale.fr/dyn/14/questions/QANR5L14QE84260.pdf"),
    ("doe_office_lighting_night_fraction", 0.05, "fraction of installed lighting", "DOE reference office lighting schedule, overnight hours",
     "https://www1.eere.energy.gov/buildings/appliance_standards/pdfs/ashrae_final_rule_tsd_app04a_characteristics_2012_05_01.pdf"),
    ("ll88_threshold_sqft", 50000, "sq ft", "Local Law 88 covered buildings (or 100k+ sq ft across buildings on one lot); lighting upgrades due Jan 1, 2025",
     "https://www.nyc.gov/assets/buildings/pdf/ll88of2009.pdf"),
    ("bill_int_0998_2026_small_retail_exemption_sqft", 4000, "sq ft", "Int 0998-2026 exempts non-chain stores under this size; covers Building Code groups B, E, M",
     "https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=8169984&GUID=B394ED89-C70F-43FF-8922-D468D4758EF0"),
]

TABLES = {
    "nyiso_load": ("NYISO integrated real-time actual load for Zone J (New York City), hourly. Measured. "
                   "Source: mis.nyiso.com/public/csv/palIntegrated.",
                   {"ts_utc": "hour start, UTC", "ts_local": "hour start, New York local time",
                    "hour_local": "0-23; overnight = 1-4 (i.e. 1-5 a.m.)", "load_mw": "average load in the hour, MW (= MWh)"}),
    "nyiso_price": ("NYISO day-ahead locational marginal price for zone N.Y.C., hourly. Source: mis.nyiso.com/public/csv/damlbmp.",
                    {"lbmp_usd_mwh": "day-ahead price, $/MWh"}),
    "calendar": ("One row per local date 2016-today with day-type flags.",
                 {"day_type": "weekday | weekend | holiday (US federal)",
                  "covid_period": "pre (before 2020-03-22) | pause (NY PAUSE, 2020-03-22 to 2020-06-07) | reopening (2020-06-08 to 2020-07-19) | post"}),
    "nyiso_hourly": ("VIEW: nyiso_load joined to nyiso_price and calendar. Start here for grid questions.", {}),
    "overnight_daily": ("VIEW: per local date, mean load 1-5 a.m. (floor_mw) and 1-5 p.m. (day_mw), their ratio, and the day type.", {}),
    "comstock_hourly": ("NREL ComStock 2025 release 3, county aggregates, MODELED (not metered). Average hourly electricity "
                        "by NYC borough, ComStock building type, scenario, month, day type and hour, for a typical year "
                        "(2018 weather) of today's building stock. Cannot be compared across years.",
                        {"upgrade": "0 = baseline (today's stock); 44 = Lighting Controls retrofit (occupancy sensors etc.)",
                         "hour": "hour starting, local standard time (EST all year), 0-23",
                         "day_type": "weekday | weekend | holiday (2018 calendar)",
                         "interior_lighting_mw": "average interior lighting load in that hour, MW",
                         "exterior_lighting_mw": "average exterior lighting load, MW",
                         "total_electricity_mw": "average total building electricity, MW",
                         "floor_area_sqft": "floor area the aggregate represents",
                         "models_used": "number of simulated buildings behind the aggregate"}),
    "comstock_annual": ("Annual totals from comstock_hourly by borough, building type and scenario: MWh for interior lighting "
                        "(all hours and 1-5 a.m. only), exterior lighting and total electricity. Modeled.", {}),
    "type_crosswalk": ("How PLUTO floor area is matched to ComStock building types to spread modeled lighting across the map. "
                       "Restaurants, groceries, factories, museums, labs, data centers and colleges have no match.", {}),
    "pluto": ("MapPLUTO (release in the version column): one row per tax lot with floor area by use, building class and "
              "location. hex is the H3 resolution-8 hexagon containing the lot.", {}),
    "ll84": ("LL84 benchmarking, annual (report years 2022-2024): METERED electricity reported by buildings of 25,000+ sq ft "
             "(private) or 10,000+ sq ft (city-owned). Self-reported; outliers exist. Annual only, no hourly data.",
             {"electricity_kwh": "grid electricity purchased in the year, kWh",
              "elec_kwh_per_sqft": "electricity_kwh / gfa_sqft",
              "bbl": "first BBL listed (some properties list several)"}),
    "nightlights": ("NASA Black Marble (VIIRS) nighttime radiance per ~500 m pixel over NYC. MEASURED brightness at "
                    "~1:30 a.m. solar time; includes streets, signs and homes, not just buildings. Not energy.",
                    {"period": "'2013'..'2025' (annual, VNP46A4) or 'YYYY-MM' (monthly, VNP46A3)",
                     "radiance": "near-nadir snow-free composite, nW/cm2/sr",
                     "quality": "0 = good (more than 3 observations), 1 = poor, 2 = gap-filled"}),
    "hex_cells": ("One row per H3 resolution-8 hexagon in NYC: building floor area by use, commercial share, "
                  "and ESTIMATED overnight lighting (ComStock borough intensities x PLUTO floor area).",
                  {"est_lighting_base_mw": "estimated 1-5 a.m. interior lighting, baseline, MW (modeled allocation)",
                   "est_lighting_controls_mw": "same with the Lighting Controls retrofit",
                   "commercial_share": "comarea / bldgarea"}),
    "hex_nightlights": ("VIEW: mean good-quality radiance per hexagon and period, from nightlights.", {}),
    "constants": ("Cited reference numbers (CO2 factors, household use, benchmarks). Cite the url column.", {}),
}


# --- NYISO ---

def read_zips(folder: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                frames.append(pd.read_csv(io.BytesIO(z.read(name))))
    return pd.concat(frames, ignore_index=True)


def nyiso_tables(con) -> None:
    load = read_zips(RAW / "nyiso" / "palIntegrated")
    load = load[load["Name"] == "N.Y.C."].copy()
    local = pd.to_datetime(load["Time Stamp"], format="%m/%d/%Y %H:%M:%S")
    load["ts_utc"] = local + pd.to_timedelta(np.where(load["Time Zone"] == "EDT", 4, 5), unit="h")
    load["ts_local"] = local
    load = (load.rename(columns={"Integrated Load": "load_mw", "Time Zone": "tz"})
                .drop_duplicates("ts_utc").sort_values("ts_utc"))
    load = load[["ts_utc", "ts_local", "tz", "load_mw"]]
    con.execute("""CREATE TABLE nyiso_load AS
        SELECT ts_utc::TIMESTAMP AS ts_utc, ts_local::TIMESTAMP AS ts_local, CAST(ts_local AS DATE) AS date_local,
               hour(ts_local) AS hour_local, tz, load_mw::DOUBLE AS load_mw FROM load""")

    price = read_zips(RAW / "nyiso" / "damlbmp_zone")
    price = price[price["Name"] == "N.Y.C."].copy()
    local = pd.to_datetime(price["Time Stamp"], format="%m/%d/%Y %H:%M")
    # No time-zone column: on the fall-back night the repeated 1 a.m. hour comes first as EDT, then EST.
    first_of_pair = ~local.duplicated(keep="first") & local.duplicated(keep=False)
    aware = local.dt.tz_localize("America/New_York", ambiguous=first_of_pair.to_numpy(), nonexistent="shift_forward")
    price["ts_utc"] = aware.dt.tz_convert("UTC").dt.tz_localize(None)
    price["ts_local"] = local
    price = price.rename(columns={"LBMP ($/MWHr)": "lbmp_usd_mwh",
                                  "Marginal Cost Congestion ($/MWHr)": "congestion_usd_mwh",
                                  "Marginal Cost Losses ($/MWHr)": "losses_usd_mwh"})
    price = price.drop_duplicates("ts_utc")[["ts_utc", "ts_local", "lbmp_usd_mwh", "congestion_usd_mwh", "losses_usd_mwh"]]
    con.execute("CREATE TABLE nyiso_price AS SELECT * FROM price ORDER BY ts_utc")

    days = pd.DataFrame({"date_local": pd.date_range("2016-01-01", date.today())})
    holidays = set(USFederalHolidayCalendar().holidays("2016-01-01", date.today()))
    days["day_type"] = np.where(days["date_local"].isin(holidays), "holiday",
                                np.where(days["date_local"].dt.dayofweek >= 5, "weekend", "weekday"))
    d = days["date_local"]
    days["covid_period"] = np.select(
        [d < "2020-03-22", d < "2020-06-08", d < "2020-07-20"], ["pre", "pause", "reopening"], "post")
    days["year"], days["month"], days["dow"] = d.dt.year, d.dt.month, d.dt.dayofweek
    con.execute("CREATE TABLE calendar AS SELECT CAST(date_local AS DATE) AS date_local, day_type, covid_period, year, month, dow FROM days")

    con.execute("""CREATE VIEW nyiso_hourly AS
        SELECT l.*, p.lbmp_usd_mwh, c.day_type, c.covid_period, c.year, c.month, c.dow
        FROM nyiso_load l LEFT JOIN nyiso_price p USING (ts_utc) LEFT JOIN calendar c USING (date_local)""")
    con.execute("""CREATE VIEW overnight_daily AS
        SELECT date_local, any_value(day_type) AS day_type, any_value(covid_period) AS covid_period,
               any_value(year) AS year, any_value(month) AS month,
               avg(load_mw) FILTER (WHERE hour_local BETWEEN 1 AND 4) AS floor_mw,
               avg(load_mw) FILTER (WHERE hour_local BETWEEN 13 AND 16) AS day_mw,
               floor_mw / day_mw AS night_day_ratio,
               avg(lbmp_usd_mwh) FILTER (WHERE hour_local BETWEEN 1 AND 4) AS floor_price_usd_mwh
        FROM nyiso_hourly GROUP BY date_local""")


# --- ComStock ---

def comstock_tables(con) -> None:
    con.register("boroughs", BOROUGHS)
    holidays_2018 = [d.strftime("%Y-%m-%d") for d in USFederalHolidayCalendar().holidays("2018-01-01", "2018-12-31")]
    con.execute(f"""CREATE TABLE comstock_hourly AS
        WITH raw AS (
            SELECT upgrade::INT AS upgrade, "in.county" AS county, "in.comstock_building_type" AS building_type,
                   timestamp::TIMESTAMP - INTERVAL 15 MINUTE AS t,
                   models_used, floor_area_represented,
                   "out.electricity.interior_lighting.energy_consumption.kwh" AS il,
                   "out.electricity.exterior_lighting.energy_consumption.kwh" AS el,
                   "out.electricity.total.energy_consumption.kwh" AS te
            FROM read_csv('{RAW}/comstock/upgrade=*/county=*/*.csv', union_by_name=true, header=true)
        ), hourly AS (
            SELECT upgrade, county, building_type, date_trunc('hour', t) AS h,
                   sum(il) AS il, sum(el) AS el, sum(te) AS te,
                   any_value(models_used) AS models_used, any_value(floor_area_represented) AS fa
            FROM raw GROUP BY ALL
        )
        SELECT b.borough, b.county_fips, h.building_type, h.upgrade, month(h.h) AS month,
               CASE WHEN strftime(h.h, '%Y-%m-%d') IN {tuple(holidays_2018)} THEN 'holiday'
                    WHEN dayofweek(h.h) IN (0, 6) THEN 'weekend' ELSE 'weekday' END AS day_type,
               hour(h.h) AS hour, count(*) AS n_days,
               avg(il) / 1000 AS interior_lighting_mw, avg(el) / 1000 AS exterior_lighting_mw,
               avg(te) / 1000 AS total_electricity_mw,
               any_value(fa) AS floor_area_sqft, any_value(models_used) AS models_used
        FROM hourly h JOIN boroughs b ON b.comstock_county = h.county
        GROUP BY ALL ORDER BY ALL""")
    con.execute("""CREATE TABLE comstock_annual AS
        SELECT borough, county_fips, building_type, upgrade,
               sum(interior_lighting_mw * n_days) AS interior_lighting_mwh,
               sum(interior_lighting_mw * n_days) FILTER (WHERE hour BETWEEN 1 AND 4) AS interior_lighting_1_5am_mwh,
               sum(exterior_lighting_mw * n_days) AS exterior_lighting_mwh,
               sum(exterior_lighting_mw * n_days) FILTER (WHERE hour BETWEEN 1 AND 4) AS exterior_lighting_1_5am_mwh,
               sum(total_electricity_mw * n_days) AS total_electricity_mwh,
               any_value(floor_area_sqft) AS floor_area_sqft, any_value(models_used) AS models_used
        FROM comstock_hourly GROUP BY ALL ORDER BY ALL""")
    rows = [(cat, rule, ", ".join(types)) for cat, rule, types in CROSSWALK]
    con.execute("CREATE TABLE type_crosswalk (category VARCHAR, pluto_rule VARCHAR, comstock_types VARCHAR)")
    con.executemany("INSERT INTO type_crosswalk VALUES (?, ?, ?)", rows)


# --- PLUTO, LL84 ---

def pluto_table(con) -> pd.DataFrame:
    p = pd.read_csv(RAW / "open_data" / "pluto.csv", low_memory=False,
                    dtype={"bbl": str, "bldgclass": str, "landuse": str, "zipcode": str, "landmark": str, "histdist": str})
    p = p.dropna(subset=["latitude", "longitude"])
    p["bbl"] = p["bbl"].str.replace(r"\.0+$", "", regex=True)
    p["hex"] = [h3.latlng_to_cell(la, lo, HEX_RES) for la, lo in zip(p["latitude"], p["longitude"])]
    p = p.merge(BOROUGHS[["borough", "pluto_code"]].rename(columns={"borough": "borough_name"}),
                left_on="borough", right_on="pluto_code", how="left").drop(columns="pluto_code")
    con.execute("CREATE TABLE pluto AS SELECT * FROM p")
    return p


def ll84_table(con) -> None:
    d = pd.read_csv(RAW / "open_data" / "ll84_annual.csv", dtype=str)
    num = lambda c: pd.to_numeric(d[c], errors="coerce")
    out = pd.DataFrame({
        "report_year": num("report_year").astype("Int64"),
        "property_id": d["property_id"],
        "bbl_raw": d["nyc_borough_block_and_lot"],
        "primary_property_type": d["primary_property_type"],
        "largest_property_use_type": d["largest_property_use_type"],
        "year_built": num("year_built"),
        "number_of_buildings": num("number_of_buildings"),
        "occupancy_pct": num("occupancy"),
        "gfa_sqft": num("property_gfa_self_reported"),
        "electricity_kwh": num("electricity_use_grid_purchase_1"),
        "site_eui_kbtu_sqft": num("site_eui_kbtu_ft"),
        "energy_star_score": num("energy_star_score"),
        "office_weekly_hours": num("office_weekly_operating_hours"),
        "estimated_values_energy": d["estimated_values_energy"],
        "borough": d["borough"],
        "latitude": num("latitude"),
        "longitude": num("longitude"),
        "nta2020": d["nta2020"],
    })
    first = out["bbl_raw"].fillna("").str.extract(r"(\d)\D*(\d{1,5})\D*(\d{1,4})")
    out["bbl"] = np.where(first[0].notna(), first[0] + first[1].str.zfill(5) + first[2].str.zfill(4), None)
    out["elec_kwh_per_sqft"] = np.where(out["gfa_sqft"] > 0, out["electricity_kwh"] / out["gfa_sqft"], np.nan)
    ok = out["latitude"].notna() & out["longitude"].notna()
    out["hex"] = None
    out.loc[ok, "hex"] = [h3.latlng_to_cell(la, lo, HEX_RES) for la, lo in zip(out.loc[ok, "latitude"], out.loc[ok, "longitude"])]
    con.execute("CREATE TABLE ll84 AS SELECT * FROM out")


# --- Hexagons: floor area + estimated lighting ---

def hex_tables(con, pluto: pd.DataFrame) -> set[str]:
    p = pluto.fillna({c: 0 for c in ["bldgarea", "comarea", "resarea", "officearea", "retailarea", "strgearea"]})
    cls = p["bldgclass"].fillna("")
    rest = (p["bldgarea"] - p["officearea"] - p["retailarea"]).clip(lower=0)
    cat_area = pd.DataFrame({
        "office": p["officearea"], "retail": p["retailarea"], "warehouse": p["strgearea"],
        "hotel": np.where(cls.str.startswith("H"), rest, 0),
        "hospital": np.where(cls == "I1", rest, 0),
        "outpatient": np.where(cls.str.match(r"I[2-9]"), rest, 0),
        "school": np.where(cls.isin(["W1", "W2", "W3", "W8"]), rest, 0),
    })
    cat_area["hex"], cat_area["borough"] = p["hex"], p["borough_name"]

    # Overnight (1-5 a.m.) interior lighting in W per sq ft, per borough and category, from ComStock.
    type_cat = pd.DataFrame([(t, cat) for cat, _, types in CROSSWALK for t in types], columns=["building_type", "category"])
    con.register("type_cat", type_cat)
    intensity = con.execute("""
        SELECT a.borough, tc.category, a.upgrade,
               sum(a.interior_lighting_1_5am_mwh) / (365 * 4) * 1e6 / sum(a.floor_area_sqft) AS w_per_sqft
        FROM comstock_annual a JOIN type_cat tc USING (building_type) GROUP BY ALL""").df()

    long = cat_area.melt(id_vars=["hex", "borough"], var_name="category", value_name="sqft")
    long = long[long["sqft"] > 0].merge(intensity, on=["borough", "category"])
    long["mw"] = long["sqft"] * long["w_per_sqft"] / 1e6
    est = long.pivot_table(index="hex", columns="upgrade", values="mw", aggfunc="sum").rename(
        columns={0: "est_lighting_base_mw", 44: "est_lighting_controls_mw"})

    agg = p.groupby("hex").agg(
        borough=("borough_name", lambda s: s.mode().iat[0] if not s.mode().empty else None),
        n_lots=("bbl", "size"), bldgarea_sqft=("bldgarea", "sum"), comarea_sqft=("comarea", "sum"),
        resarea_sqft=("resarea", "sum"), officearea_sqft=("officearea", "sum"), retailarea_sqft=("retailarea", "sum"))
    agg["commercial_share"] = np.where(agg["bldgarea_sqft"] > 0, agg["comarea_sqft"] / agg["bldgarea_sqft"], 0)
    cells = agg.join(est).fillna({"est_lighting_base_mw": 0, "est_lighting_controls_mw": 0}).reset_index()
    cells[["lat", "lon"]] = [h3.cell_to_latlng(h) for h in cells["hex"]]
    con.execute("CREATE TABLE hex_cells AS SELECT * FROM cells ORDER BY est_lighting_base_mw DESC")

    features = [{"type": "Feature", "properties": {"hex": h},
                 "geometry": {"type": "Polygon",
                              "coordinates": [[[round(lo, 5), round(la, 5)] for la, lo in
                                               list(h3.cell_to_boundary(h)) + [h3.cell_to_boundary(h)[0]]]]}}
                for h in cells["hex"]]
    (ROOT / "data" / "hex_grid.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return set(cells["hex"])


# --- Black Marble ---

def read_layer(h5: h5py.File, name: str) -> np.ndarray:
    ds = h5[f"HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/{name}"]
    data = ds[()].astype("float64")
    fill = ds.attrs.get("_FillValue")
    if fill is not None:
        data[data == float(np.ravel(fill)[0])] = np.nan
    data = data * float(np.ravel(ds.attrs.get("scale_factor", [1.0]))[0]) + float(np.ravel(ds.attrs.get("add_offset", [0.0]))[0])
    return data


def nightlights_table(con, nyc_hexes: set[str]) -> None:
    rows = []
    for path in sorted((RAW / "blackmarble").glob("VNP46A*/*.h5")):
        product = path.name.split(".")[0]
        year, doy = int(path.name.split(".")[1][1:5]), int(path.name.split(".")[1][5:8])
        period = str(year) if product == "VNP46A4" else (date(year, 1, 1) + timedelta(days=doy - 1)).strftime("%Y-%m")
        with h5py.File(path, "r") as h:
            lat = h["HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/lat"][()]
            lon = h["HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/lon"][()]
            r = np.where((lat >= NYC_BBOX["lat"][0]) & (lat <= NYC_BBOX["lat"][1]))[0]
            c = np.where((lon >= NYC_BBOX["lon"][0]) & (lon <= NYC_BBOX["lon"][1]))[0]
            rad = read_layer(h, "NearNadir_Composite_Snow_Free")[np.ix_(r, c)]
            qual = h["HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/NearNadir_Composite_Snow_Free_Quality"][()][np.ix_(r, c)]
        for i, la in enumerate(lat[r]):
            for j, lo in enumerate(lon[c]):
                cell = h3.latlng_to_cell(float(la), float(lo), HEX_RES)
                if cell in nyc_hexes:
                    rows.append((period, product, float(la), float(lo), cell,
                                 None if np.isnan(rad[i, j]) else float(rad[i, j]), int(qual[i, j])))
        print(f"  nightlights {period}: {len(r)}x{len(c)} pixels", flush=True)
    df = pd.DataFrame(rows, columns=["period", "product", "lat", "lon", "hex", "radiance", "quality"])
    con.execute("CREATE TABLE nightlights AS SELECT * FROM df ORDER BY period, hex")
    con.execute("""CREATE VIEW hex_nightlights AS
        SELECT hex, period, product, avg(radiance) FILTER (WHERE quality = 0) AS radiance_mean,
               count(*) FILTER (WHERE quality = 0) AS good_pixels, count(*) AS pixels
        FROM nightlights GROUP BY ALL""")


# --- Constants, schema doc ---

def constants_table(con) -> None:
    con.execute("CREATE TABLE constants (name VARCHAR, value DOUBLE, unit VARCHAR, description VARCHAR, url VARCHAR)")
    con.executemany("INSERT INTO constants VALUES (?, ?, ?, ?, ?)", CONSTANTS)


def write_schema(con) -> None:
    lines = ["# Schema: nyc_overnight.duckdb", "",
             "Generated by build/build_db.py. MEASURED: nyiso_*, ll84, nightlights. MODELED: comstock_*, hex_cells.est_*.", ""]
    for table, (desc, notes) in TABLES.items():
        cols = con.execute(f"DESCRIBE {table}").fetchall()
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        lines += [f"## {table} ({n:,} rows)", "", desc, ""]
        for name, ctype, *_ in cols:
            note = notes.get(name, "")
            lines.append(f"- `{name}` {ctype}" + (f": {note}" if note else ""))
        lines.append("")
    (ROOT / "data" / "schema.md").write_text("\n".join(lines))


def main() -> None:
    tmp = DB_PATH.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    steps = [("nyiso", lambda: nyiso_tables(con)), ("comstock", lambda: comstock_tables(con))]
    for name, step in steps:
        print(f"building {name}...", flush=True)
        step()
    print("building pluto...", flush=True)
    pluto = pluto_table(con)
    print("building ll84...", flush=True)
    ll84_table(con)
    print("building hexagons...", flush=True)
    hexes = hex_tables(con, pluto)
    print("building nightlights...", flush=True)
    nightlights_table(con, hexes)
    constants_table(con)
    write_schema(con)
    con.execute("CHECKPOINT")
    con.close()
    tmp.replace(DB_PATH)
    print(f"done: {DB_PATH} ({DB_PATH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
