"""Additional offline tests for collectors/mandi_prices.py: retry classes,
_fetch_state pagination edge cases, and collect() reconciliation paths."""
import io
import json
import urllib.error

import duckdb
import pytest

from collectors import mandi_prices as mp


class _Resp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._buf

    def __exit__(self, *a):
        return False


@pytest.fixture
def sleeps(monkeypatch):
    rec = []
    monkeypatch.setattr(mp.time, "sleep", rec.append)
    return rec


def rec(i=0, **over):
    r = {"state": "S", "district": "D", "market": f"M{i}", "commodity": "Wheat",
         "variety": "V", "grade": "G", "arrival_date": "01/01/2026",
         "min_price": "1", "max_price": "3", "modal_price": "2"}
    r.update(over)
    return r


# ------------------------------- get_json backoff -------------------------------

def test_get_json_429_waits_60s_other_http_backs_off(monkeypatch, sleeps):
    seq = [urllib.error.HTTPError("u", 429, "e", {}, None),
           urllib.error.HTTPError("u", 500, "e", {}, None),
           urllib.error.HTTPError("u", 503, "e", {}, None),
           _Resp({"ok": 1})]

    def f(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mp.urllib.request, "urlopen", f)
    assert mp.get_json("http://x") == {"ok": 1}
    assert sleeps == [60, 6, 9]       # 429 -> 60; others 3*(i+1) where i is attempt index


def test_get_json_generic_error_backoff_and_final_raise(monkeypatch, sleeps):
    n = {"c": 0}

    def boom(req, timeout=None):
        n["c"] += 1
        raise ConnectionError(f"c{n['c']}")

    monkeypatch.setattr(mp.urllib.request, "urlopen", boom)
    with pytest.raises(ConnectionError, match="c4"):
        mp.get_json("http://x", retries=4)
    assert sleeps == [3, 6, 9]


def test_get_json_http_error_last_attempt_raises_without_sleep(monkeypatch, sleeps):
    monkeypatch.setattr(mp.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.HTTPError("u", 429, "e", {}, None)))
    with pytest.raises(urllib.error.HTTPError):
        mp.get_json("http://x", retries=1)
    assert sleeps == []


def test_get_json_sends_ua_and_timeout(monkeypatch):
    seen = {}

    def f(req, timeout=None):
        seen["ua"], seen["t"] = req.get_header("User-agent"), timeout
        return _Resp({})

    monkeypatch.setattr(mp.urllib.request, "urlopen", f)
    mp.get_json("http://x")
    assert seen["ua"].startswith("Mozilla") and seen["t"] == 30


# ------------------------------- _fetch_state -------------------------------

def _pager(total, page_size, state_rows=None):
    """Fake get_json serving `total` records page_size at a time."""
    calls = []

    def fake(url):
        calls.append(url)
        off = int(url.split("offset=")[1].split("&")[0])
        n = min(page_size, max(total - off, 0))
        return {"total": total, "records": [rec(off + k) for k in range(n)]}

    return fake, calls


def test_fetch_state_paginates_on_server_page_size(monkeypatch, sleeps):
    fake, calls = _pager(25, 10)
    monkeypatch.setattr(mp, "get_json", fake)
    recs, total = mp._fetch_state("K", "Assam", 1000)
    assert total == 25 and len(recs) == 25
    assert [c.split("offset=")[1].split("&")[0] for c in calls] == ["0", "10", "20"]
    assert sleeps == [0.3, 0.3]        # throttle between pages only, none after the last
    assert "Assam" in calls[0]


def test_fetch_state_zero_total_stops_immediately(monkeypatch, sleeps):
    monkeypatch.setattr(mp, "get_json", lambda url: {"total": 0, "records": []})
    assert mp._fetch_state("K", "Goa", 1000) == ([], 0)
    assert sleeps == []


def test_fetch_state_null_total_treated_as_zero(monkeypatch, sleeps):
    monkeypatch.setattr(mp, "get_json", lambda url: {"total": None, "records": []})
    assert mp._fetch_state("K", "Goa", 1000) == ([], 0)


def test_fetch_state_warns_when_total_exceeds_window(monkeypatch, sleeps, capsys):
    calls = []

    def fake(url):
        calls.append(url)
        return {"total": mp.WINDOW + 5, "records": [rec()] * 5000}

    monkeypatch.setattr(mp, "get_json", fake)
    recs, total = mp._fetch_state("K", "UP", 1000)
    out = capsys.readouterr().out
    assert "WARNING: UP has 10005 rows" in out and "incomplete" in out
    assert total == mp.WINDOW + 5
    assert len(recs) == mp.WINDOW      # reading stops at the window, not at total
    assert len(calls) == 2


def test_fetch_state_no_warning_at_exactly_window(monkeypatch, sleeps, capsys):
    monkeypatch.setattr(mp, "get_json", lambda url: {"total": mp.WINDOW, "records": []}
                        if False else {"total": mp.WINDOW, "records": [rec()] * mp.WINDOW})
    recs, _ = mp._fetch_state("K", "UP", 1000)
    assert "WARNING" not in capsys.readouterr().out
    assert len(recs) == mp.WINDOW


def test_fetch_state_empty_page_midway_waits_then_resumes(monkeypatch, sleeps):
    seq = [{"total": 4, "records": [rec(0), rec(1)]},
           {"total": 4, "records": []},                       # throttled 200
           {"total": 4, "records": [rec(2), rec(3)]}]
    monkeypatch.setattr(mp, "get_json", lambda url: seq.pop(0))
    recs, total = mp._fetch_state("K", "S", 1000)
    assert len(recs) == 4 and total == 4
    assert sleeps == [0.3, 60]


def test_fetch_state_gives_up_after_repeated_empty_pages(monkeypatch, sleeps, capsys):
    seq = [{"total": 4, "records": [rec(0), rec(1)]}] + [{"total": 4, "records": []}] * 10
    calls = {"n": 0}

    def fake(url):
        calls["n"] += 1
        return seq.pop(0)

    monkeypatch.setattr(mp, "get_json", fake)
    recs, total = mp._fetch_state("K", "S", 1000)
    out = capsys.readouterr().out
    assert len(recs) == 2 and total == 4
    assert "repeated empty pages at 2/4" in out and "giving up" in out
    assert sleeps.count(60) == 5       # 5 tolerated, 6th empty gives up
    assert calls["n"] == 1 + 6


def test_fetch_state_empty_counter_resets_after_good_page(monkeypatch, sleeps, capsys):
    seq = [{"total": 6, "records": [rec(0), rec(1)]}]
    seq += [{"total": 6, "records": []}] * 5
    seq += [{"total": 6, "records": [rec(2), rec(3)]}]
    seq += [{"total": 6, "records": []}] * 5
    seq += [{"total": 6, "records": [rec(4), rec(5)]}]
    monkeypatch.setattr(mp, "get_json", lambda url: seq.pop(0))
    recs, _ = mp._fetch_state("K", "S", 1000)
    assert len(recs) == 6
    assert "giving up" not in capsys.readouterr().out


# ------------------------------- collect -------------------------------

def _rows(db):
    con = duckdb.connect(str(db))
    rows = con.execute("SELECT * FROM mandi_prices ORDER BY market").fetchall()
    con.close()
    return rows


def test_collect_uses_env_key_and_requests_1000(monkeypatch, sleeps, tmp_path):
    urls = []

    def fake(url):
        urls.append(url)
        return {"total": 1, "records": [rec(0)]}

    monkeypatch.setenv("DATA_GOV_IN_KEY", "MYKEY")
    monkeypatch.setattr(mp, "get_json", fake)
    mp.collect(tmp_path / "a.duckdb")
    assert all("api-key=MYKEY" in u for u in urls)
    assert "limit=1000" in urls[0]


def test_collect_default_key_when_env_absent(monkeypatch, sleeps, tmp_path):
    urls = []
    monkeypatch.delenv("DATA_GOV_IN_KEY", raising=False)
    monkeypatch.setattr(mp, "get_json", lambda url: urls.append(url) or {"total": 1, "records": [rec(0)]})
    mp.collect(tmp_path / "a.duckdb")
    assert f"api-key={mp.DEFAULT_KEY}" in urls[0]


def test_collect_cap_note_mentions_env_var_only_for_demo_key(monkeypatch, sleeps, tmp_path, capsys):
    def fake(url):
        if "filters" in url:
            return {"total": 1, "records": [rec(0)]}
        return {"total": 95, "records": [rec(i) for i in range(10)]}   # server capped at 10

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    monkeypatch.delenv("DATA_GOV_IN_KEY", raising=False)
    mp.collect(tmp_path / "a.duckdb")
    out = capsys.readouterr().out
    assert "server caps pages at 10 rows" in out
    assert "~11 requests for 95 records" in out       # ceil(95/10) + len(STATES)
    assert "set DATA_GOV_IN_KEY" in out

    monkeypatch.setenv("DATA_GOV_IN_KEY", "PERSONAL")
    mp.collect(tmp_path / "b.duckdb")
    out2 = capsys.readouterr().out
    assert "server caps pages at 10 rows" in out2
    assert "set DATA_GOV_IN_KEY" not in out2


def test_collect_no_cap_note_when_page_not_capped(monkeypatch, sleeps, tmp_path, capsys):
    def fake(url):
        if "filters" in url:
            return {"total": 1, "records": [rec(0)]}
        return {"total": 5, "records": [rec(i) for i in range(5)]}  # 5 == min(1000, 5)

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    mp.collect(tmp_path / "a.duckdb")
    assert "server caps" not in capsys.readouterr().out


def test_collect_warns_on_total_mismatch_and_still_writes(monkeypatch, sleeps, tmp_path, capsys):
    def fake(url):
        if "filters" in url:
            return {"total": 1, "records": [rec(0)]}
        return {"total": 7, "records": [rec(0)]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    db = tmp_path / "a.duckdb"
    mp.collect(db)
    out = capsys.readouterr().out
    assert "WARNING: collected 1 rows but the API reported total=7" in out
    assert len(_rows(db)) == 1


def test_collect_no_warning_when_totals_match(monkeypatch, sleeps, tmp_path, capsys):
    def fake(url):
        if "filters" in url:
            return {"total": 1, "records": [rec(0)]}
        return {"total": 1, "records": [rec(0)]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    mp.collect(tmp_path / "a.duckdb")
    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "mandi_prices: +1 rows today, 1 total" in out
    assert 90 not in sleeps and "retrying" not in out   # sum == grand_total: no extra pass


def test_collect_retries_short_partitions_in_later_passes(monkeypatch, sleeps, tmp_path, capsys):
    """A state whose first pass is throttled to empty is re-pulled in pass 2."""
    attempts = {"B": 0}

    def fake(url):
        if "filters" not in url:
            return {"total": 2, "records": [rec(0)]}
        if "=A" in url:
            return {"total": 1, "records": [rec(1, market="MA")]}
        # state B: first full _fetch_state returns nothing (total 0), later returns a row
        attempts["B"] += 1
        if attempts["B"] == 1:
            return {"total": 0, "records": []}
        return {"total": 1, "records": [rec(2, market="MB")]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A", "B"])
    db = tmp_path / "a.duckdb"
    mp.collect(db)
    out = capsys.readouterr().out
    assert "pass 1: 1/2" in out and "retrying 1 states in 90s" in out
    assert 90 in sleeps
    assert [r[2] for r in _rows(db)] == ["MA", "MB"]


def test_collect_keeps_longer_result_across_passes(monkeypatch, sleeps, tmp_path):
    """results[state] is only replaced by a LONGER pull."""
    seen = {"n": 0}

    def fake(url):
        if "filters" not in url:
            return {"total": 5, "records": [rec(0)]}
        seen["n"] += 1
        if seen["n"] == 1:
            return {"total": 2, "records": [rec(0, market="M0"), rec(1, market="M1")]}
        return {"total": 2, "records": [rec(9, market="M9")]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    db = tmp_path / "a.duckdb"
    mp.collect(db)
    assert sorted(r[2] for r in _rows(db)) == ["M0", "M1"]   # 3 passes, still the 2-row pull


def test_collect_stops_after_three_passes(monkeypatch, sleeps, tmp_path, capsys):
    def fake(url):
        if "filters" not in url:
            return {"total": 99, "records": [rec(0)]}
        return {"total": 1, "records": [rec(0)]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    mp.collect(tmp_path / "a.duckdb")
    out = capsys.readouterr().out
    assert "pass 1:" in out and "pass 2:" in out and "pass 3:" not in out
    assert sleeps.count(90) == 2


def test_collect_row_mapping_and_pulled_on(monkeypatch, sleeps, tmp_path):
    from datetime import date

    def fake(url):
        return {"total": 1, "records": [rec(0, min_price="5", max_price="9", modal_price="7")]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    db = tmp_path / "a.duckdb"
    mp.collect(db)
    (r,) = _rows(db)
    assert r == ("S", "D", "M0", "Wheat", "V", "G", "01/01/2026", 5.0, 9.0, 7.0,
                 date.today().isoformat())


def test_collect_preserves_other_days_rows(monkeypatch, sleeps, tmp_path):
    db = tmp_path / "a.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE mandi_prices (state TEXT, district TEXT, market TEXT,
        commodity TEXT, variety TEXT, grade TEXT, arrival_date TEXT,
        min_price DOUBLE, max_price DOUBLE, modal_price DOUBLE, pulled_on TEXT)""")
    con.execute("INSERT INTO mandi_prices VALUES ('S','D','OLD','W','V','G','x',1,2,3,'2000-01-01')")
    con.close()
    monkeypatch.setattr(mp, "get_json", lambda url: {"total": 1, "records": [rec(0)]})
    monkeypatch.setattr(mp, "STATES", ["A"])
    mp.collect(db)
    assert sorted(r[2] for r in _rows(db)) == ["M0", "OLD"]


@pytest.mark.xfail(strict=True, raises=duckdb.InvalidInputException,
                   reason="collect() calls executemany([]) when the day's feed is empty; "
                          "DuckDB >= 1.x rejects an empty parameter list, so an empty pull "
                          "crashes instead of writing 0 rows (mandi_prices.py:~175)")
def test_collect_empty_feed_writes_zero_rows_instead_of_crashing(monkeypatch, sleeps, tmp_path):
    monkeypatch.setattr(mp, "get_json", lambda url: {"total": 0, "records": []})
    monkeypatch.setattr(mp, "STATES", ["A"])
    db = tmp_path / "a.duckdb"
    mp.collect(db)
    assert _rows(db) == []


def test_collect_equal_length_repull_keeps_first_result(monkeypatch, sleeps, tmp_path):
    seen = {"n": 0}

    def fake(url):
        if "filters" not in url:
            return {"total": 5, "records": [rec(0)]}
        if "offset=0" not in url:                       # partition stays short: 1 of 2
            return {"total": 2, "records": []}
        seen["n"] += 1
        return {"total": 2, "records": [rec(0, market=f"PASS{seen['n']}")]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    db = tmp_path / "a.duckdb"
    mp.collect(db)
    assert [r[2] for r in _rows(db)] == ["PASS1"]     # later same-size pulls do not replace


def test_collect_warns_when_more_rows_than_reported_total(monkeypatch, sleeps, tmp_path, capsys):
    def fake(url):
        if "filters" in url:
            return {"total": 2, "records": [rec(0), rec(1)]}
        return {"total": 1, "records": [rec(0)]}

    monkeypatch.setattr(mp, "get_json", fake)
    monkeypatch.setattr(mp, "STATES", ["A"])
    mp.collect(tmp_path / "a.duckdb")
    assert "WARNING: collected 2 rows but the API reported total=1" in capsys.readouterr().out
