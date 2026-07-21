#!/usr/bin/env python3
"""Daily Agmarknet mandi (APMC) commodity prices via the data.gov.in API.

Source: "Current Daily Price of Various Commodities from Various Markets"
(Ministry of Agriculture & Farmers Welfare), resource 9ef84268.
Uses data.gov.in's PUBLIC demo key by default (documented on data.gov.in);
set DATA_GOV_IN_KEY for a personal key.

The feed fills through the IST trading day — schedule pulls after ~14:00 IST.
Re-running on the same day replaces that day's rows (idempotent).

Usage:  python3 collectors/mandi_prices.py [--db data/agri.duckdb]
"""

import argparse
import json
import os
import time
import urllib.request
from datetime import date
from pathlib import Path

import duckdb

# Public demo key published on data.gov.in — not a secret.
DEFAULT_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


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
    key = os.environ.get("DATA_GOV_IN_KEY", DEFAULT_KEY)
    rows, offset, limit = [], 0, 1000
    while True:
        url = (f"https://api.data.gov.in/resource/{RESOURCE}"
               f"?api-key={key}&format=json&limit={limit}&offset={offset}")
        d = get_json(url)
        recs = d.get("records", [])
        for r in recs:
            rows.append((
                r.get("state"), r.get("district"), r.get("market"),
                r.get("commodity"), r.get("variety"), r.get("grade"),
                r.get("arrival_date"),
                r.get("min_price"), r.get("max_price"), r.get("modal_price"),
                date.today().isoformat(),
            ))
        if len(recs) < limit:
            break
        offset += limit
        time.sleep(0.5)

    con = duckdb.connect(str(db_path))
    con.execute("""CREATE TABLE IF NOT EXISTS mandi_prices (
        state TEXT, district TEXT, market TEXT, commodity TEXT,
        variety TEXT, grade TEXT, arrival_date TEXT,
        min_price DOUBLE, max_price DOUBLE, modal_price DOUBLE,
        pulled_on TEXT)""")
    con.execute("DELETE FROM mandi_prices WHERE pulled_on = ?",
                [date.today().isoformat()])
    con.executemany(
        "INSERT INTO mandi_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    total = con.execute("SELECT COUNT(*) FROM mandi_prices").fetchone()[0]
    con.close()
    print(f"mandi_prices: +{len(rows)} rows today, {total:,} total -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "agri.duckdb"))
    args = ap.parse_args()
    collect(args.db)
