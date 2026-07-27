"""Tests for collectors/mandi_prices.py — the daily Agmarknet mandi-price collector.

Covers the pure URL builder, the retry/error handling in get_json (network mocked),
and an end-to-end collect() run with the network mocked and a temporary DuckDB —
verifying schema creation, row mapping, the grand-total reconciliation, and that a
same-day re-run is idempotent (replaces the day's rows, never doubles them).
"""
import io
import json
import urllib.error

import duckdb
import pytest

from collectors import mandi_prices as mp


# --------------------------- _url() : pure API contract ---------------------------

def test_url_has_core_query_params():
    u = mp._url("KEY123", limit=500, offset=40)
    assert f"resource/{mp.RESOURCE}" in u
    assert "api-key=KEY123" in u
    assert "format=json" in u
    assert "limit=500" in u
    assert "offset=40" in u


def test_url_without_state_has_no_filter():
    assert "filters" not in mp._url("K", 10, 0)
    assert "filters" not in mp._url("K", 10, 0, state=None)


def test_url_with_state_adds_encoded_filter():
    u = mp._url("K", 10, 0, state="Tamil Nadu")
    assert "filters%5Bstate.keyword%5D=" in u   # filters[state.keyword]
    assert "Tamil%20Nadu" in u                  # space is percent-encoded


def test_states_list_is_nonempty_strings():
    assert mp.STATES and all(isinstance(s, str) and s for s in mp.STATES)


# --------------------------- get_json() : fetch + retry ---------------------------

class _Resp:
    """Minimal context-manager wrapping JSON bytes, like urlopen's return."""
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())
    def __enter__(self):
        return self._buf
    def __exit__(self, *a):
        return False


def test_get_json_returns_parsed_payload(monkeypatch):
    monkeypatch.setattr(mp.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"total": 3, "records": [1, 2, 3]}))
    assert mp.get_json("http://x")["total"] == 3


def test_get_json_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise urllib.error.HTTPError("http://x", 500, "err", {}, None)

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)  # no real waiting
    with pytest.raises(urllib.error.HTTPError):
        mp.get_json("http://x", retries=3)
    assert calls["n"] == 3   # exhausted all retries before raising


def test_get_json_recovers_after_transient_error(monkeypatch):
    seq = [urllib.error.HTTPError("u", 429, "e", {}, None),
           _Resp({"total": 1, "records": [{"ok": 1}]})]

    def flaky(*a, **k):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mp.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)
    assert mp.get_json("http://x", retries=5)["records"] == [{"ok": 1}]


# --------------------------- collect() : end-to-end (mocked net + temp DB) --------

def _fake_record():
    return {
        "state": "Teststate", "district": "Td", "market": "Tm",
        "commodity": "Wheat", "variety": "V", "grade": "G",
        "arrival_date": "01/01/2026",
        "min_price": "100", "max_price": "200", "modal_price": "150",
    }


@pytest.fixture
def mocked_api(monkeypatch):
    """One record per state; probe reports grand_total == number of states."""
    n_states = len(mp.STATES)

    def fake_get_json(url, retries=10):
        if "filters" in url:                 # per-state partition: 1 record
            return {"total": 1, "records": [_fake_record()]}
        return {"total": n_states, "records": [_fake_record()]}  # probe

    monkeypatch.setattr(mp, "get_json", fake_get_json)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)
    return n_states


def test_collect_creates_table_and_rows(mocked_api, tmp_path):
    db = tmp_path / "agri.duckdb"
    mp.collect(str(db))

    con = duckdb.connect(str(db))
    cols = [r[1] for r in con.execute("PRAGMA table_info('mandi_prices')").fetchall()]
    assert {"state", "modal_price", "pulled_on"} <= set(cols)
    count = con.execute("SELECT COUNT(*) FROM mandi_prices").fetchone()[0]
    con.close()
    assert count == mocked_api            # one row per state


def test_collect_is_idempotent_same_day(mocked_api, tmp_path):
    db = tmp_path / "agri.duckdb"
    mp.collect(str(db))
    mp.collect(str(db))                    # second same-day run must replace, not add

    con = duckdb.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM mandi_prices").fetchone()[0]
    con.close()
    assert count == mocked_api            # still one row per state, not doubled


def test_collect_maps_prices_as_numeric(mocked_api, tmp_path):
    db = tmp_path / "agri.duckdb"
    mp.collect(str(db))
    con = duckdb.connect(str(db))
    row = con.execute(
        "SELECT modal_price, commodity FROM mandi_prices LIMIT 1").fetchone()
    con.close()
    assert row[0] == 150.0 and row[1] == "Wheat"   # stored as DOUBLE
