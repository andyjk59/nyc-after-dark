"""Download NYISO monthly CSV zips: integrated hourly load and day-ahead zonal prices.

    python3 -I build/fetch_nyiso.py [first YYYY-MM] [last YYYY-MM]

Files land in data/raw/nyiso/<product>/. Months already on disk are skipped, so re-running only
fetches what's missing. The current month's zip grows daily: delete it to refresh.
"""

import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "nyiso"
PRODUCTS = {
    "palIntegrated": "http://mis.nyiso.com/public/csv/palIntegrated/{ym}01palIntegrated_csv.zip",
    "damlbmp_zone": "http://mis.nyiso.com/public/csv/damlbmp/{ym}01damlbmp_zone_csv.zip",
}


def months(first: str, last: str) -> list[str]:
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch(product: str, ym: str) -> str:
    dest = RAW / product / f"{ym}.zip"
    if dest.exists() and dest.stat().st_size > 0:
        return f"skip {product} {ym}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(PRODUCTS[product].format(ym=ym), timeout=60) as r:
            tmp.write_bytes(r.read())
        tmp.rename(dest)
        return f"ok   {product} {ym} {dest.stat().st_size // 1024} KB"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"FAIL {product} {ym}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    first = sys.argv[1] if len(sys.argv) > 1 else "2016-01"
    last = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y-%m")
    jobs = [(p, ym) for p in PRODUCTS for ym in months(first, last)]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda job: fetch(*job), jobs))
    for line in results:
        if not line.startswith("skip"):
            print(line)
    failed = [r for r in results if r.startswith("FAIL")]
    print(f"nyiso: {len(results) - len(failed)}/{len(results)} files present")
    sys.exit(1 if failed else 0)
