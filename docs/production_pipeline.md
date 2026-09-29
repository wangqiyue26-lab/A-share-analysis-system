# Daily production pipeline

Phase 4B turns the tested research components into one scheduled cloud pipeline.

## Daily path

1. Fetch a complete SSE/SZSE/BSE security master. A recent complete cached snapshot may be used only when the live master fails; snapshots older than 30 days are rejected.
2. Use AKShare/Eastmoney's all-A-share snapshot once to pre-screen by current turnover amount. ST names and recently listed securities are excluded before history requests.
3. If the bulk Eastmoney snapshot fails, fall back to a size proxy from the complete security master rather than repeatedly calling another all-market endpoint.
4. Fetch only the bounded candidate set's daily history. Per-symbol history keeps the existing Eastmoney -> Sina fallback and uses an Actions-restored Parquet cache. Normal days request only dates after the cached latest bar.
5. Remove symbols whose latest bar is older than the cross-sectional market as-of date, so suspended/stale names are not compared with fresh names.
6. Run the explainable market-factor ranking, write CSV/JSON outputs, build the static dashboard, upload research artifacts, then deploy the same site through GitHub Pages.

The default production screen is deliberately bounded (`candidate_limit=30`, `top_n=10`) to keep free public endpoints stable on GitHub-hosted runners. It is an engineering starting point, not a claim that 30 names are sufficient for final research. The candidate limit can be expanded after repeated cloud runs establish acceptable endpoint latency and failure rates.

## Backtest separation

The daily page does **not** backtest today's current candidate universe into the past. Doing so would introduce survivorship/current-universe bias. Phase 3B already supports strict as-of security-master snapshots and benchmark reporting. The scheduled pipeline retains complete security-master snapshots in cache from now on; strict rolling portfolio backtests can be added to the daily page once enough historical snapshots exist.

## Storage

- Source code/configuration stay in Git.
- Rebuildable bar/security-master data stay in GitHub Actions cache.
- Daily machine-readable outputs and the generated site are workflow artifacts.
- GitHub Pages receives the generated static site; the workflow does not commit daily market data into repository history.
