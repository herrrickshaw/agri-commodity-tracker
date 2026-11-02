"""Tests for collectors/worldbank_agri.py — pure parsing plus a mocked end-to-end run."""
import duckdb

from collectors import worldbank_agri as wb


def _page(pages, obs):
    return [{"pages": pages, "lastupdated": "2026-01-01"}, obs]


def test_indicator_url_contains_country_code_and_page():
    u = wb.indicator_url("IND", "AG.YLD.CREL.KG", 2)
    assert u.startswith(wb.API_BASE + "/country/IND/indicator/AG.YLD.CREL.KG?")
    assert "format=json" in u and f"per_page={wb.PER_PAGE}" in u and "page=2" in u


def test_parse_page_skips_null_values_and_casts_types():
    rows, pages = wb.parse_page(
        _page(3, [{"date": "2020", "value": "1.5"}, {"date": "2019", "value": None}]),
        "IND", "C", "name")
    assert rows == [("IND", "C", "name", 2020, 1.5, "2026-01-01")]
    assert pages == 3


def test_parse_page_handles_error_envelopes():
    assert wb.parse_page(None, "IND", "C", "n") == ([], 0)
    assert wb.parse_page([{"message": "bad"}], "IND", "C", "n") == ([], 0)
    assert wb.parse_page([{"pages": 1}, None], "IND", "C", "n") == ([], 0)


def test_fetch_indicator_walks_all_pages(monkeypatch):
    calls = []

    def fake_get_json(url, retries=3):
        calls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        return _page(2, [{"date": str(2000 + page), "value": page}])

    monkeypatch.setattr(wb, "get_json", fake_get_json)
    rows = list(wb.fetch_indicator("IND", "C", "n"))
    assert [r[3] for r in rows] == [2001, 2002]
    assert len(calls) == 2


def test_collect_rebuilds_table(monkeypatch, tmp_path):
    monkeypatch.setattr(wb, "get_json",
                        lambda url, retries=3: _page(1, [{"date": "2021", "value": 7}]))
    monkeypatch.setattr(wb.time, "sleep", lambda *_: None)
    db = tmp_path / "agri.duckdb"
    wb.collect(str(db))
    wb.collect(str(db))                       # CREATE OR REPLACE: no accumulation

    con = duckdb.connect(str(db))
    n = con.execute("SELECT COUNT(*) FROM worldbank_agri").fetchone()[0]
    row = con.execute("SELECT country, year, value FROM worldbank_agri LIMIT 1").fetchone()
    con.close()
    assert n == len(wb.COUNTRIES) * len(wb.INDICATORS)
    assert row[1] == 2021 and row[2] == 7.0
