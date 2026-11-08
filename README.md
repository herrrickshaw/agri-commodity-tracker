# agri-commodity-tracker

Daily India agricultural commodity (APMC mandi) price tracking, a cross-country
World Bank agriculture indicator panel, and the Food Corporation of India depot
directory — three small Python collectors that write into one DuckDB file
(`data/agri.duckdb`) with CSV snapshots committed alongside. Built for
researchers and analysts who want an open, re-collectable price history without
registration or paid keys.

> Status: active (cron-driven daily pulls). All sources are open government /
> World Bank APIs; data is republished as-is for research use, no warranty.
> Mandi coverage is complete from 2026-07-23 onward (see Limitations).

<!-- how-to-use-with-ai -->
> **New here?** See [How to use this repo with AI](HOW_TO_USE_WITH_AI.md) —
> clone it, paste it into an AI tool, and ask in plain English.
> [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/agri-commodity-tracker/blob/main/notebooks/colab_test.ipynb)

## What it does
- Pulls the full Agmarknet daily price feed (~16,000 rows/day: state / district / market / commodity / variety / grade, min–max–modal ₹ per quintal) from data.gov.in, partitioned by state to dodge the API's 10,000-row window.
- Accumulates those pulls into a mandi-level price history (`mandi_prices`), idempotent per pull date.
- Rebuilds a World Bank panel of 9 agriculture indicators × 7 countries (IN CN BR US ID VN TH), 1960–present (`worldbank_agri`).
- Pulls the FCI depot master (478 depots, 5 zones / 25 regions) by replicating the site's AES/HMAC request envelope — no browser needed (`fci_locations`).
- Exports CSV snapshots of every table and auto-commits them (`scripts/sync_data.sh`).
- Ships GOBARdhan / FOM bulk-sale reference documents under `docs/gobardhan/`.

## Quick start
```bash
git clone https://github.com/herrrickshaw/agri-commodity-tracker.git
cd agri-commodity-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 collectors/mandi_prices.py       # today's mandi prices -> data/agri.duckdb
```
Optional: `python3 collectors/worldbank_agri.py` and `python3 collectors/fci_locations.py`
rebuild the other two tables. Every collector accepts `--db <path>`.

## How it works
Each collector is a three-stage pipeline — network fetch, pure transform, DuckDB write —
so the transform step is unit-testable without the network:

| Stage | `mandi_prices.py` | `worldbank_agri.py` | `fci_locations.py` |
|---|---|---|---|
| fetch | `fetch_all_states()` — probe grand total, pull each state partition, re-pull short ones (≤3 passes) | `fetch_panel()` → `fetch_indicator()` pages the `[meta, observations]` envelope | `fetch_zones()` / `fetch_depots()` via `call()`, which signs+encrypts the body (`build_request_body()`) |
| transform | `rows_from_results()` | `parse_page()` | `depot_row()` |
| write | `write_rows()` — `DELETE WHERE pulled_on = today` then insert | `write_table()` — `CREATE OR REPLACE` | `write_table()` — `CREATE OR REPLACE` |

`collectors/common.py` holds the shared pieces: `get_json()` (GET + retry, with a
long wait on HTTP 429), the default DB path, the `--db` argument parser and
stdout logging. Each module is importable as `collectors.<name>` and runnable
as `python3 collectors/<name>.py` (the form cron uses).

Design decisions:
- **State partitioning** — the data.gov.in backend is Elasticsearch with
  `max_result_window=10000`; the daily feed is ~16k rows, so an unfiltered sweep
  can never pass row 10,000. Pagination trusts only the server's reported
  `total` and the page size it *actually* returns (the demo key clamps to 10).
- **Grand-total reconciliation** — throttled responses can be HTTP 200 with an
  empty `records` list, so partitions shorter than their reported total are
  re-pulled and a warning is logged if the sum still misses the grand total.
- **DuckDB + CSV snapshots** — one file, SQL-queryable, no server; the CSVs
  exist so the data is readable without DuckDB and diffable in git.
- **Python 3.9 compatible** — the production cron runs macOS `/usr/bin/python3`.

### Scheduling (cron, IST host)
```
30 14 * * *   cd <repo> && /usr/bin/python3 collectors/mandi_prices.py >> collect.log 2>&1 && ./scripts/sync_data.sh >> collect.log 2>&1
45 10 15 * *  cd <repo> && /usr/bin/python3 collectors/worldbank_agri.py >> collect.log 2>&1 && /usr/bin/python3 collectors/fci_locations.py >> collect.log 2>&1 && ./scripts/sync_data.sh >> collect.log 2>&1
```
The mandi feed fills through the IST day — pull after ~14:00 IST.

## Repository layout
| Path | Purpose |
|---|---|
| `collectors/common.py` | Shared HTTP-with-retry, default DB path, CLI/logging boilerplate |
| `collectors/mandi_prices.py` | Agmarknet daily prices → `mandi_prices` (accumulating) |
| `collectors/worldbank_agri.py` | World Bank indicators → `worldbank_agri` (rebuilt) |
| `collectors/fci_locations.py` | FCI depot directory → `fci_locations` (rebuilt) |
| `scripts/sync_data.sh` | DuckDB → CSV snapshots, then commit + push `data/` |
| `tests/` | pytest: pure-function unit tests, mocked end-to-end runs, CLI smoke tests |
| `data/agri.duckdb` | The DuckDB store (tracked, ~13 MB, regular git object — not LFS) |
| `data/*_snapshot.csv` | CSV export of each table (tracked; `fci` snapshot omits `raw_json`) |
| `docs/gobardhan/` | GOBARdhan FOM/LFOM bulk-sale notifications, SOPs and HS 3101 trade CSVs (reference, not generated) |
| `notebooks/colab_test.ipynb` | Colab walkthrough |
| `DATA_ACCESS.md` | API-key and LFS-history notes |

### Schema
- `mandi_prices(state, district, market, commodity, variety, grade, arrival_date, min_price, max_price, modal_price, pulled_on)`
- `worldbank_agri(country, indicator_code, indicator, year, value, source_last_updated)`
- `fci_locations(depot_id, depot_code, depot_name, region_code, region_name, zone_id, zone_name, credential_details, additional_information, primary_link, n_links, raw_json)`

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| Agmarknet via [data.gov.in](https://data.gov.in) resource `9ef84268-d588-465a-a308-a864a43d0070` | Current-day APMC prices, all states | REST API; public demo key by default (10 rows/request, ~1,700 requests/day), personal key via `DATA_GOV_IN_KEY` | Open Government Data; API serves *only the current day* |
| [World Bank API v2](https://api.worldbank.org/v2) | 9 indicators (NV.AGR.*, AG.CON.FERT.ZS, AG.YLD.CREL.KG, AG.PRD.FOOD.XD, AG.LND.*, SL.AGR.EMPL.ZS, TX.VAL.AGRI.ZS.UN) × 7 countries | REST API, no key | CC BY-4.0 |
| [FCI depot details](https://fci.gov.in/depot-details) | Full depot master with zone/region names and links | Encrypted POST API replicated from the site's public JS bundle (AES key, IV and HMAC key are client-side constants, not secrets) | Government site; near-static directory, scraped |
| [GOBARdhan portal](https://gobardhan.eil.co.in/important-circular) | DA&FW bulk-sale gazette notifications (text extracts) | Downloaded 2026-07-27 | Government gazette; see `docs/gobardhan/README.md` |

## Configuration
| Variable | Default | Purpose |
|---|---|---|
| `DATA_GOV_IN_KEY` | data.gov.in public demo key | Personal key honours `limit=1000`, cutting a daily pull to <100 requests. Register free at data.gov.in (My Account → API key). Never commit it. |

No other secrets are needed. The FCI AES/HMAC constants in `fci_locations.py`
are published in the site's JavaScript and are deliberately in the source.

## Testing
```bash
pip install -r requirements-dev.txt ruff
ruff check .
pytest -q          # 31 tests, network fully mocked, temp DuckDB per test
```

## Limitations and known issues
- **Historical stub days:** until 2026-07-23 the collector had a pagination bug
  (it trusted the requested `limit=1000` instead of the server's 10-row page and
  stopped after one page). Days before that are ~10-row stubs and cannot be
  backfilled — the source API only serves the current day.
- The demo key is shared and throttles (HTTP 429, sometimes a 200 with empty
  `records`) after a few hundred requests; the collector waits 60 s per attempt,
  up to 10, and reconciles short partitions, but a day can still come up short —
  watch for `WARNING: collected … but the API reported total=…` in `collect.log`.
- A new or renamed state spelling in the feed (it already uses `Keralam`,
  `Chattisgarh`, `Pondicherry`, `NCT of Delhi`) silently drops that state until
  it is added to `STATES`; the grand-total warning is the only signal.
- `scripts/sync_data.sh` pushes to `main` from cron with the host's git
  credentials; it is not idempotent against a concurrent manual commit.
- Git LFS history: two 1.9 MB LFS objects from an earlier layout are unreachable
  (account LFS budget exhausted) — clone with `GIT_LFS_SKIP_SMUDGE=1`; current
  data files are regular git objects. Details in [DATA_ACCESS.md](DATA_ACCESS.md).
- UPAg (`upag.gov.in/api/`) and FCI foodgrain stocks are candidate collectors,
  not implemented.

## Related repositories
- [herrrickshaw/india-mandi-arrivals](https://github.com/herrrickshaw/india-mandi-arrivals) — Agmarknet *arrivals* (quantities) and UPAG collectors; this repo covers prices.
- [herrrickshaw/repo-data-dedup](https://github.com/herrrickshaw/repo-data-dedup) — account-wide data source catalogue and LFS migration playbook.

## Licence
No licence file yet — all rights reserved. Underlying data carries its sources'
own terms (Open Government Data India, World Bank CC BY-4.0).
