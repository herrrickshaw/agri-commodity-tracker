"""Smoke tests: every CLI entry point the cron jobs invoke still starts and parses args."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
COLLECTORS = ["mandi_prices", "worldbank_agri", "fci_locations"]


@pytest.mark.parametrize("name", COLLECTORS)
def test_collector_help_runs_as_script(name):
    out = subprocess.run([sys.executable, f"collectors/{name}.py", "--help"],
                         cwd=REPO, capture_output=True, text=True, check=True)
    assert "--db" in out.stdout


def test_sync_script_parses():
    subprocess.run(["bash", "-n", "scripts/sync_data.sh"], cwd=REPO, check=True)
