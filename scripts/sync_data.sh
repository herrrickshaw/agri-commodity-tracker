#!/bin/bash
# Refresh CSV snapshots from the DuckDB, then commit+push data/ if anything changed.
# Called by cron after each collector run.
set -e
cd "$(dirname "$0")/.."

/usr/bin/python3 - << 'EOF'
import duckdb
con = duckdb.connect('data/agri.duckdb')
con.execute("COPY mandi_prices TO 'data/mandi_prices_snapshot.csv' (HEADER)")
con.execute("COPY worldbank_agri TO 'data/worldbank_agri_snapshot.csv' (HEADER)")
try:
    con.execute("COPY (SELECT * EXCLUDE (raw_json) FROM fci_locations) TO 'data/fci_locations_snapshot.csv' (HEADER)")
except Exception:
    pass  # table may not exist until fci_locations.py has run
EOF

git add data/
if git diff --cached --quiet; then
    echo "$(date '+%F %T') sync: no data changes"
else
    git commit -q -m "data: automated refresh $(date '+%F')"
    git push -q
    echo "$(date '+%F %T') sync: committed and pushed"
fi
