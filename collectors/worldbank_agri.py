#!/usr/bin/env python3
"""World Bank agriculture indicator panel (open API, no key).

Pulls the full annual history (1960-present) for a set of agri indicators
across comparator countries into DuckDB. Rebuilds the table on each run —
the API is the system of record.

Pipeline:  fetch_panel() -> write_table()
(network, yields rows)    (DuckDB, CREATE OR REPLACE)

Usage:  python3 collectors/worldbank_agri.py [--db data/agri.duckdb]
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb

if __package__ in (None, ""):  # run as a script: python3 collectors/worldbank_agri.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import common  # noqa: E402

log = logging.getLogger(__name__)

UA = common.UA
API_BASE = "https://api.worldbank.org/v2"
PER_PAGE = 1000
RETRIES = 3
BACKOFF_S = 2
INDICATOR_PAUSE_S = 0.3

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

TABLE_DDL = """CREATE OR REPLACE TABLE worldbank_agri (
    country TEXT, indicator_code TEXT, indicator TEXT,
    year INTEGER, value DOUBLE, source_last_updated TEXT)"""

# (country, indicator_code, indicator, year, value, source_last_updated)
Row = tuple[str, str, str, int, float, Any]


def get_json(url: str, retries: int = RETRIES) -> Any:
    """GET JSON with the World Bank retry policy (short linear backoff)."""
    return common.get_json(url, retries=retries, backoff_s=BACKOFF_S)


def indicator_url(iso: str, code: str, page: int) -> str:
    """One page of one indicator for one country."""
    return (f"{API_BASE}/country/{iso}/indicator/{code}"
            f"?format=json&per_page={PER_PAGE}&page={page}")


def parse_page(payload: Any, iso: str, code: str, name: str) -> tuple[list[Row], int]:
    """Rows with non-null values from one API page, plus the page count it reports.

    The World Bank envelope is ``[meta, observations]``; anything else (error
    objects, empty bodies) parses as no rows and 0 pages.
    """
    if not payload or len(payload) < 2 or not payload[1]:
        return [], 0
    meta, observations = payload[0], payload[1]
    rows = [(iso, code, name, int(obs["date"]), float(obs["value"]), meta.get("lastupdated"))
            for obs in observations if obs.get("value") is not None]
    return rows, meta.get("pages", 1)


def fetch_indicator(iso: str, code: str, name: str) -> Iterator[Row]:
    """All pages of one (country, indicator) series."""
    page = 1
    while True:
        rows, pages = parse_page(get_json(indicator_url(iso, code, page)), iso, code, name)
        if not rows and pages == 0:
            return
        yield from rows
        if page >= pages:
            return
        page += 1


def fetch_panel(countries: list[str] = COUNTRIES,
                indicators: dict[str, str] = INDICATORS) -> list[Row]:
    """Every indicator for every country; logs a progress line per country."""
    rows: list[Row] = []
    for iso in countries:
        for code, name in indicators.items():
            rows.extend(fetch_indicator(iso, code, name))
            time.sleep(INDICATOR_PAUSE_S)
        log.info("  %s: done (%d rows cumulative)", iso, len(rows))
    return rows


def write_table(db_path: str, rows: list[Row]) -> None:
    """Replace the worldbank_agri table with ``rows``."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(TABLE_DDL)
        con.executemany("INSERT INTO worldbank_agri VALUES (?,?,?,?,?,?)", rows)
    finally:
        con.close()


def collect(db_path: str) -> None:
    """Rebuild the worldbank_agri table in ``db_path`` from the live API."""
    rows = fetch_panel()
    write_table(db_path, rows)
    log.info("worldbank_agri: %s rows -> %s", f"{len(rows):,}", db_path)


def main(argv: list[str] | None = None) -> None:
    common.configure_cli_logging()
    args = common.db_arg_parser(__doc__.splitlines()[0]).parse_args(argv)
    collect(args.db)


if __name__ == "__main__":
    main()
