"""Offline tests for collectors/fci_locations.py (AES/HMAC envelope + depot table build)."""
import base64
import hashlib
import hmac
import io
import json

import duckdb
import pytest

from collectors import fci_locations as fl


class _Resp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._buf

    def __exit__(self, *a):
        return False


def _server_reply(obj):
    """What the FCI API returns: AES(base64(json))."""
    f = base64.b64encode(json.dumps(obj).encode())
    return {"RESPONSE_DATA": fl._enc(f)}


# ------------------------------- crypto envelope -------------------------------

def test_enc_dec_roundtrip():
    obj = {"a": [1, 2, {"b": "x"}], "unicode": "café"}
    f = base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    assert fl._dec(fl._enc(f)) == obj


def test_enc_is_base64_string_and_block_aligned():
    out = fl._enc(b"hello")
    assert isinstance(out, str)
    assert len(base64.b64decode(out)) % 16 == 0


def test_enc_pads_exact_block_with_extra_block():
    # PKCS7: a 16-byte plaintext gains a full padding block.
    assert len(base64.b64decode(fl._enc(b"x" * 16))) == 32
    assert len(base64.b64decode(fl._enc(b"x" * 15))) == 16


def test_dec_rejects_garbage():
    with pytest.raises(Exception):
        fl._dec(base64.b64encode(b"not-a-valid-ciphertext!!").decode())


# ------------------------------- call() -------------------------------

def test_call_builds_signed_encrypted_request(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = json.loads(req.data)
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["timeout"] = timeout
        return _Resp(_server_reply({"ok": True}))

    monkeypatch.setattr(fl.urllib.request, "urlopen", fake_urlopen)
    payload = {"x": 1}
    assert fl.call("api/thing", payload) == {"ok": True}

    assert seen["url"] == "https://fci.gov.in/admin/api/thing"
    assert seen["method"] == "POST"
    assert seen["timeout"] == 40
    assert seen["headers"]["origin"] == "https://fci.gov.in"
    assert seen["headers"]["content-type"] == "application/json"
    # the body decrypts back to base64(json(payload)) and is signed over exactly that
    f = base64.b64encode(json.dumps(payload).encode())
    cipher = base64.b64decode(seen["body"]["REQUEST_DATA"])
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    plain = unpad(AES.new(fl.AES_KEY, AES.MODE_CBC, fl.AES_IV).decrypt(cipher), 16)
    assert plain == f
    assert seen["body"]["REQUEST_TOKEN"] == hmac.new(
        fl.HMAC_KEY, f, hashlib.sha256).hexdigest()


def test_call_retries_with_backoff_then_succeeds(monkeypatch):
    seq = [OSError("boom"), OSError("boom2"), _Resp(_server_reply({"n": 3}))]
    sleeps = []

    def flaky(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(fl.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(fl.time, "sleep", sleeps.append)
    assert fl.call("e", {}) == {"n": 3}
    assert sleeps == [2, 4]            # 2*(i+1) backoff, no sleep after success


def test_call_raises_last_error_after_retries(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def boom(req, timeout=None):
        calls["n"] += 1
        raise OSError(f"fail{calls['n']}")

    monkeypatch.setattr(fl.urllib.request, "urlopen", boom)
    monkeypatch.setattr(fl.time, "sleep", sleeps.append)
    with pytest.raises(OSError, match="fail3"):
        fl.call("e", {}, retries=3)
    assert calls["n"] == 3
    assert sleeps == [2, 4]            # no sleep after the final failed attempt


def test_call_single_retry_raises_immediately(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fl.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(fl.time, "sleep", sleeps.append)
    with pytest.raises(OSError):
        fl.call("e", {}, retries=1)
    assert sleeps == []


# ------------------------------- fetch_zones / fetch_depots -------------------------------

def test_fetch_zones_extracts_nested_data(monkeypatch):
    monkeypatch.setattr(fl, "call", lambda ep, p, **k: {
        "original": {"data": [{"int_Demographic_Id": 1}]}})
    assert fl.fetch_zones() == [{"int_Demographic_Id": 1}]


@pytest.mark.parametrize("resp", [{}, {"original": "str"}, {"original": None},
                                  {"original": {}}, {"original": []}])
def test_fetch_zones_defaults_to_empty(monkeypatch, resp):
    monkeypatch.setattr(fl, "call", lambda ep, p, **k: resp)
    assert fl.fetch_zones() == []


def test_fetch_zones_hits_zone_endpoint(monkeypatch):
    seen = []
    monkeypatch.setattr(fl, "call", lambda ep, p, **k: seen.append((ep, p)) or {})
    fl.fetch_zones()
    assert seen == [("api/get-zone-list", {})]


def test_fetch_depots_returns_sec_1_and_sends_paging_payload(monkeypatch):
    seen = {}

    def fake(ep, p, **k):
        seen["ep"], seen["p"] = ep, p
        return {"result": {"sec_1": [{"id": 1}, {"id": 2}]}}

    monkeypatch.setattr(fl, "call", fake)
    assert fl.fetch_depots() == [{"id": 1}, {"id": 2}]
    assert seen["ep"] == "api/ManageDepotDetails/viewall"
    assert seen["p"][0]["offSet"] == 0
    assert seen["p"][0]["noOfRecords"] == 100000
    assert seen["p"][0]["secSlNo"] == 1


@pytest.mark.parametrize("resp", [{}, {"result": {}}])
def test_fetch_depots_defaults_to_empty(monkeypatch, resp):
    monkeypatch.setattr(fl, "call", lambda ep, p, **k: resp)
    assert fl.fetch_depots() == []


# ------------------------------- collect() -------------------------------

def _depot(i, **over):
    d = {
        "id": i, "depot": f"D{i}", "region": f"R{i}", "zone": 10 + i,
        "credentialDetails": "cred", "additionalInformation": "info",
        "jsonOptTxtDetails": json.dumps({
            "VCH_DEPOT_CODE": f"Depot {i}", "VCH_REGION_CODE": f"Region {i}",
            "INT_ZONE_ID": f"Zone {i}"}),
        "depotLink": [{"linkName": "http://a"}, {"linkName": ""}, {"linkName": "http://b"}],
    }
    d.update(over)
    return d


@pytest.fixture
def fake_api(monkeypatch):
    state = {"zones": [{"int_Demographic_Id": 11, "vch_Demographic_Name": "North"}],
             "depots": [_depot(1), _depot(2)]}
    monkeypatch.setattr(fl, "fetch_zones", lambda: state["zones"])
    monkeypatch.setattr(fl, "fetch_depots", lambda: state["depots"])
    return state


def _rows(db):
    con = duckdb.connect(str(db))
    cols = [r[1] for r in con.execute("PRAGMA table_info('fci_locations')").fetchall()]
    rows = con.execute("SELECT * FROM fci_locations ORDER BY depot_id").fetchall()
    con.close()
    return cols, rows


def test_collect_maps_every_column(fake_api, tmp_path, capsys):
    db = tmp_path / "a.duckdb"
    fl.collect(db)
    cols, rows = _rows(db)
    assert cols == ["depot_id", "depot_code", "depot_name", "region_code", "region_name",
                    "zone_id", "zone_name", "credential_details",
                    "additional_information", "primary_link", "n_links", "raw_json"]
    r = dict(zip(cols, rows[0]))
    assert (r["depot_id"], r["depot_code"], r["depot_name"]) == (1, "D1", "Depot 1")
    assert (r["region_code"], r["region_name"]) == ("R1", "Region 1")
    assert (r["zone_id"], r["zone_name"]) == (11, "Zone 1")
    assert (r["credential_details"], r["additional_information"]) == ("cred", "info")
    assert r["primary_link"] == "http://a"
    assert r["n_links"] == 2            # empty linkName is not counted
    assert json.loads(r["raw_json"])["id"] == 1
    assert len(rows) == 2
    out = capsys.readouterr().out
    assert "zones: 1 (North" in out
    assert "depots: 2" in out
    assert "fci_locations: 2 depots across 2 zones / 2 regions" in out


def test_collect_tolerates_bad_or_missing_name_json(fake_api, tmp_path):
    fake_api["depots"] = [
        _depot(1, jsonOptTxtDetails="{not json"),
        _depot(2, jsonOptTxtDetails=None),
        _depot(3, jsonOptTxtDetails=12345),     # TypeError path in json.loads
    ]
    db = tmp_path / "a.duckdb"
    fl.collect(db)
    cols, rows = _rows(db)
    assert len(rows) == 3
    for row in rows:
        r = dict(zip(cols, row))
        assert r["depot_name"] is None and r["region_name"] is None and r["zone_name"] is None
        assert r["depot_code"].startswith("D")   # raw codes still mapped


def test_collect_no_links_and_blank_text_become_none(fake_api, tmp_path):
    fake_api["depots"] = [_depot(1, depotLink=[], credentialDetails="",
                                 additionalInformation=None)]
    db = tmp_path / "a.duckdb"
    fl.collect(db)
    cols, rows = _rows(db)
    r = dict(zip(cols, rows[0]))
    assert r["primary_link"] is None and r["n_links"] == 0
    assert r["credential_details"] is None
    assert r["additional_information"] is None


def test_collect_missing_depot_link_key(fake_api, tmp_path):
    d = _depot(1)
    del d["depotLink"]
    fake_api["depots"] = [d]
    db = tmp_path / "a.duckdb"
    fl.collect(db)
    cols, rows = _rows(db)
    assert dict(zip(cols, rows[0]))["n_links"] == 0


def test_collect_zone_summary_skips_none_names_and_truncates(fake_api, tmp_path, capsys):
    fake_api["zones"] = [{"int_Demographic_Id": i, "vch_Demographic_Name": ("Z" * 30 if i else None)}
                         for i in range(6)]
    fl.collect(tmp_path / "a.duckdb")
    line = [l for l in capsys.readouterr().out.splitlines() if "zones:" in l][0]
    assert "zones: 6" in line
    assert "None" not in line
    inner = line.split("(", 1)[1].rsplit("…", 1)[0]
    assert len(inner) == 80


def test_collect_replaces_table_on_rerun(fake_api, tmp_path):
    db = tmp_path / "a.duckdb"
    fl.collect(db)
    fake_api["depots"] = [_depot(9)]
    fl.collect(db)
    _, rows = _rows(db)
    assert [r[0] for r in rows] == [9]


@pytest.mark.xfail(strict=True, raises=duckdb.InvalidInputException,
                   reason="collect() calls executemany([]) when the API returns no depots; "
                          "DuckDB >= 1.x rejects an empty parameter list (fci_locations.py:~112)")
def test_collect_empty_depots(fake_api, tmp_path, capsys):
    fake_api["depots"] = []
    db = tmp_path / "a.duckdb"
    fl.collect(db)
    _, rows = _rows(db)
    assert rows == []
    assert "0 depots across 0 zones / 0 regions" in capsys.readouterr().out
