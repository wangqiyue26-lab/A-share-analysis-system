# Phase 4K-2E — Reconstructed historical Top60 engine

This module reconstructs a historical v2 liquidity candidate universe only after the required historical inputs have been separately validated.

It does not create strict contemporaneous PIT evidence.

Required inputs:

- official reconstructed SSE/SZSE lifecycle membership;
- historical daily amount archive including current and delisted A shares;
- historical daily state archive with `is_st`, `trade_status`, and `close`.

For each trading date the engine applies the production eligibility logic:

```text
exchange in {SSE, SZSE}
list_date <= date - 120 calendar days
date < delist_date, when a delist date exists
is_st == false
trade_status == true
close > 0
amount >= CNY 50,000,000
```

Eligible securities are ranked by daily amount and the first 60 are selected.

## Known production-equivalence gap

Live production sorts equal-amount rows by:

```text
amount DESC
float_market_cap DESC
symbol ASC
```

The historical reconstruction currently lacks a full historical float-market-cap archive. Therefore exact amount ties are resolved by symbol.

Every reconstructed day records:

- the candidate cutoff amount;
- the number of rows tied at that amount;
- the missing-state / missing-amount counts;
- state and amount coverage;
- the explicit sort-rule difference.

This prevents the reconstruction from claiming exact production equivalence where evidence does not support it.

## Fail-closed policy

The engine refuses a day when:

- any lifecycle-eligible symbol lacks historical state;
- fewer than 60 liquid, tradable, non-ST securities survive;
- required input columns are invalid or duplicated.

The calendar runner records failed dates rather than silently shifting the rebalance schedule.

The module must not be connected to the historical v2 backtest until the full amount and historical-state archives pass their own validation gates.
