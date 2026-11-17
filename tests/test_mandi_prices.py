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


# --------------------------- _fetch_state() : pagination ---------------------------

def _paged_server(total, page_size, empties_at=None, n_empties=0):
    """Fake API serving `total` records in pages of `page_size` (ignoring the
    requested limit, like the demo key), optionally returning `n_empties`
    throttled HTTP-200 empty pages when offset == empties_at."""
    state = {"empties_left": n_empties, "calls": []}

    def fake(url, retries=10):
        q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        off = int(q["offset"])
        state["calls"].append(off)
        if empties_at is not None and off == empties_at and state["empties_left"]:
            state["empties_left"] -= 1
            return {"total": total, "records": []}
        n = max(0, min(page_size, total - off))
        return {"total": total, "records": [{"i": off + k} for k in range(n)]}
    return fake, state


def test_fetch_state_paginates_on_served_page_size_not_requested(monkeypatch):
    fake, st = _paged_server(total=25, page_size=10)
    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)
    recs, total = mp._fetch_state("k", "Goa", requested=1000)
    assert total == 25
    assert [r["i"] for r in recs] == list(range(25))
    assert st["calls"] == [0, 10, 20]


def test_fetch_state_zero_total_is_one_request(monkeypatch):
    fake, st = _paged_server(total=0, page_size=10)
    monkeypatch.setattr(mp, "get_json", fake)
    recs, total = mp._fetch_state("k", "Ladakh", 1000)
    assert (recs, total, st["calls"]) == ([], 0, [0])


def test_fetch_state_waits_out_throttled_empty_pages(monkeypatch):
    fake, st = _paged_server(total=30, page_size=10, empties_at=10, n_empties=2)
    sleeps = []
    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp.time, "sleep", sleeps.append)
    recs, _ = mp._fetch_state("k", "Bihar", 1000)
    assert len(recs) == 30
    assert sleeps.count(60) == 2


def test_fetch_state_gives_up_after_repeated_empties(monkeypatch, capsys):
    fake, _ = _paged_server(total=30, page_size=10, empties_at=10, n_empties=99)
    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)
    recs, total = mp._fetch_state("k", "Bihar", 1000)
    assert len(recs) == 10 and total == 30
    assert "giving up" in capsys.readouterr().out


def test_fetch_state_stops_at_elasticsearch_window(monkeypatch, capsys):
    fake, st = _paged_server(total=mp.WINDOW + 500, page_size=1000)
    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)
    recs, _ = mp._fetch_state("k", "Uttar Pradesh", 1000)
    assert len(recs) == mp.WINDOW
    assert max(st["calls"]) < mp.WINDOW
    assert "exceeds" in capsys.readouterr().out


def test_collect_repulls_a_state_whose_probe_was_throttled(monkeypatch, tmp_path):
    """First pass: Goa's partition comes back as a throttled empty 200 (total 0).
    The grand-total reconciliation must re-pull it on the next pass."""
    seen = {"goa": 0}

    def fake(url, retries=10):
        if "filters" not in url:
            return {"total": 2, "records": [_fake_record()]}
        if url.endswith("=Goa"):
            seen["goa"] += 1
            if seen["goa"] == 1:
                return {"total": 0, "records": []}
            return {"total": 1, "records": [dict(_fake_record(), state="Goa")]}
        if url.endswith("=Kerala"):
            return {"total": 1, "records": [dict(_fake_record(), state="Kerala")]}
        return {"total": 0, "records": []}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp.time, "sleep", lambda *_: None)
    db = tmp_path / "agri.duckdb"
    mp.collect(str(db))
    con = duckdb.connect(str(db))
    states = sorted(r[0] for r in con.execute("SELECT state FROM mandi_prices").fetchall())
    con.close()
    assert states == ["Goa", "Kerala"]
    assert seen["goa"] == 2


def test_empty_rerun_does_not_wipe_todays_rows(mocked_api, monkeypatch, tmp_path):
    """A same-day re-run that gets nothing (throttled / outage) used to DELETE
    today's rows and then crash in executemany([]), losing the earlier pull."""
    db = tmp_path / "agri.duckdb"
    mp.collect(str(db))
    monkeypatch.setattr(mp, "get_json", lambda url, retries=10: {"total": 0, "records": []})
    try:
        mp.collect(str(db))
    except Exception:
        pass
    con = duckdb.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM mandi_prices").fetchone()[0]
    con.close()
    assert count == mocked_api
