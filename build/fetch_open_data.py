"""Download MapPLUTO attributes and LL84 annual benchmarking from NYC Open Data (Socrata API).

    python3 -I build/fetch_open_data.py

Writes data/raw/open_data/pluto.csv and ll84_annual.csv, paging 50,000 rows at a time. Only the
columns the agent uses are kept (no owner names). An NYC_APP_TOKEN in .env raises the rate limit
but isn't required.
"""

import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "open_data"
PAGE = 50_000

DATASETS = {
    "pluto": {
        "id": "64uk-42ks",
        "order": "bbl",
        "where": None,
        "columns": [
            "bbl", "borough", "borocode", "block", "lot", "cd", "council", "zipcode", "bct2020",
            "bldgclass", "landuse", "ownertype", "numbldgs", "numfloors", "yearbuilt", "yearalter1",
            "lotarea", "bldgarea", "comarea", "resarea", "officearea", "retailarea", "garagearea",
            "strgearea", "factryarea", "otherarea", "unitsres", "unitstotal", "landmark", "histdist",
            "latitude", "longitude", "version",
        ],
    },
    "ll84_annual": {
        "id": "5zyy-y8am",
        "order": ":id",
        "where": None,
        "columns": [
            "report_year", "property_id", "parent_property_id", "nyc_borough_block_and_lot",
            "nyc_building_identification", "primary_property_type", "largest_property_use_type",
            "largest_property_use_type_1", "list_of_all_property_use", "year_built",
            "number_of_buildings", "occupancy", "property_gfa_self_reported",
            "property_gfa_calculated_1", "electricity_use_grid_purchase_1",
            "electricity_use_grid_purchase_2", "site_eui_kbtu_ft", "energy_star_score",
            "office_weekly_operating_hours", "estimated_values_energy", "default_values",
            "alert_gross_floor_area_is", "borough", "latitude", "longitude", "nta2020",
            "council_district",
        ],
    },
}


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def fetch(name: str, spec: dict) -> int:
    dest = RAW / f"{name}.csv"
    tmp = dest.with_suffix(".part")
    headers = {"X-App-Token": os.environ["NYC_APP_TOKEN"]} if os.environ.get("NYC_APP_TOKEN") else {}
    rows, offset = 0, 0
    with tmp.open("w", encoding="utf-8") as out:
        while True:
            params = {"$select": ",".join(spec["columns"]), "$order": spec["order"],
                      "$limit": PAGE, "$offset": offset}
            if spec["where"]:
                params["$where"] = spec["where"]
            url = f"https://data.cityofnewyork.us/resource/{spec['id']}.csv?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as r:
                lines = r.read().decode("utf-8").splitlines(keepends=True)
            body = lines[1:]
            if offset == 0:
                out.write(lines[0])
            out.writelines(body)
            rows += len(body)
            print(f"{name}: {rows:,} rows", flush=True)
            if len(body) < PAGE:
                break
            offset += PAGE
    tmp.rename(dest)
    return rows


if __name__ == "__main__":
    load_env()
    RAW.mkdir(parents=True, exist_ok=True)
    for name, spec in DATASETS.items():
        try:
            print(f"{name}: done, {fetch(name, spec):,} rows")
        except Exception as e:
            print(f"{name}: FAIL {type(e).__name__}: {e}")
            sys.exit(1)
