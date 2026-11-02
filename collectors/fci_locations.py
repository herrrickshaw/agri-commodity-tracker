#!/usr/bin/env python3
"""FCI (Food Corporation of India) depot / storage location directory.

fci.gov.in is an Angular app whose API POSTs an AES-128-CBC-encrypted,
HMAC-SHA256-signed body and returns an AES-encrypted response. This collector
replicates that envelope (keys are embedded client-side in the public JS
bundle — not secrets) to pull the depot master directly, no browser needed.

Envelope (from main.*.js data_encryptV2 / data_decryptV2):
  f            = base64(JSON.stringify(payload))
  REQUEST_DATA = base64( AES-128-CBC(f) )
  REQUEST_TOKEN= HMAC_SHA256(f, apiHashingKey) hex
  response: AES-128-CBC-decrypt(RESPONSE_DATA) -> base64 -> JSON

Pipeline:  fetch_zones() + fetch_depots() -> depot_row() -> write_table()
(network, encrypted envelope)              (pure)         (DuckDB)

Output: fci_locations table in data/agri.duckdb (+ CSV snapshot via scripts/sync_data.sh).

Usage:  python3 collectors/fci_locations.py [--db data/agri.duckdb]
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import duckdb
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

if __package__ in (None, ""):  # run as a script: python3 collectors/fci_locations.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import common  # noqa: E402

log = logging.getLogger(__name__)

# Public client-side constants from fci.gov.in's JS bundle (not secrets).
API = "https://fci.gov.in/admin/"
AES_KEY = bytes.fromhex("0123456789abcdef0123456789abcdef")
AES_IV = b"1234567890abcdef"
HMAC_KEY = b"22CSMTOOL2022"
UA = {"User-Agent": common.BROWSER_UA + " AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
      "Content-Type": "application/json", "Origin": "https://fci.gov.in",
      "Referer": "https://fci.gov.in/"}
RETRIES = 3
BACKOFF_S = 2
HTTP_TIMEOUT_S = 40
DEPOT_PAGE_SIZE = 100000    # larger than the ~500-depot directory: one page

TABLE_DDL = """CREATE OR REPLACE TABLE fci_locations (
    depot_id BIGINT, depot_code TEXT, depot_name TEXT,
    region_code TEXT, region_name TEXT, zone_id BIGINT, zone_name TEXT,
    credential_details TEXT, additional_information TEXT,
    primary_link TEXT, n_links INTEGER, raw_json TEXT)"""


# ----------------------------------------------------------------- envelope --

def _enc(f_bytes: bytes) -> str:
    """base64(AES-128-CBC(f)) as the site's data_encryptV2 does."""
    return base64.b64encode(
        AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
            pad(f_bytes, AES.block_size))).decode()


def _dec(b64: str) -> Any:
    """Inverse of the response envelope: AES-decrypt -> base64-decode -> JSON."""
    pt = unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(
        base64.b64decode(b64)), AES.block_size)
    return json.loads(base64.b64decode(pt).decode("utf-8"))


def build_request_body(payload: Any) -> bytes:
    """Signed+encrypted JSON body for one API call (pure; deterministic)."""
    f = base64.b64encode(json.dumps(payload).encode()).decode().encode()
    return json.dumps({
        "REQUEST_DATA": _enc(f),
        "REQUEST_TOKEN": hmac.new(HMAC_KEY, f, hashlib.sha256).hexdigest(),
    }).encode()


def call(endpoint: str, payload: Any, retries: int = RETRIES) -> Any:
    """POST ``payload`` to ``API + endpoint`` and return the decrypted response."""
    body = build_request_body(payload)
    for i in range(retries):
        try:
            req = urllib.request.Request(API + endpoint, data=body, headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                return _dec(json.load(r)["RESPONSE_DATA"])
        except Exception:  # noqa: BLE001 — network/envelope errors; re-raised on last attempt
            if i == retries - 1:
                raise
            time.sleep(BACKOFF_S * (i + 1))
    raise RuntimeError("unreachable: retries must be >= 1")


# -------------------------------------------------------------------- fetch --

def fetch_zones() -> list[dict]:
    resp = call("api/get-zone-list", {})
    return (resp.get("original", {}).get("data", [])
            if isinstance(resp.get("original"), dict) else [])


def fetch_depots() -> list[dict]:
    """result.sec_1 carries the depot rows; noOfRec is the true total."""
    payload = [{"secSlNo": 1, "offSet": 0, "noOfRecords": DEPOT_PAGE_SIZE,
                "orderBy": [{"columnName": "intId", "type": "ASC"}],
                "condition": [], "viewParameters": ""}]
    resp = call("api/ManageDepotDetails/viewall", payload)
    return resp.get("result", {}).get("sec_1", [])


# ---------------------------------------------------------------- transform --

def depot_row(d: dict) -> tuple:
    """Map one raw depot record to a fci_locations tuple (column order = TABLE_DDL)."""
    # human-readable names live in a nested JSON string
    try:
        names = json.loads(d.get("jsonOptTxtDetails") or "{}")
    except (ValueError, TypeError):
        names = {}
    links = [link.get("linkName") for link in d.get("depotLink", [])
             if link.get("linkName")]
    return (
        d.get("id"),
        d.get("depot"),                        # depot code
        names.get("VCH_DEPOT_CODE"),           # depot name
        d.get("region"),                       # region code
        names.get("VCH_REGION_CODE"),          # region name
        d.get("zone"),                         # zone id
        names.get("INT_ZONE_ID"),              # zone name
        d.get("credentialDetails") or None,
        d.get("additionalInformation") or None,
        links[0] if links else None,
        len(links),
        json.dumps(d, ensure_ascii=False),
    )


# -------------------------------------------------------------------- write --

def write_table(db_path: str, rows: list[tuple]) -> tuple[int, int, int]:
    """Replace fci_locations with ``rows``; return (depots, distinct zones, distinct regions)."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(TABLE_DDL)
        con.executemany("INSERT INTO fci_locations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        n = con.execute("SELECT COUNT(*) FROM fci_locations").fetchone()[0]
        zc = con.execute("SELECT COUNT(DISTINCT zone_name), COUNT(DISTINCT region_name) "
                         "FROM fci_locations").fetchone()
        return n, zc[0], zc[1]
    finally:
        con.close()


def collect(db_path: str) -> None:
    """Rebuild the fci_locations table in ``db_path`` from the live FCI API."""
    zones = fetch_zones()
    zmap = {z.get("int_Demographic_Id"): z.get("vch_Demographic_Name") for z in zones}
    log.info("  zones: %d (%s…)", len(zones), ", ".join(v for v in zmap.values() if v)[:80])

    depots = fetch_depots()
    log.info("  depots: %d", len(depots))

    rows = [depot_row(d) for d in depots]
    n, n_zones, n_regions = write_table(db_path, rows)
    log.info("fci_locations: %d depots across %d zones / %d regions -> %s",
             n, n_zones, n_regions, db_path)


def main(argv: list[str] | None = None) -> None:
    common.configure_cli_logging()
    args = common.db_arg_parser(__doc__.splitlines()[0]).parse_args(argv)
    collect(args.db)


if __name__ == "__main__":
    main()
