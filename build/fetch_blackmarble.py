"""Download NASA Black Marble nighttime-lights tiles covering NYC (tile h10v04) from LAADS DAAC.

    python3 -I build/fetch_blackmarble.py

- VNP46A4 annual composites, 2013-2025
- VNP46A3 monthly composites for Mar-Jun 2019, Mar-Jun 2020 and Oct 2025-Sep 2026

About 33 HDF5 files, ~4.3 GB, into data/raw/blackmarble/<product>/. Needs BLACKMARBLE_TOKEN in
.env (a NASA Earthdata token). Files already on disk with the listed size are skipped.
"""

import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "blackmarble"
API = "https://ladsweb.modaps.eosdis.nasa.gov/api/v2/content"
TILE = "h10v04"

ANNUAL_YEARS = range(2013, 2026)
MONTHS = [(2019, m) for m in range(3, 7)] + [(2020, m) for m in range(3, 7)] + \
         [(2025, m) for m in range(10, 13)] + [(2026, m) for m in range(1, 10)]


def load_token() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("BLACKMARBLE_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("BLACKMARBLE_TOKEN is missing from .env")


def tile_in(product: str, year: int, doy: int) -> tuple[str, int, str] | None:
    """The NYC tile in one LAADS day-of-year folder: (name, size, download link)."""
    url = f"{API}/details/allData/5200/{product}/{year}/{doy:03d}?fields=all"
    with urllib.request.urlopen(url, timeout=60) as r:
        for item in json.load(r)["content"]:
            if f".{TILE}." in item["name"]:
                return item["name"], int(item["size"]), item["downloadsLink"]
    return None


def fetch(product: str, entry: tuple[str, int, str], token: str) -> str:
    name, size, link = entry
    dest = RAW / product / name
    if dest.exists() and dest.stat().st_size == size:
        return f"skip {name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        req = urllib.request.Request(link, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=600) as r, tmp.open("wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
        if tmp.stat().st_size != size:
            raise IOError(f"got {tmp.stat().st_size} bytes, expected {size}")
        tmp.rename(dest)
        return f"ok   {name} {size // 1_000_000} MB"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"FAIL {name}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    token = load_token()
    wanted = [("VNP46A4", y, 1) for y in ANNUAL_YEARS] + \
             [("VNP46A3", y, date(y, m, 1).timetuple().tm_yday) for y, m in MONTHS]
    found, missing = [], []
    for product, year, doy in wanted:
        entry = tile_in(product, year, doy)
        (found.append((product, entry)) if entry else missing.append(f"{product} {year}/{doy:03d}"))
    print(f"blackmarble: {len(found)} tiles listed, {sum(e[1] for _, e in found) / 1e9:.2f} GB", flush=True)
    for m in missing:
        print(f"not yet published: {m}")
    with ThreadPoolExecutor(3) as pool:
        for line in pool.map(lambda f: fetch(f[0], f[1], token), found):
            if not line.startswith("skip"):
                print(line, flush=True)
    present = sum(1 for p, e in found if (RAW / p / e[0]).exists())
    print(f"blackmarble: {present}/{len(found)} files present")
    sys.exit(0 if present == len(found) else 1)
