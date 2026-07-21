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
EOF

git add data/
if git diff --cached --quiet; then
    echo "$(date '+%F %T') sync: no data changes"
else
    git commit -q -m "data: automated refresh $(date '+%F')"
    git push -q
    echo "$(date '+%F %T') sync: committed and pushed"
fi
