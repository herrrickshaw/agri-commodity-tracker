"""Tests for collectors/fci_locations.py — envelope crypto, row mapping, mocked collect."""
import base64
import hashlib
import hmac
import json

import duckdb

from collectors import fci_locations as fci


def test_envelope_roundtrip():
    payload = {"a": 1, "b": [1, 2]}
    f = base64.b64encode(json.dumps(payload).encode())
    assert fci._dec(fci._enc(f)) == payload


def test_build_request_body_is_signed_and_decryptable():
    payload = [{"secSlNo": 1}]
    body = json.loads(fci.build_request_body(payload))
    assert set(body) == {"REQUEST_DATA", "REQUEST_TOKEN"}
    assert fci._dec(body["REQUEST_DATA"]) == payload
    f = base64.b64encode(json.dumps(payload).encode())
    assert body["REQUEST_TOKEN"] == hmac.new(fci.HMAC_KEY, f, hashlib.sha256).hexdigest()


def _depot(**over):
    d = {"id": 7, "depot": "D01", "region": "R1", "zone": 3,
         "jsonOptTxtDetails": json.dumps({"VCH_DEPOT_CODE": "Depot One",
                                          "VCH_REGION_CODE": "Region One",
                                          "INT_ZONE_ID": "North"}),
         "depotLink": [{"linkName": "http://a"}, {"linkName": ""}, {"linkName": "http://b"}],
         "credentialDetails": "", "additionalInformation": "info"}
    d.update(over)
    return d


def test_depot_row_maps_names_links_and_blanks():
    row = fci.depot_row(_depot())
    assert row[:7] == (7, "D01", "Depot One", "R1", "Region One", 3, "North")
    assert row[7] is None and row[8] == "info"       # "" -> None
    assert row[9] == "http://a" and row[10] == 2     # first non-empty link, count
    assert json.loads(row[11])["id"] == 7            # raw_json round-trips


def test_depot_row_tolerates_bad_nested_json():
    row = fci.depot_row(_depot(jsonOptTxtDetails="{not json", depotLink=[]))
    assert row[2] is None and row[9] is None and row[10] == 0


def test_collect_writes_table(monkeypatch, tmp_path):
    def fake_call(endpoint, payload, retries=3):
        if endpoint.endswith("get-zone-list"):
            return {"original": {"data": [{"int_Demographic_Id": 3,
                                           "vch_Demographic_Name": "North"}]}}
        return {"result": {"sec_1": [_depot(), _depot(id=8, depot="D02")]}}

    monkeypatch.setattr(fci, "call", fake_call)
    db = tmp_path / "agri.duckdb"
    fci.collect(str(db))
    con = duckdb.connect(str(db))
    n, zones = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT zone_name) FROM fci_locations").fetchone()
    con.close()
    assert (n, zones) == (2, 1)
