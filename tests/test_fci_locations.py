"""Tests for collectors/fci_locations.py — FCI's AES+HMAC request envelope and the
depot-row mapping. The network call is mocked; no request leaves the machine."""
import base64
import hashlib
import hmac
import io
import json

import duckdb
import pytest

pytest.importorskip("Crypto")
from collectors import fci_locations as fci  # noqa: E402


def test_enc_dec_roundtrip_matches_site_envelope():
    payload = {"a": 1, "name": "Depot ₹"}
    # server response = AES(base64(JSON)) ; _dec reverses it to the JSON
    inner = base64.b64encode(json.dumps(payload).encode())
    assert fci._dec(fci._enc(inner)) == payload


def test_enc_is_deterministic_fixed_iv():
    assert fci._enc(b"abc") == fci._enc(b"abc")
    assert len(base64.b64decode(fci._enc(b"abc"))) == 16   # one padded AES block


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_call_posts_encrypted_body_with_hmac_token(monkeypatch):
    captured = {}
    reply = {"result": {"sec_1": [{"id": 7}]}}

    def fake_urlopen(req, timeout):
        captured["req"] = req
        enc_reply = fci._enc(base64.b64encode(json.dumps(reply).encode()))
        return _Resp(json.dumps({"RESPONSE_DATA": enc_reply}).encode())

    monkeypatch.setattr(fci.urllib.request, "urlopen", fake_urlopen)
    out = fci.call("api/x", {"q": 1})
    assert out == reply
    req = captured["req"]
    assert req.get_method() == "POST" and req.full_url == fci.API + "api/x"
    body = json.loads(req.data)
    f = base64.b64encode(json.dumps({"q": 1}).encode())
    assert body["REQUEST_TOKEN"] == hmac.new(fci.HMAC_KEY, f, hashlib.sha256).hexdigest()
    assert body["REQUEST_DATA"] == fci._enc(f)


def test_call_retries_then_raises(monkeypatch):
    n = {"c": 0}

    def boom(req, timeout):
        n["c"] += 1
        raise OSError("down")

    monkeypatch.setattr(fci.urllib.request, "urlopen", boom)
    monkeypatch.setattr(fci.time, "sleep", lambda *_: None)
    with pytest.raises(OSError):
        fci.call("api/x", {}, retries=3)
    assert n["c"] == 3


def test_fetch_zones_tolerates_missing_original(monkeypatch):
    monkeypatch.setattr(fci, "call", lambda e, p: {"original": "oops"})
    assert fci.fetch_zones() == []


def test_collect_maps_depot_rows(monkeypatch, tmp_path):
    depots = [
        {"id": 1, "depot": "D01", "region": "R1", "zone": 3,
         "jsonOptTxtDetails": json.dumps({"VCH_DEPOT_CODE": "Synthetic Depot A",
                                          "VCH_REGION_CODE": "Region X",
                                          "INT_ZONE_ID": "Zone North"}),
         "depotLink": [{"linkName": "http://example.invalid/a"}, {"linkName": ""}]},
        {"id": 2, "depot": "D02", "region": "R1", "zone": 3,
         "jsonOptTxtDetails": "{not json", "depotLink": []},
    ]
    monkeypatch.setattr(fci, "fetch_zones", lambda: [{"int_Demographic_Id": 3,
                                                      "vch_Demographic_Name": "North"}])
    monkeypatch.setattr(fci, "fetch_depots", lambda: depots)
    db = tmp_path / "agri.duckdb"
    fci.collect(str(db))
    con = duckdb.connect(str(db))
    rows = con.execute("SELECT depot_id, depot_code, depot_name, region_name, zone_name, "
                       "primary_link, n_links FROM fci_locations ORDER BY depot_id").fetchall()
    con.close()
    assert rows[0] == (1, "D01", "Synthetic Depot A", "Region X", "Zone North",
                       "http://example.invalid/a", 1)
    assert rows[1] == (2, "D02", None, None, None, None, 0)


def test_empty_depot_response_does_not_wipe_existing_table(monkeypatch, tmp_path):
    """An empty/failed depot listing used to replace the table, then crash in
    executemany([]), leaving fci_locations empty."""
    monkeypatch.setattr(fci, "fetch_zones", lambda: [])
    monkeypatch.setattr(fci, "fetch_depots", lambda: [{"id": 1, "depot": "D01"}])
    db = tmp_path / "agri.duckdb"
    fci.collect(str(db))
    monkeypatch.setattr(fci, "fetch_depots", lambda: [])
    try:
        fci.collect(str(db))
    except Exception:
        pass
    con = duckdb.connect(str(db))
    n = con.execute("SELECT COUNT(*) FROM fci_locations").fetchone()[0]
    con.close()
    assert n == 1
