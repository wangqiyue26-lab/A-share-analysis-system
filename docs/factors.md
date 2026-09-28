# Factor engine design

Phase 2 is split into two parts so market factors can be validated before introducing financial-statement availability rules.

## Phase 2A: market factors

All factors use only canonical daily bars and are evaluated at the latest available trading date for each symbol.

| Factor | Definition | Default direction |
| --- | --- | --- |
| `momentum_20` | `close[t] / close[t-20] - 1` | higher |
| `momentum_60` | `close[t] / close[t-60] - 1` | higher |
| `trend_ma20_ma60` | `MA20 / MA60 - 1` | higher |
| `volatility_20` | daily-return std × sqrt(252) | lower |
| `max_drawdown_60` | positive magnitude of worst trailing 60-day peak-to-trough drawdown | lower |
| `log_amount_20` | `log(1 + mean(amount, 20))` | higher |

The engine keeps raw factor values and creates separate `score_<factor>` columns after cross-sectional winsorization and z-scoring. Lower-is-better factors have their standardized score sign reversed before weighted aggregation.

## Universe gate

Phase 2A currently requires:

- at least 61 daily bars;
- a valid positive amount series;
- configurable minimum 20-day average trading amount.

ST status, listing age, delisting flags and board-specific eligibility require security-master history and are added in the next data/universe step rather than guessed from current names.

## Phase 2B: fundamentals

Financial factors must include an `available_at`/announcement timestamp. A report for a historical period is not usable in a backtest before it was actually published. This requirement is intentionally treated as part of the data model, not a post-hoc correction.
