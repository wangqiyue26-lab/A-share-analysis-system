# A-share backtesting policy

Phase 3A provides a conservative daily-bar, long-only execution engine. Its first purpose is to prevent common sources of impossible fills and look-ahead execution before adding portfolio-scale strategy research.

## Execution model

- A signal stamped on trading day `T` is eligible to execute no earlier than the next available trading day.
- Orders use the next eligible day's opening price plus configurable slippage.
- Buy and sell quantities are rounded to the configured board lot.
- A zero-volume daily bar is treated as unavailable for execution and the target remains pending.
- A buy blocked at the daily upper price limit remains pending.
- A sell blocked at the daily lower price limit remains pending.
- Shares bought on a trading day cannot be sold on the same trading day (T+1 stock settlement guard).
- Cash, commission, sell-side stamp duty and mark-to-market equity are tracked explicitly.

## Historical rules currently modelled

- Sell-side stamp duty: 0.10% from 2008-09-19 until 2023-08-27, then 0.05% from 2023-08-28.
- Main-board ordinary shares: 10% daily price limit.
- ChiNext: 10% before 2020-08-24 and 20% from 2020-08-24.
- STAR Market: 20%.
- Beijing Stock Exchange: 30%.
- Main-board risk-warning shares: 5% before 2026-07-06 and 10% from 2026-07-06.

The engine deliberately rejects unsupported stamp-duty history before 2008-09-19 rather than silently applying a modern rate.

## Not yet modelled

The current engine must not be treated as exchange-perfect for these cases:

- IPO and other no-price-limit windows.
- Ex-rights/ex-dividend reference-price mechanics.
- Intraday temporary suspensions and auction microstructure.
- Historical ST/status/delisting reconstruction before retained security-master snapshots.
- Partial fills based on order-book depth or participation-rate limits.
- Short selling, margin financing, options, futures or convertible-bond rules.

These are explicit Phase 3B/3C work items. Until implemented, research that materially depends on them should be excluded or labelled as approximate.

## Metrics

The initial result bundle exposes total return, annualized return, annualized volatility, maximum drawdown and a zero-risk-free-rate daily Sharpe estimate. These metrics are diagnostics, not evidence that a strategy is robust. Walk-forward validation, benchmark comparison, turnover analysis and factor stability are separate research steps.
