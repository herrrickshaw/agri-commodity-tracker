"""Tests for the FAOSTAT and NASA POWER collectors (network mocked, temp DuckDB)."""
import io
import json
import zipfile

import duckdb

from collectors import faostat_crops as fc
from collectors import faostat_prices as fp
from collectors import nasa_power as npw


def _zip(tmp_path, name, text):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(name, text)
    return p


PRICES_CSV = (
    "Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,"
    "Element,Year Code,Year,Months Code,Months,Unit,Value,Flag\n"
    '"100","\'356","India","15","\'0111","Wheat","5530","Producer Price (LCU/tonne)",'
    '"2020","2020","7021","Annual value","LCU","19000.5","A"\n'
    '"2","\'004","Afghanistan","221","\'01371","Almonds","5530","P","1993","1993",'
    '"7021","Annual value","LCU","46000","A"\n'
    '"100","\'356","India","15","\'0111","Wheat","5539","P","2021","2021","7021",'
    '"Annual value","","","A"\n'
)


def test_prices_parse_keeps_panel_countries_and_drops_blank_values(tmp_path):
    zp = _zip(tmp_path, "Prices_E_All_Data_(Normalized).csv", PRICES_CSV)
    rows = list(fp.parse(zp))
    assert len(rows) == 1
    assert rows[0][0] == "IND" and rows[0][3] == "Wheat" and rows[0][9] == 19000.5


def test_prices_collect_writes_table(tmp_path, monkeypatch):
    src = _zip(tmp_path, "Prices_E_All_Data_(Normalized).csv", PRICES_CSV)
    monkeypatch.setattr(fp, "download",
                        lambda url, dest: dest.write_bytes(src.read_bytes()))
    db = tmp_path / "t.duckdb"
    assert fp.collect(db) == 1
    con = duckdb.connect(str(db))
    assert con.execute("SELECT count(*) FROM faostat_producer_prices").fetchone()[0] == 1


CROPS_CSV = (
    "Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,"
    "Element,Year Code,Year,Unit,Value,Flag,Note\n"
    '"100","\'356","India","15","\'0111","Wheat","5312","Area harvested","2020",'
    '"2020","ha","31000000","A",\n'
    '"100","\'356","India","866","\'02111","Cattle","5320","Producing Animals/Slaughtered",'
    '"2020","2020","An","5","A",\n'
)


def test_crops_parse_filters_elements(tmp_path):
    zp = _zip(tmp_path, "Production_Crops_Livestock_E_All_Data_(Normalized).csv",
              CROPS_CSV)
    rows = list(fc.parse(zp))
    assert [(r[0], r[4], r[7]) for r in rows] == [("IND", "Area harvested", 31000000.0)]


def test_nasa_parse_skips_annual_and_fill_values():
    payload = {"properties": {"parameter": {
        "PRECTOTCORR": {"202001": 1.5, "202002": -999, "202013": 9.0},
        "T2M": {"202001": 14.2}}}}
    rows = npw.parse("Punjab", 30.9, 75.85, payload)
    assert sorted((r[5], r[4], r[6]) for r in rows) == [
        ("rain_mm_per_day", 1, 1.5), ("temp_c_mean", 1, 14.2)]


def test_nasa_collect_writes_one_call_per_state(tmp_path, monkeypatch):
    payload = {"properties": {"parameter": {
        "PRECTOTCORR": {"202001": 1.0}, "T2M": {"202001": 10.0}}}}
    calls = []
    monkeypatch.setattr(npw, "get_json", lambda u: calls.append(u) or payload)
    monkeypatch.setattr(npw.time, "sleep", lambda s: None)
    db = tmp_path / "t.duckdb"
    n = npw.collect(db, 2020, 2020)
    assert len(calls) == len(npw.POINTS) and n == 2 * len(npw.POINTS)
