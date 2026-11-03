# Phase 4J — V2 robustness suite

Phase 4J starts after the first calibrated Phase 4I development backtest produced an unexpectedly strong cumulative result.

The purpose of Phase 4J is **not** to search for higher returns. It freezes the current capital-aware-v2 selection and portfolio logic and asks whether the observed result survives simple robustness checks.

## Why this phase exists

The calibrated 2025-01-02 through 2026-11-03 development run produced a strong cumulative return, but the return distribution is concentrated:

- one 20-trading-day block generated an unusually large gain;
- the strongest few blocks account for a large share of compounded performance;
- the historical experiment still replays today's 60-name universe backward in time;
- therefore survivorship/current-universe bias remains unresolved.

A high total return is not sufficient evidence of a robust edge.

## Frozen baseline

Phase 4J does not change:

- factor weights in `config/factors_v2.yml`;
- 3-stock capital-aware portfolio construction;
- 5-trading-day rebalance interval;
- entry/keep rank buffers;
- MA20 and momentum entry gates;
- ETF trend sleeve;
- CNY 20,000 starting capital;
- actual stock and ETF fee schedules.

Any later change to those rules requires a new strategy version or a clearly identified development experiment.

## Robustness scenarios

The workflow `.github/workflows/v2-robustness.yml` prepares one 60-name current universe and then evaluates all scenarios against that same snapshot.

### Execution stress

The 2025+ period is replayed at:

- 5 bps slippage — baseline;
- 10 bps slippage — moderate execution stress;
- 20 bps slippage — severe execution stress.

The goal is to measure degradation, not to select the slippage assumption that makes the strategy look best.

### Temporal holdout

The frozen v2 logic is also evaluated over 2023-01-03 through 2024-12-31, a period that was not used when the original v2 parameters were proposed.

This is useful temporal evidence but **not strict PIT validation**, because the experiment still uses a current observable universe replayed backward. Once this holdout result is inspected, it is considered consumed evidence and cannot be reused as a pristine holdout after further tuning.

## Return concentration diagnostics

Every 20-day validation summary records:

- best block return;
- worst block return;
- compounded return excluding the single best block;
- share of total log return contributed by the best block;
- share contributed by the two best blocks.

These diagnostics help distinguish a broadly persistent edge from a result dominated by one or two exceptional periods.

## Interpretation

Phase 4J can reject a fragile strategy. It cannot prove investability.

Evidence hierarchy remains:

1. current-universe development and robustness tests;
2. strict PIT / historically observable universe tests;
3. forward CNY 20k shadow portfolio;
4. only then a real-money readiness review.

A strategy should be treated skeptically when a small increase in slippage, removal of one exceptional block, or movement to another time period destroys its apparent edge.
