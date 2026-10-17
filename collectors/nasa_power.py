#!/usr/bin/env python3
"""NASA POWER monthly agro-climate (rainfall, temperature) for major Indian
agri states (open API, no key).

One representative point per state (main agricultural belt / capital region --
an approximation, not a state average). Rebuilds `nasa_power_monthly`.

Usage:  python3 collectors/nasa_power.py [--start 2015] [--end 2025] [--db data/agri.duckdb]
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path

import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
PARAMS = {"PRECTOTCORR": "rain_mm_per_day", "T2M": "temp_c_mean"}
# state -> (lat, lon); state names follow the Agmarknet feed spellings
POINTS = {
    "Punjab": (30.9, 75.85), "Haryana": (29.06, 76.08),
    "Uttar Pradesh": (26.85, 80.95), "Madhya Pradesh": (23.26, 77.41),
    "Rajasthan": (26.91, 75.79), "Gujarat": (23.02, 72.57),
    "Maharashtra": (19.99, 73.79), "Karnataka": (15.35, 75.12),
    "Andhra Pradesh": (16.51, 80.65), "Telangana": (17.38, 78.48),
    "Tamil Nadu": (11.13, 78.66), "West Bengal": (23.0, 87.9),
    "Bihar": (25.6, 85.14), "Odisha": (20.27, 85.84),
}


def url(lat, lon, start, end):
    return ("https://power.larc.nasa.gov/api/temporal/monthly/point?"
            f"parameters={','.join(PARAMS)}&community=AG&longitude={lon}"
            f"&latitude={lat}&start={start}&end={end}&format=JSON")


def get_json(u, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(u, headers=UA), timeout=90
            ) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def parse(state, lat, lon, payload):
    """Rows (state, lat, lon, year, month, parameter, value); skips month 13
    (annual mean) and the -999 fill value."""
    out = []
    for p, name in PARAMS.items():
        for ym, v in payload["properties"]["parameter"][p].items():
            year, month = int(ym[:4]), int(ym[4:])
            if month > 12 or v == -999:
                continue
            out.append((state, lat, lon, year, month, name, float(v)))
    return out


def collect(db_path, start=2015, end=2025):
    rows = []
    for state, (lat, lon) in POINTS.items():
        rows += parse(state, lat, lon, get_json(url(lat, lon, start, end)))
        time.sleep(0.5)
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE nasa_power_monthly (
        state TEXT, lat DOUBLE, lon DOUBLE, year INTEGER, month INTEGER,
        parameter TEXT, value DOUBLE)""")
    con.executemany("INSERT INTO nasa_power_monthly VALUES (?,?,?,?,?,?,?)", rows)
    con.close()
    print(f"nasa_power_monthly: {len(rows):,} rows -> {db_path}")
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "agri.duckdb"))
    ap.add_argument("--start", type=int, default=2015)
    ap.add_argument("--end", type=int, default=2025)
    a = ap.parse_args()
    collect(a.db, a.start, a.end)
