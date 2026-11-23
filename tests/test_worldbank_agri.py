"""Offline tests for collectors/worldbank_agri.py."""
import io
import json
import urllib.error

import duckdb
import pytest

from collectors import worldbank_agri as wb


class _Resp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._buf

    def __exit__(self, *a):
        return False


# ------------------------------- get_json -------------------------------

def test_get_json_parses_and_sends_ua_and_timeout(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return _Resp([{"pages": 1}, []])

    monkeypatch.setattr(wb.urllib.request, "urlopen", fake)
    assert wb.get_json("http://x") == [{"pages": 1}, []]
    assert seen["ua"].startswith("Mozilla/5.0")
    assert seen["timeout"] == 30


def test_get_json_retries_with_backoff(monkeypatch):
    seq = [urllib.error.URLError("a"), urllib.error.URLError("b"), _Resp({"ok": 1})]
    sleeps = []

    def flaky(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(wb.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(wb.time, "sleep", sleeps.append)
    assert wb.get_json("http://x") == {"ok": 1}
    assert sleeps == [2, 4]


def test_get_json_exhausts_retries(monkeypatch):
    n = {"c": 0}
    sleeps = []

    def boom(req, timeout=None):
        n["c"] += 1
        raise urllib.error.URLError(f"e{n['c']}")

    monkeypatch.setattr(wb.urllib.request, "urlopen", boom)
    monkeypatch.setattr(wb.time, "sleep", sleeps.append)
    with pytest.raises(urllib.error.URLError, match="e3"):
        wb.get_json("http://x")
    assert n["c"] == 3 and sleeps == [2, 4]


def test_get_json_single_retry_no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(wb.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(wb.time, "sleep", sleeps.append)
    with pytest.raises(OSError):
        wb.get_json("http://x", retries=1)
    assert sleeps == []


# ------------------------------- constants -------------------------------

def test_indicator_and_country_tables_sane():
    assert "IND" in wb.COUNTRIES
    assert len(set(wb.INDICATORS.values())) == len(wb.INDICATORS)
    assert all(c.count(".") >= 2 for c in wb.INDICATORS)


# ------------------------------- collect -------------------------------

@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(wb, "COUNTRIES", ["IND", "CHN"])
    monkeypatch.setattr(wb, "INDICATORS", {"AAA": "alpha", "BBB": "beta"})
    sleeps = []
    monkeypatch.setattr(wb.time, "sleep", sleeps.append)
    return sleeps


def _read(db):
    con = duckdb.connect(str(db))
    rows = con.execute("SELECT * FROM worldbank_agri ORDER BY country, indicator_code, year").fetchall()
    cols = [r[1] for r in con.execute("PRAGMA table_info('worldbank_agri')").fetchall()]
    con.close()
    return cols, rows


def test_collect_builds_rows_and_skips_null_values(small, monkeypatch, tmp_path, capsys):
    def fake(url):
        assert "per_page=1000" in url and "format=json" in url
        return [{"pages": 1, "lastupdated": "2026-01-01"},
                [{"date": "2020", "value": 1.5}, {"date": "2021", "value": None},
                 {"date": "2022", "value": 0}]]

    monkeypatch.setattr(wb, "get_json", fake)
    db = tmp_path / "w.duckdb"
    wb.collect(db)
    cols, rows = _read(db)
    assert cols == ["country", "indicator_code", "indicator", "year", "value",
                    "source_last_updated"]
    # 2 countries x 2 indicators x (2020, 2022); 2021 None skipped; value 0 kept
    assert len(rows) == 8
    assert ("IND", "AAA", "alpha", 2020, 1.5, "2026-01-01") in rows
    assert ("CHN", "BBB", "beta", 2022, 0.0, "2026-01-01") in rows
    assert not any(r[3] == 2021 for r in rows)
    out = capsys.readouterr().out
    assert "IND: done (4 rows cumulative)" in out
    assert "CHN: done (8 rows cumulative)" in out
    assert "worldbank_agri: 8 rows" in out


def test_collect_paginates_until_last_page(small, monkeypatch, tmp_path):
    urls = []

    def fake(url):
        urls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        return [{"pages": 3, "lastupdated": "x"},
                [{"date": str(2000 + page), "value": float(page)}]]

    monkeypatch.setattr(wb, "COUNTRIES", ["IND"])
    monkeypatch.setattr(wb, "INDICATORS", {"AAA": "alpha"})
    monkeypatch.setattr(wb, "get_json", fake)
    db = tmp_path / "w.duckdb"
    wb.collect(db)
    _, rows = _read(db)
    assert [r[3] for r in rows] == [2001, 2002, 2003]
    assert len(urls) == 3
    assert urls[0].endswith("page=1") and urls[2].endswith("page=3")
    assert "country/IND/indicator/AAA" in urls[0]


def test_collect_stops_on_missing_pages_key(small, monkeypatch, tmp_path):
    calls = []

    def fake(url):
        calls.append(url)
        return [{"lastupdated": "x"}, [{"date": "2000", "value": 1}]]   # no "pages"

    monkeypatch.setattr(wb, "COUNTRIES", ["IND"])
    monkeypatch.setattr(wb, "INDICATORS", {"AAA": "alpha"})
    monkeypatch.setattr(wb, "get_json", fake)
    wb.collect(tmp_path / "w.duckdb")
    assert len(calls) == 1


SHORT = [None, [], [{"pages": 1}], [{"pages": 1}, None], [{"pages": 1}, []]]


@pytest.mark.parametrize("short", SHORT)
def test_collect_skips_empty_or_short_responses(small, monkeypatch, tmp_path, short):
    """Only IND/AAA has data; every other call returns a short/empty response."""
    def fake(url):
        if "country/IND/indicator/AAA" in url:
            return [{"pages": 1, "lastupdated": "u"}, [{"date": "2001", "value": 4}]]
        return short

    monkeypatch.setattr(wb, "get_json", fake)
    db = tmp_path / "w.duckdb"
    wb.collect(db)
    _, rows = _read(db)
    assert rows == [("IND", "AAA", "alpha", 2001, 4.0, "u")]


@pytest.mark.parametrize("short", SHORT)
@pytest.mark.xfail(strict=True, raises=duckdb.InvalidInputException,
                   reason="collect() calls executemany([]) when no observations come back; "
                          "DuckDB >= 1.x rejects an empty parameter list (worldbank_agri.py:~74)")
def test_collect_with_no_observations_writes_empty_table(small, monkeypatch, tmp_path, short):
    monkeypatch.setattr(wb, "get_json", lambda url: short)
    db = tmp_path / "w.duckdb"
    wb.collect(db)
    _, rows = _read(db)
    assert rows == []


def test_collect_sleeps_between_indicators(small, monkeypatch, tmp_path):
    monkeypatch.setattr(wb, "get_json", lambda url: [{"pages": 1, "lastupdated": "u"},
                                                     [{"date": "2001", "value": 4}]])
    wb.collect(tmp_path / "w.duckdb")
    assert small == [0.3] * 4          # 2 countries x 2 indicators


def test_collect_replaces_table(small, monkeypatch, tmp_path):
    db = tmp_path / "w.duckdb"
    monkeypatch.setattr(wb, "get_json", lambda url: [{"pages": 1, "lastupdated": "u"},
                                                     [{"date": "1999", "value": 2}]])
    wb.collect(db)
    wb.collect(db)
    _, rows = _read(db)
    assert len(rows) == 4              # rebuilt, not appended


def test_collect_year_is_int_and_value_float(small, monkeypatch, tmp_path):
    monkeypatch.setattr(wb, "get_json", lambda url: [{"pages": 1, "lastupdated": "u"},
                                                     [{"date": "1999", "value": "2.5"}]])
    db = tmp_path / "w.duckdb"
    wb.collect(db)
    _, rows = _read(db)
    assert rows[0][3] == 1999 and rows[0][4] == 2.5
