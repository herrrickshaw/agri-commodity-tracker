"""Open-data collectors writing into ``data/agri.duckdb``.

Each module is both importable (``from collectors import mandi_prices``) and a
standalone CLI (``python3 collectors/mandi_prices.py``), which is how the cron
jobs invoke them.
"""
