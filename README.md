# agri-commodity-tracker

Daily India agricultural commodity price tracking + cross-country agriculture
indicator panel, from fully open sources (no registration, no paid keys).

## Sources

| Collector | Source | Data | Cadence |
|---|---|---|---|
| `collectors/mandi_prices.py` | Agmarknet via [data.gov.in](https://data.gov.in) API | APMC mandi prices: state / district / market / commodity / variety, min-max-modal ₹ per quintal | Daily (feed fills through the IST day — pull after ~14:00 IST) |
| `collectors/worldbank_agri.py` | [World Bank API](https://api.worldbank.org/v2) | 9 agri indicators (value-added, fertilizer use, cereal yield, food production index, irrigation, employment) × 7 countries (IN CN BR US ID VN TH), 1960–present | Annual series; re-pull monthly |

The data.gov.in collector uses the **public demo API key documented on
data.gov.in** by default; set `DATA_GOV_IN_KEY` to use a personal key.

## Usage

```bash
pip install -r requirements.txt
python3 collectors/mandi_prices.py       # today's mandi prices -> data/agri.duckdb
python3 collectors/worldbank_agri.py     # rebuild WB agri panel -> data/agri.duckdb
```

Both write to `data/agri.duckdb` (gitignored). CSV snapshots of each table are
committed under `data/` for reference.

`mandi_prices` accumulates daily (idempotent per pull date — re-running a day
replaces that day's rows), building a mandi-level price history over time.
Suggested cron (14:30 IST daily):

```
30 14 * * * cd <repo> && /usr/bin/python3 collectors/mandi_prices.py >> collect.log 2>&1
```

## Schema

- `mandi_prices(state, district, market, commodity, variety, grade, arrival_date, min_price, max_price, modal_price, pulled_on)`
- `worldbank_agri(country, indicator_code, indicator, year, value, source_last_updated)`

## Provenance

Sources were identified by access-probing under-used data sources on
2026-07-21: the data.gov.in sample key was verified live against the Agmarknet
daily-price resource (`9ef84268-d588-465a-a308-a864a43d0070`), and the World
Bank API returned data current to 2025. UPAg (`upag.gov.in/api/`) responds but
its endpoints are not yet mapped — a candidate future collector, alongside
FCI foodgrain stocks (browser-rendered pages).
