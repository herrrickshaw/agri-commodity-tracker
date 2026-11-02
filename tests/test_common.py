"""Unit tests for collectors/common.py (network mocked)."""
import io
import json
import urllib.error

import pytest

from collectors import common


class _Resp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._buf

    def __exit__(self, *a):
        return False


def test_get_json_uses_injected_opener():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return _Resp({"ok": 1})

    assert common.get_json("http://x/y", opener=opener) == {"ok": 1}
    assert seen == {"url": "http://x/y", "ua": common.BROWSER_UA,
                    "timeout": common.HTTP_TIMEOUT_S}


def test_get_json_linear_backoff_then_raise(monkeypatch):
    sleeps = []
    monkeypatch.setattr(common.time, "sleep", sleeps.append)

    def boom(req, timeout):
        raise urllib.error.URLError("down")

    with pytest.raises(urllib.error.URLError):
        common.get_json("http://x", retries=3, backoff_s=2, opener=boom)
    assert sleeps == [2, 4]            # retries-1 sleeps, linear in attempt number


def test_get_json_429_uses_throttle_wait(monkeypatch):
    sleeps = []
    monkeypatch.setattr(common.time, "sleep", sleeps.append)
    seq = [urllib.error.HTTPError("u", 429, "e", {}, None),
           urllib.error.HTTPError("u", 500, "e", {}, None),
           _Resp({"records": []})]

    def flaky(req, timeout):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    out = common.get_json("http://x", retries=5, backoff_s=3, throttle_wait_s=60, opener=flaky)
    assert out == {"records": []}
    assert sleeps == [60, 6]           # 429 -> throttle wait; 500 on attempt 1 -> 3*(1+1)


def test_db_arg_parser_default_points_at_repo_data_dir():
    args = common.db_arg_parser("x").parse_args([])
    assert args.db == str(common.REPO_ROOT / "data" / "agri.duckdb")
    assert common.db_arg_parser("x").parse_args(["--db", "/tmp/a.duckdb"]).db == "/tmp/a.duckdb"
