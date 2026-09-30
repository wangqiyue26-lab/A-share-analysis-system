# Shadow trading protocol

Phase 4H turns the daily research ranking into a forward-only paper portfolio. Its purpose is to measure what the system would actually have done after a signal became observable, without rewriting history after the outcome is known.

## Baseline portfolio

The first baseline deliberately stays simple:

- use the daily market-factor Top10;
- equal weight the selected names;
- start from CNY 1,000,000 of simulated cash;
- treat the close-of-day ranking as the signal;
- execute no earlier than the next trading-day open;
- use the existing A-share execution engine for board lots, T+1, suspensions, price limits, commission, stamp duty and slippage;
- do not integrate the supplemental fundamental factors into the production score yet.

The purpose of equal weighting is to test selection quality before adding another optimisation layer through portfolio weighting.

## Immutable evidence

Each scheduled production run writes one small signal file under:

`research/shadow/signals/YYYY-MM-DD.csv`

The date is the market `as_of` date in `selected.csv`. The snapshot records the original rank, score, target weight, board/ST metadata, source and first-observed timestamp.

A rerun may reuse an identical signal file. If the newly generated selection differs from the already persisted file for the same market date, the pipeline refuses to overwrite it. This prevents accidental or intentional hindsight changes.

Signal files are small research evidence and may be retained in Git. Large market histories remain outside Git and continue to use caches/artifacts.

Each scheduled run also uploads a 90-day workflow-artifact backup of the signal ledger.

## Performance calculation

On each scheduled run the system rebuilds the paper portfolio from the immutable signal history and currently observable daily bars. This produces:

- `reports/production/shadow/equity_curve.csv`;
- `reports/production/shadow/trades.csv`;
- `reports/production/shadow/signal_history.csv`;
- `reports/production/shadow/metrics.json`;
- `site/shadow.html`.

The initial metrics include total return, annualised return/volatility, maximum drawdown, Sharpe ratio, daily positive-return rate, number of observed return days, number of trades and distinct signalled symbols.

The daily positive-return rate is a portfolio statistic. It must not be interpreted as the probability that an individual stock pick will make money. Individual forward-return hit rates and benchmark-relative attribution can be added once enough observations have matured.

## Fixed review windows

The system must not react to one or two good/bad days by changing factor weights.

- Days 0-19: evidence collection only.
- At 20 observed return days: first diagnostic review. Check data integrity, turnover, cost drag, concentration and obvious execution problems. Do not declare the strategy validated.
- Days 20-59: continue forward collection and compare predeclared variants only.
- At 60+ observed return days: begin stricter rolling validation, benchmark attribution and regime/sector diagnostics.

Any future strategy change receives a new `strategy_version`. Old signals remain in the ledger and are not rewritten.

## Real-money boundary

`real_money_ready` remains `false` in Phase 4H. Paper performance alone does not flip this flag. A later readiness gate must also include broader-universe validation, benchmark-relative results, drawdown/turnover limits, sufficient forward observations and portfolio-level risk rules.

The shadow portfolio is research evidence, not a recommendation to place trades.
