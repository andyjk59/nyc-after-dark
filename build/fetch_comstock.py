"""Download NREL ComStock 2025 release 3 county time-series aggregates for NYC's five counties.

    python3 -I build/fetch_comstock.py

Upgrade 0 is the baseline; upgrade 44 is Lighting Controls. That's 5 counties x 15 building types
x 2 upgrades = 150 CSVs, about 2.7 GB, from NREL's public OEDI bucket, into data/raw/comstock/.
Files whose size already matches the bucket listing are skipped.
"""

import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "comstock"
BUCKET = "https://oedi-data-lake.s3.amazonaws.com"
PREFIX = ("nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/"
          "comstock_amy2018_release_3/timeseries_aggregates/by_county")
COUNTIES = {"G3600050": "Bronx", "G3600470": "Kings", "G3600610": "New York", "G3600810": "Queens",
            "G3600850": "Richmond"}
UPGRADES = [0, 44]


def listing(prefix: str) -> list[tuple[str, int]]:
    with urllib.request.urlopen(f"{BUCKET}/?list-type=2&prefix={prefix}", timeout=60) as r:
        xml = r.read().decode()
    return [(k, int(s)) for k, s in re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", xml)]


def fetch(key: str, size: int) -> str:
    dest = RAW / key[len(PREFIX) + 1:]
    if dest.exists() and dest.stat().st_size == size:
        return f"skip {dest.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(f"{BUCKET}/{key}", timeout=120) as r, tmp.open("wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
        if tmp.stat().st_size != size:
            raise IOError(f"got {tmp.stat().st_size} bytes, expected {size}")
        tmp.rename(dest)
        return f"ok   {dest.name} {size // 1_000_000} MB"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"FAIL {dest.name}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    files = [f for up in UPGRADES for c in COUNTIES for f in listing(f"{PREFIX}/upgrade={up}/county={c}/")]
    print(f"comstock: {len(files)} files, {sum(s for _, s in files) / 1e9:.2f} GB", flush=True)
    with ThreadPoolExecutor(8) as pool:
        for line in pool.map(lambda f: fetch(*f), files):
            if not line.startswith("skip"):
                print(line, flush=True)
    present = sum(1 for k, s in files if (RAW / k[len(PREFIX) + 1:]).exists())
    print(f"comstock: {present}/{len(files)} files present")
    sys.exit(0 if present == len(files) == 150 else 1)
