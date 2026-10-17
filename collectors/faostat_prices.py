#!/usr/bin/env python3
"""FAOSTAT producer prices (bulk CSV, no key) for India + comparator countries.

Streams the ~12 MB zip (Prices_E_All_Data_(Normalized)) from bulks-faostat.fao.org,
keeps the 7 panel countries, and rebuilds the `faostat_producer_prices` table.

Usage:  python3 collectors/faostat_prices.py [--db data/agri.duckdb]
"""

import argparse
import csv
import io
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
URL = ("https://bulks-faostat.fao.org/production/"
       "Prices_E_All_Data_(Normalized).zip")
# FAOSTAT area codes for the same 7 countries as worldbank_agri
AREAS = {"100": "IND", "41": "CHN", "21": "BRA", "231": "USA",
         "101": "IDN", "237": "VNM", "216": "THA"}


def download(url, dest, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=90
            ) as r, open(dest, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            return
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def parse(zip_path):
    """Yield table rows for the panel countries from the normalized CSV."""
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith("All_Data_(Normalized).csv"))
        with z.open(name) as raw:
            for r in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                iso = AREAS.get(r["Area Code"])
                if iso is None or r["Value"] == "":
                    continue
                yield (iso, r["Area"], int(r["Item Code"]), r["Item"],
                       int(r["Element Code"]), r["Element"], int(r["Year"]),
                       r["Months"], r["Unit"], float(r["Value"]), r["Flag"])


def collect(db_path):
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / "prices.zip"
        download(URL, zp)
        rows = list(parse(zp))
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE faostat_producer_prices (
        country TEXT, area TEXT, item_code INTEGER, item TEXT,
        element_code INTEGER, element TEXT, year INTEGER, months TEXT,
        unit TEXT, value DOUBLE, flag TEXT)""")
    con.executemany(
        "INSERT INTO faostat_producer_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.close()
    print(f"faostat_producer_prices: {len(rows):,} rows -> {db_path}")
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "agri.duckdb"))
    collect(ap.parse_args().db)
