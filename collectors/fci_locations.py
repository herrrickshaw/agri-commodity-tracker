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

Output: fci_locations table in data/agri.duckdb (+ CSV snapshot on demand).

Usage:  python3 collectors/fci_locations.py [--db data/agri.duckdb]
"""

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.request
from pathlib import Path

import duckdb
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# Public client-side constants from fci.gov.in's JS bundle (not secrets).
API = "https://fci.gov.in/admin/"
AES_KEY = bytes.fromhex("0123456789abcdef0123456789abcdef")
AES_IV = b"1234567890abcdef"
HMAC_KEY = b"22CSMTOOL2022"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
      "Content-Type": "application/json", "Origin": "https://fci.gov.in",
      "Referer": "https://fci.gov.in/"}


def _enc(f_bytes):
    return base64.b64encode(
        AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
            pad(f_bytes, AES.block_size))).decode()


def _dec(b64):
    pt = unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(
        base64.b64decode(b64)), AES.block_size)
    return json.loads(base64.b64decode(pt).decode("utf-8"))


def call(endpoint, payload, retries=3):
    f = base64.b64encode(json.dumps(payload).encode()).decode().encode()
    body = json.dumps({
        "REQUEST_DATA": _enc(f),
        "REQUEST_TOKEN": hmac.new(HMAC_KEY, f, hashlib.sha256).hexdigest(),
    }).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(API + endpoint, data=body,
                                         headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=40) as r:
                return _dec(json.load(r)["RESPONSE_DATA"])
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))


def fetch_zones():
    resp = call("api/get-zone-list", {})
    return (resp.get("original", {}).get("data", [])
            if isinstance(resp.get("original"), dict) else [])


def fetch_depots():
    """result.sec_1 carries the depot rows; noOfRec is the true total."""
    payload = [{"secSlNo": 1, "offSet": 0, "noOfRecords": 100000,
                "orderBy": [{"columnName": "intId", "type": "ASC"}],
                "condition": [], "viewParameters": ""}]
    resp = call("api/ManageDepotDetails/viewall", payload)
    return resp.get("result", {}).get("sec_1", [])


def collect(db_path):
    zones = fetch_zones()
    zmap = {z.get("int_Demographic_Id"): z.get("vch_Demographic_Name")
            for z in zones}
    print(f"  zones: {len(zones)} ({', '.join(v for v in zmap.values() if v)[:80]}…)")

    depots = fetch_depots()
    print(f"  depots: {len(depots)}")

    rows = []
    for d in depots:
        # human-readable names live in a nested JSON string
        try:
            names = json.loads(d.get("jsonOptTxtDetails") or "{}")
        except (ValueError, TypeError):
            names = {}
        links = [l.get("linkName") for l in d.get("depotLink", [])
                 if l.get("linkName")]
        rows.append((
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
        ))

    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE fci_locations (
        depot_id BIGINT, depot_code TEXT, depot_name TEXT,
        region_code TEXT, region_name TEXT, zone_id BIGINT, zone_name TEXT,
        credential_details TEXT, additional_information TEXT,
        primary_link TEXT, n_links INTEGER, raw_json TEXT)""")
    con.executemany(
        "INSERT INTO fci_locations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    n = con.execute("SELECT COUNT(*) FROM fci_locations").fetchone()[0]
    zc = con.execute(
        "SELECT COUNT(DISTINCT zone_name), COUNT(DISTINCT region_name) "
        "FROM fci_locations").fetchone()
    con.close()
    print(f"fci_locations: {n} depots across {zc[0]} zones / {zc[1]} regions -> {db_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "agri.duckdb"))
    args = ap.parse_args()
    collect(args.db)
