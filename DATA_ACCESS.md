# DATA_ACCESS — how to get this repo's data

> ⚠️ **Git LFS in this repo is currently unreachable** — the account's LFS budget
> is exhausted, so `git clone` / `git lfs pull` cannot download the data files
> (they arrive as ~130-byte pointer stubs). This is account-wide, not specific to
> this repo. Clone with `GIT_LFS_SKIP_SMUDGE=1 git clone …` to avoid errors.

## This repo's LFS footprint (audit 2026-07-22)

| LFS objects | Total size |
|---|---|
| 2 | 1.9 MB |

## Where the data actually comes from

1.9 MB — Agmarknet mandi daily pulls; re-collectable via the repo's own collector (data.gov.in public key, pull after 14:00 IST; daily 14:30 cron).

## data.gov.in API key

`collectors/mandi_prices.py` uses data.gov.in's public **sample key** by default.
The sample key works but **caps every response at 10 records** regardless of the
requested `limit`, so a full daily feed (~16,000 records) takes ~1,700 paginated
requests (~15–25 min, and more exposure to 429 rate-limiting).

**Recommended:** register a free personal API key at
[data.gov.in](https://data.gov.in/user/register) (My Account → API key) and
export it before running the collector / in the cron environment:

```
export DATA_GOV_IN_KEY=<your key>
```

Personal keys honour large page sizes (`limit=1000`), so the same pull completes
in well under 100 requests.

Two server-side quirks the collector works around (with any key):

- **10,000-row result window** — the API's Elasticsearch backend rejects
  `offset+limit > 10000`, and the daily feed is ~16k rows, so an unfiltered
  sweep can never reach past row 10,000. The collector therefore partitions by
  state (`filters[state.keyword]`, each state is far below 10k rows/day) and
  paginates within each partition, on the server's *reported* `total` and
  *actual* returned page size — never the requested limit. It warns if the
  summed row count doesn't match the API's grand total (e.g. a new/renamed
  state name missing from its `STATES` list — note the feed's own spellings:
  `Keralam`, `Chattisgarh`, `Pondicherry`, `NCT of Delhi`).
- **429 rate-limiting** — the shared sample key throttles after bursts of a few
  hundred requests and clears in a few minutes; the collector waits 60s per
  attempt (up to 10) on 429.

## Account-wide context

- Full pointer inventory, dedup plan and audit tooling:
  [`herrrickshaw/repo-data-dedup`](https://github.com/herrrickshaw/repo-data-dedup)
- Source catalogue + re-collection SOP for every dataset:
  [`SOP_DATA_SOURCES.md`](https://github.com/herrrickshaw/repo-data-dedup/blob/main/SOP_DATA_SOURCES.md)
- Migration recipe off LFS:
  [`PLAYBOOK.md`](https://github.com/herrrickshaw/repo-data-dedup/blob/main/PLAYBOOK.md)
- **Policy: do not add new LFS objects** — they would be born unreachable. New data
  goes in as gzipped/parquet regular git objects under 50 MB, one canonical format,
  with its collector script committed alongside.
