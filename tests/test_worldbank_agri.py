"""Tests for collectors/worldbank_agri.py with the World Bank API mocked."""
import duckdb

from collectors import worldbank_agri as wb


def _fake_api(monkeypatch, pages=2):
    calls = []

    def fake(url, retries=3):
        calls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        meta = {"pages": pages, "lastupdated": "2026-07-01"}
        obs = [{"date": str(2000 + page), "value": 1.5 * page},
               {"date": "1960", "value": None}]
        return [meta, obs]

    monkeypatch.setattr(wb, "get_json", fake)
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)
    return calls


def test_collect_follows_pages_and_skips_nulls(monkeypatch, tmp_path):
    calls = _fake_api(monkeypatch, pages=2)
    db = tmp_path / "agri.duckdb"
    wb.collect(str(db))
    n_series = len(wb.COUNTRIES) * len(wb.INDICATORS)
    assert len(calls) == 2 * n_series
    con = duckdb.connect(str(db))
    count, years = con.execute(
        "SELECT COUNT(*), list(DISTINCT year ORDER BY year) FROM worldbank_agri").fetchone()
    con.close()
    assert count == 2 * n_series          # the null 1960 observations are dropped
    assert years == [2001, 2002]


def test_collect_rebuilds_table(monkeypatch, tmp_path):
    _fake_api(monkeypatch, pages=1)
    db = tmp_path / "agri.duckdb"
    wb.collect(str(db))
    wb.collect(str(db))
    con = duckdb.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM worldbank_agri").fetchone()[0]
    con.close()
    assert count == len(wb.COUNTRIES) * len(wb.INDICATORS)


def test_outage_does_not_wipe_existing_panel(monkeypatch, tmp_path):
    """If the API returns only error payloads, collect() used to CREATE OR REPLACE
    the table and then crash in executemany([]) -- leaving the panel empty."""
    _fake_api(monkeypatch, pages=1)
    db = tmp_path / "agri.duckdb"
    wb.collect(str(db))
    monkeypatch.setattr(wb, "get_json", lambda url, retries=3: [{"message": "bad"}])
    try:
        wb.collect(str(db))
    except Exception:
        pass
    con = duckdb.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM worldbank_agri").fetchone()[0]
    con.close()
    assert count == len(wb.COUNTRIES) * len(wb.INDICATORS)
