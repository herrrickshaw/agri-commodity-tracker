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

Pipeline:  fetch_all_states() -> rows_from_results() -> write_rows()
(network)                       (pure)                  (DuckDB)

Usage:  python3 collectors/mandi_prices.py [--db data/agri.duckdb]
"""

from __future__ import annotations

import logging
import os
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

if __package__ in (None, ""):  # run as a script: python3 collectors/mandi_prices.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import common  # noqa: E402

log = logging.getLogger(__name__)

# Public demo key published on data.gov.in — not a secret.
DEFAULT_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
API_BASE = f"https://api.data.gov.in/resource/{RESOURCE}"
UA = common.UA

# The API backend is Elasticsearch with index.max_result_window=10000:
# offset+limit past 10k errors out with ANY key, and the daily feed runs
# ~16k records — so it cannot be read in one unfiltered sweep. Partition
# by state (each far below 10k rows/day) and paginate within each.
WINDOW = 10000
REQUESTED_PAGE = 1000       # honoured by personal keys; demo key clamps to 10
RETRIES = 10
BACKOFF_S = 3               # sleep 3, 6, 9 … s between non-429 retries
THROTTLE_WAIT_S = 60        # the shared demo key throttles for minutes on 429
PAGE_PAUSE_S = 0.3
MAX_EMPTY_PAGES = 5
RECONCILE_PASSES = 3
RECONCILE_PAUSE_S = 90

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

TABLE_DDL = """CREATE TABLE IF NOT EXISTS mandi_prices (
    state TEXT, district TEXT, market TEXT, commodity TEXT,
    variety TEXT, grade TEXT, arrival_date TEXT,
    min_price DOUBLE, max_price DOUBLE, modal_price DOUBLE,
    pulled_on TEXT)"""

# state -> (records, server-reported total for that partition)
StateResults = dict[str, tuple[list[dict], int]]


def get_json(url: str, retries: int = RETRIES) -> Any:
    """GET JSON with the data.gov.in retry policy (long wait on 429)."""
    return common.get_json(url, retries=retries, backoff_s=BACKOFF_S,
                           throttle_wait_s=THROTTLE_WAIT_S)


def _url(key: str, limit: int, offset: int, state: str | None = None) -> str:
    """Resource URL for one page, optionally filtered to one state."""
    u = f"{API_BASE}?api-key={key}&format=json&limit={limit}&offset={offset}"
    if state is not None:
        u += "&filters%5Bstate.keyword%5D=" + urllib.parse.quote(state)
    return u


def _fetch_state(key: str, state: str, requested: int) -> tuple[list[dict], int]:
    """One filter partition: paginate on the server's reported total and the
    page size it actually returns (the demo key clamps to 10), never on the
    requested limit."""
    recs: list[dict] = []
    offset, total, empties = 0, None, 0
    while True:
        d = get_json(_url(key, requested, offset, state))
        page = d.get("records", [])
        if total is None:
            total = int(d.get("total") or 0)
            if total > WINDOW:
                log.warning("WARNING: %s has %d rows — exceeds the %d-row API result "
                            "window; partition will be incomplete", state, total, WINDOW)
        if not page:
            if offset >= min(total, WINDOW):
                break
            # Throttled responses can be HTTP 200 with an empty records
            # list — don't mistake one for end-of-data mid-partition.
            empties += 1
            if empties > MAX_EMPTY_PAGES:
                log.warning("WARNING: %s: repeated empty pages at %d/%d — giving up "
                            "on this partition", state, offset, total)
                break
            time.sleep(THROTTLE_WAIT_S)
            continue
        empties = 0
        recs.extend(page)
        offset += len(page)
        if offset >= min(total, WINDOW):
            break
        time.sleep(PAGE_PAUSE_S)
    return recs, total


def _short_states(results: StateResults) -> list[str]:
    """States whose partition came back shorter than the server said it holds."""
    return [s for s in STATES
            if len(results[s][0]) < min(results[s][1] or 0, WINDOW)
            or (results[s][1] == 0 and not results[s][0])]


def fetch_all_states(key: str, requested: int = REQUESTED_PAGE) -> tuple[StateResults, int]:
    """Pull every state partition, re-pulling short ones until the sum matches
    the API's grand total (up to RECONCILE_PASSES). Network only, no DB."""
    probe = get_json(_url(key, requested, 0))
    grand_total = int(probe.get("total") or 0)
    page = len(probe.get("records", [])) or 1
    if page < min(requested, grand_total):
        n_req = -(-grand_total // page) + len(STATES)
        log.info("note: server caps pages at %d rows (~%d requests for %d records)%s",
                 page, n_req, grand_total,
                 " — set DATA_GOV_IN_KEY to a free personal data.gov.in key for larger pages"
                 if key == DEFAULT_KEY else "")

    # A throttled 200-with-empty-records can also zero out a state's probe,
    # so reconcile against the grand total and re-pull short partitions.
    results: StateResults = {}
    todo = list(STATES)
    for attempt in range(RECONCILE_PASSES):
        if attempt:
            got = sum(len(r) for r, _ in results.values())
            log.info("  pass %d: %d/%d — retrying %d states in %ds",
                     attempt, got, grand_total, len(todo), RECONCILE_PAUSE_S)
            time.sleep(RECONCILE_PAUSE_S)
            grand_total = int(get_json(_url(key, 1, 0)).get("total") or grand_total)
        for state in todo:
            recs, total = _fetch_state(key, state, requested)
            if state not in results or len(recs) > len(results[state][0]):
                results[state] = (recs, total)
                if recs:
                    log.info("  %s: %d/%d", state, len(recs), total)
            time.sleep(PAGE_PAUSE_S)
        if sum(len(r) for r, _ in results.values()) >= grand_total:
            break
        todo = _short_states(results)
    return results, grand_total


def rows_from_results(results: StateResults, pulled_on: str) -> list[tuple]:
    """Flatten API records into mandi_prices tuples (column order = TABLE_DDL)."""
    rows = []
    for recs, _ in results.values():
        for r in recs:
            rows.append((
                r.get("state"), r.get("district"), r.get("market"),
                r.get("commodity"), r.get("variety"), r.get("grade"),
                r.get("arrival_date"),
                r.get("min_price"), r.get("max_price"), r.get("modal_price"),
                pulled_on,
            ))
    return rows


def write_rows(db_path: str, rows: list[tuple], pulled_on: str) -> int:
    """Replace ``pulled_on``'s rows with ``rows``; return the table's total row count."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(TABLE_DDL)
        con.execute("DELETE FROM mandi_prices WHERE pulled_on = ?", [pulled_on])
        con.executemany("INSERT INTO mandi_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        return con.execute("SELECT COUNT(*) FROM mandi_prices").fetchone()[0]
    finally:
        con.close()


def collect(db_path: str, key: str | None = None, pulled_on: str | None = None) -> None:
    """Fetch today's feed and upsert it into ``db_path`` (idempotent per pull date)."""
    key = key or os.environ.get("DATA_GOV_IN_KEY", DEFAULT_KEY)
    pulled_on = pulled_on or date.today().isoformat()

    results, grand_total = fetch_all_states(key, REQUESTED_PAGE)
    rows = rows_from_results(results, pulled_on)
    if len(rows) != grand_total:
        log.warning("WARNING: collected %d rows but the API reported total=%d — today's "
                    "pull may be incomplete (throttled probes, or a state name missing "
                    "from STATES?)", len(rows), grand_total)

    total = write_rows(db_path, rows, pulled_on)
    log.info("mandi_prices: +%d rows today, %s total -> %s", len(rows), f"{total:,}", db_path)


def main(argv: list[str] | None = None) -> None:
    common.configure_cli_logging()
    args = common.db_arg_parser(__doc__.splitlines()[0]).parse_args(argv)
    collect(args.db)


if __name__ == "__main__":
    main()
