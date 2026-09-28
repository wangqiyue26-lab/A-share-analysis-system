# Daily research pipeline

Phase 2C connects the previously isolated components into an end-to-end research path:

```text
trade date
   ↓
symbol universe
   ↓
bounded-concurrency incremental market-data update
   ↓
per-symbol Parquet cache
   ↓
current-date coverage check
   ↓
factor engine
   ↓
ranking.csv + exclusions.csv + updates.csv + summary.json
```

## Incremental cache rules

For a symbol with no cache, the updater bootstraps a configurable calendar window (180 days by default). For an existing cache, it requests only dates after the last stored bar. Incoming bars overwrite duplicate trade dates so corrected source data can replace an earlier value.

Only symbols whose latest cached bar equals the target trading date enter that day's ranking. A suspended/stale symbol is therefore visible in `updates.csv` instead of being ranked from old prices.

## Failure isolation

Each symbol updates independently. One data-source failure does not abort the whole batch. The status table records `updated`, `cached`, `stale` or `failed`, along with the source endpoint and error when available.

## Cloud scaling

The current batch updater provides bounded thread concurrency inside one runner. Before the first full-market bootstrap is scheduled, the workflow will be sharded across multiple GitHub Actions jobs and each shard will keep a rebuildable cache. This avoids turning ~5,000 serial history requests into one long-running job.

The end-to-end CI smoke intentionally uses only a few liquid stocks; it validates orchestration, not production coverage.
