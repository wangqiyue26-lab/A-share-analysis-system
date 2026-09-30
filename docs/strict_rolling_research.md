# Strict rolling research evidence

The system must not reconstruct a historical stock universe from today's security master. A strict historical run needs evidence that was actually observable at or before each research cutoff.

## Security-master evidence

`SecurityMasterSnapshotStore` already writes append-only complete exchange snapshots into `data/cache/security_master/`. Phase 4F adds an independent daily artifact copy so this evidence is not dependent on Actions cache survival alone.

After the daily universe job, `python -m ashare_system.archive_security_master`:

1. locates the latest retained complete security-master snapshot;
2. re-validates that SSE, SZSE and BSE are all present;
3. copies the Parquet snapshot without modifying its observation timestamp;
4. writes a manifest containing the observation timestamp, row count, exchanges and SHA-256 checksum;
5. marks the artifact as eligible for strict point-in-time research only at dates on or after its `observed_at` timestamp.

The workflow uploads this directory as `security-master-snapshot-<run_id>` with 90-day retention. If no complete canonical snapshot exists, the archive step records the failure but does not block the daily market ranking.

## Why this matters

Current membership, ST status, names and share metadata can change through time. Substituting today's universe into an old backtest introduces survivorship and availability bias even when price and financial data are otherwise point-in-time safe.

The archive therefore stores the observation itself, not a reconstructed guess.

## Current strictness boundary

The production dashboard does not claim a strict rolling portfolio backtest. Fundamental factors are also still supplemental and do not alter the live composite score.

A future strict rolling experiment may use only cutoffs for which the required universe evidence is actually retained. If a cutoff predates the earliest available snapshot, the experiment must fail closed rather than fall back to a newer universe.

## Next research gate

Before enabling fundamental factors in production scoring, collect enough snapshots to evaluate multiple rebalance dates and reporting cycles. Then validate:

- universe coverage and membership changes;
- ST and listing-age filters at each cutoff;
- financial-factor coverage at each cutoff;
- factor cross-sectional stability and sector concentration;
- turnover, transaction costs and benchmark-relative results;
- sensitivity to rebalance frequency and factor weights.

Only a separately versioned experimental configuration should be promoted after those checks. The current production market-factor ranking remains the control configuration.
