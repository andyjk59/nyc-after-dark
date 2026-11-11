"""Keep the grid data current while the app runs.

The shipped database stops on the day it was built. NYISO adds each day to the current month's zip early
the next morning, so refresh() re-downloads the months from the database's newest hour through today,
swaps those hours into a copy of the database, and returns the copy's path for the app to serve. Only
nyiso_load, nyiso_price and calendar change: nyiso_hourly and overnight_daily are views over them.

Standard library only (pandas is in the offline build group, not on Cloud Run). The parsing matches
build/build_db.py's: same columns, same time handling, first row kept when an hour repeats.
"""

import csv
import io
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

LOAD_ZIP = "http://mis.nyiso.com/public/csv/palIntegrated/{ym}01palIntegrated_csv.zip"
PRICE_ZIP = "http://mis.nyiso.com/public/csv/damlbmp/{ym}01damlbmp_zone_csv.zip"
LOAD_DAY = "http://mis.nyiso.com/public/csv/palIntegrated/{ymd}palIntegrated.csv"
ZONE = "N.Y.C."
# NYC load runs about 3,000-12,000 MW. Anything far outside means a broken file: keep the old data.
LOAD_RANGE, PRICE_RANGE = (500, 20_000), (-1_000, 10_000)


def download(url: str, timeout: int = 30) -> bytes | None:
    """The file's bytes, or None if NYISO hasn't posted it (404). Other failures raise."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def zip_csvs(blob: bytes) -> list[tuple[str, str]]:
    """(name, text) of each daily CSV in a monthly zip, in date order."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return [(name, z.read(name).decode()) for name in sorted(z.namelist())]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ny_today() -> date:
    """Today in New York, near enough (EST all year): Cloud Run's clock is UTC."""
    return (utc_now() - timedelta(hours=5)).date()


def utc_offset(local: datetime, repeat: bool = False) -> int:
    """Hours to add to New York local time to get UTC (US rules since 2007). On the fall-back night the
    1 a.m. hour happens twice: repeat=True means the second one (EST). A spring-forward 2 a.m. that
    doesn't exist is read as 3 a.m. EDT, like build_db.py's shift_forward."""
    march, nov = date(local.year, 3, 1), date(local.year, 11, 1)
    start = datetime.combine(march + timedelta(days=(6 - march.weekday()) % 7 + 7), datetime.min.time()) + timedelta(hours=2)
    end = datetime.combine(nov + timedelta(days=(6 - nov.weekday()) % 7), datetime.min.time()) + timedelta(hours=1)
    if start <= local < start + timedelta(hours=1):
        return 5
    if end <= local < end + timedelta(hours=1):
        return 5 if repeat else 4
    return 4 if start <= local < end else 5


def load_rows(texts: list[str]) -> dict[datetime, tuple]:
    """NYC rows of palIntegrated CSVs: ts_utc -> (ts_utc, ts_local, date_local, hour_local, tz, load_mw)."""
    rows = {}
    for text in texts:
        for r in csv.DictReader(io.StringIO(text)):
            if r["Name"] != ZONE:
                continue
            local = datetime.strptime(r["Time Stamp"], "%m/%d/%Y %H:%M:%S")
            utc = local + timedelta(hours=4 if r["Time Zone"] == "EDT" else 5)
            mw = float(r["Integrated Load"]) if r["Integrated Load"] else None
            rows.setdefault(utc, (utc, local, local.date(), local.hour, r["Time Zone"], mw))
    return rows


def price_rows(texts: list[str]) -> dict[datetime, tuple]:
    """NYC rows of damlbmp zone CSVs: ts_utc -> (ts_utc, ts_local, lbmp, congestion, losses)."""
    rows, seen = {}, set()
    for text in texts:
        for r in csv.DictReader(io.StringIO(text)):
            if r["Name"] != ZONE:
                continue
            local = datetime.strptime(r["Time Stamp"], "%m/%d/%Y %H:%M")
            utc = local + timedelta(hours=utc_offset(local, repeat=local in seen))
            seen.add(local)
            num = lambda k: float(r[k]) if r[k] else None
            rows.setdefault(utc, (utc, local, num("LBMP ($/MWHr)"), num("Marginal Cost Congestion ($/MWHr)"),
                                  num("Marginal Cost Losses ($/MWHr)")))
    return rows


def federal_holidays(year: int) -> set[date]:
    """US federal holidays as pandas' USFederalHolidayCalendar has them (weekend dates move to Fri/Mon)."""
    def nth(month, weekday, n):  # n-th weekday of the month; n=-1 for the last
        if n > 0:
            first = date(year, month, 1)
            return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
        last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        return last - timedelta(days=(last.weekday() - weekday) % 7)

    def observed(d):
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d

    days = {observed(date(year, 1, 1)), nth(1, 0, 3), nth(2, 0, 3), nth(5, 0, -1), observed(date(year, 7, 4)),
            nth(9, 0, 1), nth(10, 0, 2), observed(date(year, 11, 11)), nth(11, 3, 4), observed(date(year, 12, 25))}
    if year >= 2021:
        days.add(observed(date(year, 6, 19)))
    if observed(date(year + 1, 1, 1)).year == year:  # Jan 1 on a Saturday is observed Dec 31
        days.add(date(year, 12, 31))
    return days


def calendar_row(d: date) -> tuple:
    day_type = "holiday" if d in federal_holidays(d.year) else "weekend" if d.weekday() >= 5 else "weekday"
    covid = "pre" if d < date(2020, 3, 22) else "pause" if d < date(2020, 6, 8) else "reopening" if d < date(2020, 7, 20) else "post"
    return (d, day_type, covid, d.year, d.month, d.weekday())


def months(first: date, last: date) -> list[str]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def check(rows: dict, index: int, bounds: tuple, what: str):
    values = [r[index] for r in rows.values() if r[index] is not None]
    bad = [v for v in values if not bounds[0] <= v <= bounds[1]]
    if bad:
        raise ValueError(f"{len(bad)} {what} values outside {bounds}, e.g. {bad[0]}")


def refresh(current: Path, workdir: Path | None = None) -> tuple[Path, str] | None:
    """Copy the database at `current`, bring its grid tables up to date, and return (new path, summary).
    None when NYISO has nothing newer. Raises if a download or a file looks wrong; `current` is untouched."""
    workdir = workdir or Path(tempfile.gettempdir())
    target = workdir / f"nyc_overnight-{utc_now():%Y%m%d%H%M%S%f}.duckdb"
    shutil.copyfile(current, target)
    con = None
    try:
        con = duckdb.connect(str(target))
        (load_max,) = con.execute("SELECT max(ts_local) FROM nyiso_load").fetchone()
        (price_max,) = con.execute("SELECT max(ts_local) FROM nyiso_price").fetchone()
        today = ny_today()
        loads, prices = {}, {}
        for i, ym in enumerate(wanted := months(min(load_max, price_max).date(), today)):
            current_month = i == len(wanted) - 1
            for url, parse, out in ((LOAD_ZIP, load_rows, loads), (PRICE_ZIP, price_rows, prices)):
                blob = download(url.format(ym=ym))
                if blob is None:
                    if current_month:  # the 1st of a month: NYISO hasn't started the new zip yet
                        continue
                    raise FileNotFoundError(url.format(ym=ym))  # a gap would delete hours we can't put back
                out.update(parse([text for _, text in zip_csvs(blob)]))
        check(loads, 5, LOAD_RANGE, "load")
        check(prices, 2, PRICE_RANGE, "price")
        new_load_max = max((r[1] for r in loads.values()), default=load_max)
        new_price_max = max((r[1] for r in prices.values()), default=price_max)
        if new_load_max <= load_max and new_price_max <= price_max:
            con.close()
            target.unlink()
            return None

        con.execute("BEGIN")
        for table, rows, columns in (("nyiso_load", loads, 6), ("nyiso_price", prices, 5)):
            if rows:
                con.execute(f"DELETE FROM {table} WHERE ts_utc BETWEEN ? AND ?", [min(rows), max(rows)])
                con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * columns)})",
                                [rows[k] for k in sorted(rows)])
        (calendar_max,) = con.execute("SELECT max(date_local) FROM calendar").fetchone()
        last_day = max(today, new_load_max.date(), new_price_max.date())
        new_days = [calendar_max + timedelta(days=n) for n in range(1, (last_day - calendar_max).days + 1)]
        if new_days:
            con.executemany("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?)", [calendar_row(d) for d in new_days])
        con.execute("COMMIT")
        con.execute("CHECKPOINT")
        con.close()
    except BaseException:
        if con is not None:
            con.close()
        target.unlink(missing_ok=True)
        raise
    return target, (f"grid data now through {new_load_max:%Y-%m-%d %H:%M} (load) and "
                    f"{new_price_max:%Y-%m-%d %H:%M} (prices), was {load_max:%Y-%m-%d %H:%M} / {price_max:%Y-%m-%d %H:%M}")


def load_day(day: date) -> tuple[str, str] | None:
    """(source url, CSV text) of one day's palIntegrated file. NYISO keeps daily files only for the
    current month; older days come out of that month's zip."""
    url = LOAD_DAY.format(ymd=f"{day:%Y%m%d}")
    blob = download(url, timeout=20)
    if blob is not None:
        return url, blob.decode()
    url = LOAD_ZIP.format(ym=f"{day:%Y%m}")
    blob = download(url, timeout=30)
    if blob is None:
        return None
    for name, text in zip_csvs(blob):
        if name.startswith(f"{day:%Y%m%d}"):
            return url, text
    return None
