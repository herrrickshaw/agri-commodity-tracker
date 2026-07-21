#!/usr/bin/env python3
"""World Bank agriculture indicator panel (open API, no key).

Pulls the full annual history (1960-present) for a set of agri indicators
across comparator countries into DuckDB. Rebuilds the table on each run —
the API is the system of record.

Usage:  python3 collectors/worldbank_agri.py [--db data/agri.duckdb]
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path

import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

INDICATORS = {
    "NV.AGR.TOTL.ZS": "agri_value_added_pct_gdp",
    "NV.AGR.TOTL.CD": "agri_value_added_usd",
    "AG.CON.FERT.ZS": "fertilizer_kg_per_ha",
    "AG.YLD.CREL.KG": "cereal_yield_kg_per_ha",
    "AG.PRD.FOOD.XD": "food_production_index",
    "AG.LND.AGRI.ZS": "agri_land_pct",
    "AG.LND.IRIG.AG.ZS": "irrigated_land_pct",
    "SL.AGR.EMPL.ZS": "agri_employment_pct",
    "TX.VAL.AGRI.ZS.UN": "agri_raw_exports_pct",
}
COUNTRIES = ["IND", "CHN", "BRA", "USA", "IDN", "VNM", "THA"]


def get_json(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=30
            ) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def collect(db_path):
    rows = []
    for iso in COUNTRIES:
        for code, name in INDICATORS.items():
            page = 1
            while True:
                d = get_json(
                    f"https://api.worldbank.org/v2/country/{iso}/indicator/"
                    f"{code}?format=json&per_page=1000&page={page}")
                if not d or len(d) < 2 or not d[1]:
                    break
                for obs in d[1]:
                    if obs.get("value") is not None:
                        rows.append((iso, code, name, int(obs["date"]),
                                     float(obs["value"]),
                                     d[0].get("lastupdated")))
                if page >= d[0].get("pages", 1):
                    break
                page += 1
            time.sleep(0.3)
        print(f"  {iso}: done ({len(rows)} rows cumulative)")

    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE worldbank_agri (
        country TEXT, indicator_code TEXT, indicator TEXT,
        year INTEGER, value DOUBLE, source_last_updated TEXT)""")
    con.executemany("INSERT INTO worldbank_agri VALUES (?,?,?,?,?,?)", rows)
    con.close()
    print(f"worldbank_agri: {len(rows):,} rows -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "agri.duckdb"))
    args = ap.parse_args()
    collect(args.db)
