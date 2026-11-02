"""Shared helpers for the collectors: HTTP-with-retry, DuckDB path, CLI boilerplate.

Pure plumbing only — no source-specific logic lives here. Python 3.9-compatible
because the production cron runs the macOS system ``/usr/bin/python3``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "agri.duckdb"

#: Browser-like User-Agent — several Indian government hosts 403 the urllib default.
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
UA = {"User-Agent": BROWSER_UA}

HTTP_TIMEOUT_S = 30


def get_json(
    url: str,
    retries: int = 3,
    backoff_s: float = 2.0,
    throttle_wait_s: float | None = None,
    headers: dict | None = None,
    timeout_s: float = HTTP_TIMEOUT_S,
    opener: Callable[..., Any] | None = None,
) -> Any:
    """GET ``url`` and parse the JSON body, retrying transient failures.

    Attempt ``i`` (0-based) sleeps ``backoff_s * (i + 1)`` before the next try.
    If ``throttle_wait_s`` is set, an HTTP 429 sleeps that long instead (the
    data.gov.in demo key throttles for minutes, not seconds). The last failure
    is re-raised unchanged so callers see the real error. ``opener`` defaults to
    ``urllib.request.urlopen`` looked up at call time (so tests can patch it).
    """
    open_fn = opener or urllib.request.urlopen
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            with open_fn(req, timeout=timeout_s) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if i == retries - 1:
                raise
            if throttle_wait_s is not None and e.code == 429:
                time.sleep(throttle_wait_s)
            else:
                time.sleep(backoff_s * (i + 1))
        except Exception:  # noqa: BLE001 — network/JSON errors; re-raised on last attempt
            if i == retries - 1:
                raise
            time.sleep(backoff_s * (i + 1))
    raise RuntimeError("unreachable: retries must be >= 1")


def db_arg_parser(description: str) -> argparse.ArgumentParser:
    """Argument parser shared by every collector: just ``--db``."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="DuckDB file to write (default: data/agri.duckdb)")
    return ap


def configure_cli_logging() -> None:
    """Plain-message logging to stdout, so cron ``>> collect.log`` output is unchanged."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
