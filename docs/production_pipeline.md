# Daily production pipeline

Phase 4B turns the tested research components into one scheduled cloud pipeline.

## Two-runner daily path

The production workflow deliberately separates market-wide screening from historical-bar collection. Cloud tests showed that making a market-wide snapshot request and then immediately requesting many history endpoints from the **same GitHub-hosted runner/IP** could lead to remote disconnects. A separate job receives a fresh runner/IP and a small candidate artifact.

### Runner 1: universe preparation

1. Try to fetch a complete SSE/SZSE/BSE security master. A recent complete cached snapshot may be used only when the live master fails; stale snapshots are rejected by the security-master store.
2. Try the primary all-A-share spot snapshot and rank the bounded SSE/SZSE universe by real current traded amount.
3. If the primary screen fails, enter an explicit degraded chain rather than substituting share count for liquidity:
   - Eastmoney all-A-share spot snapshot;
   - Sina all-A-share spot snapshot;
   - a recent successful candidate cache (maximum seven days);
   - as a final bounded fallback, pre-screen a valid security master and re-rank the pre-screened names by **actual recent daily traded amount** from per-symbol history.
4. Exclude ST names. Listing-age verification is performed from the canonical master when that information is available; degraded spot-only candidates are still subject to the minimum-history rule before factor ranking.
5. The scheduled production universe is limited to **SSE + SZSE**. BSE remains tracked in the canonical security master and is not removed from the research platform; it is excluded from the scheduled candidate set until its cloud historical-data path is validated independently.
6. Write `candidates.csv` and `universe_manifest.json`, including the exact screen source and degraded-state warnings, then upload them as a GitHub Actions artifact.

The candidate cache and canonical security-master snapshots are intentionally separate. A degraded current-universe fallback is never written as a canonical historical security-master snapshot.

### Runner 2: history, factors and site

1. Download the candidate artifact on a fresh GitHub-hosted runner.
2. Fetch only the bounded candidate set's daily history. Per-symbol history keeps the existing provider fallback and uses an Actions-restored Parquet cache. Normal days request only dates after the cached latest bar.
3. Remove symbols whose histories are too short for the factor engine, and remove stale/suspended symbols whose latest bar is older than the cross-sectional market as-of date.
4. Run the explainable market-factor ranking, write CSV/JSON outputs, build the static dashboard and upload research artifacts.
5. On `main`, upload the generated static site as a Pages artifact and deploy it through GitHub Pages.

The dashboard exposes the stock name, factor ranking, history/exclusion counts and the current data-source state. If a fallback candidate source was used, the page visibly marks the run as **备用源** and names the source. A degraded universe therefore cannot silently look like a normal primary-data run.

The default production screen is deliberately bounded (`candidate_limit=30`, `top_n=10`) to keep free public endpoints stable on GitHub-hosted runners. It is an engineering starting point, not a claim that 30 names are sufficient for final research. The candidate limit can be expanded after repeated cloud runs establish acceptable endpoint latency and failure rates.

## Backtest separation

The daily page does **not** backtest today's current candidate universe into the past. Doing so would introduce survivorship/current-universe bias. Phase 3B already supports strict as-of security-master snapshots and benchmark reporting. The scheduled pipeline retains complete security-master snapshots in cache from now on; strict rolling portfolio backtests can be added to the daily page once enough historical snapshots exist.

## Workflow concurrency

The scheduled production workflow owns the shared `pages` concurrency group and may supersede an older Pages run. The manual preview workflow uses the same group with `cancel-in-progress: false`, so a manual preview can queue behind production but cannot cancel a production update.

## Storage

- Source code/configuration stay in Git.
- Rebuildable bar/security-master data stay in GitHub Actions cache.
- The candidate handoff, daily machine-readable outputs and generated site are workflow artifacts.
- GitHub Pages receives the generated static site; the workflow does not commit daily market data into repository history.
