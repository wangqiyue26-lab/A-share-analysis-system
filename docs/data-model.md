# Security master and point-in-time data

## Current security master

The current A-share master is assembled from exchange-oriented AKShare interfaces for SSE, SZSE and BSE. The normalized schema contains code, name, exchange, board, listing date, industry/share fields when supplied by the source, current ST-name flag, listing flag and `observed_at`.

Every **complete** fetch can be stored as an append-only Parquet snapshot. Partial exchange results are never persisted as a canonical snapshot.

The provider fetches SSE, SZSE and BSE independently with retry/backoff. `fetch_current()` exposes exchange-level failures and a `complete` flag. Production callers use `get_current()` which rejects an incomplete universe by default. This prevents a temporary exchange outage from silently turning into a biased stock-selection universe.

A current snapshot is **not** equivalent to a historical universe. Backtests before the first retained snapshot still require historical listing/delisting/status events. Phase 3 must not silently substitute today's constituents for old dates.

## Point-in-time fundamental data

Fundamental records use long form:

```text
symbol | metric | value | period_end | available_at | source
```

`period_end` answers “which accounting period does this number describe?” while `available_at` answers “when could the strategy have known it?”. Both are required.

Example: a 2025 annual-report ROE with `period_end=2025-12-31` and an announcement on `2026-03-25` is unavailable to a 2026-02-01 backtest even though the accounting period had ended.

Restatements are supported naturally: the same period/metric can have a later `available_at`. `latest_metrics_as_of()` only selects versions observable by the requested cutoff.

## Provider policy

The core model refuses to invent `available_at`. A source that supplies report period but no defensible announcement/availability timestamp is not automatically eligible for historical fundamental backtests. It may still be useful for current research, but that is a separate mode and must be labeled accordingly.
