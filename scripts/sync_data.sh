#!/bin/bash
# Refresh CSV snapshots from the DuckDB, then commit+push data/ if anything changed.
# Called by cron after each collector run (see README "Scheduling").
set -euo pipefail
cd "$(dirname "$0")/.."

/usr/bin/python3 - << 'EOF_PY'
import duckdb

con = duckdb.connect("data/agri.duckdb")
con.execute("COPY mandi_prices TO 'data/mandi_prices_snapshot.csv' (HEADER)")
con.execute("COPY worldbank_agri TO 'data/worldbank_agri_snapshot.csv' (HEADER)")
# fci_locations only exists once collectors/fci_locations.py has run at least once.
have_fci = con.execute(
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'fci_locations'"
).fetchone()[0]
if have_fci:
    con.execute("COPY (SELECT * EXCLUDE (raw_json) FROM fci_locations) "
                "TO 'data/fci_locations_snapshot.csv' (HEADER)")
else:
    print("sync: fci_locations table not present yet — snapshot skipped")
con.close()
EOF_PY

git add data/
if git diff --cached --quiet; then
    echo "$(date '+%F %T') sync: no data changes"
else
    git commit -q -m "data: automated refresh $(date '+%F')"
    git push -q
    echo "$(date '+%F %T') sync: committed and pushed"
fi
