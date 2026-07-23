#!/usr/bin/env python3
"""Daily Agmarknet mandi (APMC) commodity prices via the data.gov.in API.

Source: "Current Daily Price of Various Commodities from Various Markets"
(Ministry of Agriculture & Farmers Welfare), resource 9ef84268.
Uses data.gov.in's PUBLIC demo key by default (documented on data.gov.in);
it works but caps every response at 10 records, so a full ~16k-row day takes
~1,700 requests. Register a free personal key at data.gov.in and set
DATA_GOV_IN_KEY to get proper page sizes (see DATA_ACCESS.md).

The feed fills through the IST trading day — schedule pulls after ~14:00 IST.
Re-running on the same day replaces that day's rows (idempotent).

Usage:  python3 collectors/mandi_prices.py [--db data/agri.duckdb]
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import duckdb

# Public demo key published on data.gov.in — not a secret.
DEFAULT_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# The API backend is Elasticsearch with index.max_result_window=10000:
# offset+limit past 10k errors out with ANY key, and the daily feed runs
# ~16k records — so it cannot be read in one unfiltered sweep. Partition
# by state (each far below 10k rows/day) and paginate within each.
WINDOW = 10000

# Superset of Agmarknet state names, including the feed's own spellings
# (Keralam, Chattisgarh, Pondicherry, NCT of Delhi) alongside standard
# alternates — an absent name costs one zero-total probe request, and a
# state string missing from this list surfaces via the grand-total check.
STATES = [
    "Andaman and Nicobar", "Andhra Pradesh", "Arunachal Pradesh", "Assam",
    "Bihar", "Chandigarh", "Chattisgarh", "Chhattisgarh",
    "Dadra and Nagar Haveli", "Daman and Diu", "Delhi", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand",
    "Karnataka", "Kerala", "Keralam", "Ladakh", "Lakshadweep",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "NCT of Delhi", "Nagaland", "Odisha", "Orissa", "Pondicherry",
    "Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "Uttaranchal",
    "West Bengal",
]


def get_json(url, retries=10):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=30
            ) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if i == retries - 1:
                raise
            # The shared demo key throttles after a burst of a few hundred
            # requests; the window clears in a few minutes — wait it out.
            time.sleep(60 if e.code == 429 else 3 * (i + 1))
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(3 * (i + 1))


def _url(key, limit, offset, state=None):
    u = (f"https://api.data.gov.in/resource/{RESOURCE}"
         f"?api-key={key}&format=json&limit={limit}&offset={offset}")
    if state is not None:
        u += "&filters%5Bstate.keyword%5D=" + urllib.parse.quote(state)
    return u


def _fetch_state(key, state, requested):
    """One filter partition: paginate on the server's reported total and the
    page size it actually returns (the demo key clamps to 10), never on the
    requested limit."""
    recs, offset, total, empties = [], 0, None, 0
    while True:
        d = get_json(_url(key, requested, offset, state))
        page = d.get("records", [])
        if total is None:
            total = int(d.get("total") or 0)
            if total > WINDOW:
                print(f"WARNING: {state} has {total} rows — exceeds the "
                      f"{WINDOW}-row API result window; partition will be "
                      f"incomplete")
        if not page:
            if offset >= min(total, WINDOW):
                break
            # Throttled responses can be HTTP 200 with an empty records
            # list — don't mistake one for end-of-data mid-partition.
            empties += 1
            if empties > 5:
                print(f"WARNING: {state}: repeated empty pages at "
                      f"{offset}/{total} — giving up on this partition")
                break
            time.sleep(60)
            continue
        empties = 0
        recs.extend(page)
        offset += len(page)
        if offset >= min(total, WINDOW):
            break
        time.sleep(0.3)
    return recs, total


def collect(db_path):
    key = os.environ.get("DATA_GOV_IN_KEY", DEFAULT_KEY)
    requested = 1000

    probe = get_json(_url(key, requested, 0))
    grand_total = int(probe.get("total") or 0)
    page = len(probe.get("records", [])) or 1
    if page < min(requested, grand_total):
        n_req = -(-grand_total // page) + len(STATES)
        print(f"note: server caps pages at {page} rows "
              f"(~{n_req} requests for {grand_total} records)"
              + (" — set DATA_GOV_IN_KEY to a free personal "
                 "data.gov.in key for larger pages"
                 if key == DEFAULT_KEY else ""))

    # A throttled 200-with-empty-records can also zero out a state's probe,
    # so reconcile against the grand total and re-pull short partitions.
    results = {}  # state -> (recs, reported_total)
    todo = list(STATES)
    for attempt in range(3):
        if attempt:
            got = sum(len(r) for r, _ in results.values())
            print(f"  pass {attempt}: {got}/{grand_total} — "
                  f"retrying {len(todo)} states in 90s")
            time.sleep(90)
            grand_total = int(
                get_json(_url(key, 1, 0)).get("total") or grand_total)
        for state in todo:
            recs, total = _fetch_state(key, state, requested)
            if state not in results or len(recs) > len(results[state][0]):
                results[state] = (recs, total)
                if recs:
                    print(f"  {state}: {len(recs)}/{total}")
            time.sleep(0.3)
        if sum(len(r) for r, _ in results.values()) >= grand_total:
            break
        todo = [s for s in STATES
                if len(results[s][0]) < min(results[s][1] or 0, WINDOW)
                or (results[s][1] == 0 and not results[s][0])]

    rows = []
    for recs, _ in results.values():
        for r in recs:
            rows.append((
                r.get("state"), r.get("district"), r.get("market"),
                r.get("commodity"), r.get("variety"), r.get("grade"),
                r.get("arrival_date"),
                r.get("min_price"), r.get("max_price"), r.get("modal_price"),
                date.today().isoformat(),
            ))

    if len(rows) != grand_total:
        print(f"WARNING: collected {len(rows)} rows but the API reported "
              f"total={grand_total} — today's pull may be incomplete "
              f"(throttled probes, or a state name missing from STATES?)")

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
