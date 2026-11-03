# Phase 4I — CNY 20k capital-aware validation

Phase 4I starts after the first forward shadow experiment exposed a severe mismatch between the original research ranking and a small real account.

The legacy v1 experiment remains preserved as evidence. It started with CNY 1,000,000, used daily Top10 equal weights, and produced very high turnover and poor forward performance. Phase 4I does not rewrite that result.

## Why v2 exists

The first forward experiment revealed several structural problems:

- 20/60-day momentum and trend factors were paired with daily constituent replacement;
- roughly half of the Top10 could change from one day to the next;
- losses were materially larger than gains;
- lower-ranked names contributed most of the loss;
- the 30-name liquidity screen was too narrow and reinforced a hot-stock bias;
- a CNY 20k account cannot efficiently hold ten A-share stocks because board lots and minimum commission dominate small tickets;
- expensive names can be theoretically selected but practically unaffordable;
- unused capital needs an explicit cash / ETF policy instead of silently becoming an implementation accident.

## v2 account assumptions

Forward v2 uses CNY 20,000 simulated starting capital.

Stock transaction costs follow the user's observed broker schedule:

- trade notional <= CNY 10,000: CNY 5 commission;
- trade notional > CNY 10,000: CNY 5 + excess above CNY 10,000 * 0.0086%;
- stock transfer fee: 0.001% of notional;
- stock sell stamp duty: 0.05%;
- simulated slippage: 5 bps each side.

ETF trades use the same broker commission schedule in the current model, but stock stamp duty and stock transfer fee are not applied. This ETF fee assumption should be replaced with the user's own ETF contract note when available.

## v2 factor model

The original production score allocated 55% to medium-term momentum/trend. v2 reduces that exposure and raises explicit risk control:

| factor | v1 | v2 |
| --- | ---: | ---: |
| momentum_20 | 20% | 15% |
| momentum_60 | 20% | 10% |
| MA20/MA60 trend | 15% | 15% |
| 20-day volatility, lower is better | 15% | 25% |
| 60-day max drawdown, lower is better | 15% | 25% |
| 20-day liquidity | 15% | 10% |

The factor engine also records close / MA20 trend state. New v2 entries require positive 20-day momentum and price above MA20. After the minimum holding period, a stock that falls below MA20 no longer qualifies for the rank-buffer hold rule.

These are hypotheses to test, not claims that v2 is already better.

## Capital-aware portfolio construction

The initial v2 baseline is deliberately simple:

- production candidate universe expands from 30 to 60 liquid SSE/SZSE names;
- at most 3 individual-stock positions;
- 25% target weight per stock;
- total stock target <= 75%;
- at least 10% cash;
- rebalance every 5 trading days, not every day;
- new entries must rank in the top 5;
- existing positions can remain while rank <= 10;
- minimum holding period is 5 trading days;
- one A-share board lot must fit inside the CNY 5,000 target ticket;
- unaffordable high-priced stocks are skipped rather than producing a zero-share “position”.

This rule set directly addresses the main v1 turnover and affordability failures.

## ETF sleeve

ETFs are treated as a separate sleeve rather than being ranked in the same cross-section as individual stocks.

Initial pool:

- 510300 — CSI 300 ETF
- 510500 — CSI 500 ETF
- 159915 — ChiNext ETF
- 588000 — STAR 50 ETF

At each rebalance, the ETF sleeve considers only ETFs with positive 20-day momentum and price above MA60, then selects the strongest candidate. If stock slots are unused, the ETF weight may increase, but the portfolio still keeps the configured minimum cash reserve. If no ETF passes its trend gate, the residual remains cash.

This is intentionally conservative. More asset classes should not be added until the basic stock/ETF sleeve is validated.

## Fast 20-trading-day development loop

Waiting 20 real trading days for every design change is too slow. Phase 4I therefore adds a historical development loop based on non-overlapping 20-trading-day return blocks.

Each block records:

- strategy return;
- CSI 300 return;
- excess return;
- maximum drawdown;
- positive-day rate;
- trade count;
- turnover;
- explicit commission / tax / transfer fees;
- simulated slippage;
- total cost drag.

The initial development gate requires at least 12 complete 20-day blocks and also checks positive-block rate, median drawdown, median turnover, cost drag, and CSI 300 outperformance frequency.

### Critical limitation

The fast experiment currently uses the current observable candidate universe to replay older history. That introduces survivorship / historical-universe bias.

Therefore:

**Fast historical blocks are for debugging, hypothesis rejection, and relative comparison between versions. They are not real-money evidence.**

Strict validation still requires genuinely observed historical security-master snapshots or other valid point-in-time universe evidence.

## Iteration discipline

The project should use three layers of evidence:

1. **Development backtest** — many historical 20-day blocks, fast and inexpensive. Reject obviously poor designs quickly.
2. **Strict/PIT validation** — use only historical universes and facts that were actually observable at the time.
3. **Forward shadow portfolio** — final out-of-sample evidence. Historical tuning must never rewrite this ledger.

A new strategy change receives a new strategy version. Do not repeatedly tune v2 against the same bad 20-day block and then call the resulting fit “validation”.

## v1 / v2 boundary

- v1 shadow ledger: retained under research/shadow/signals/ and no longer used as the active forward portfolio.
- v2 ranking ledger: research/shadow_v2/rankings/.
- v2 initial cash: CNY 20,000.
- v2 real_money_ready: always false in Phase 4I.

The purpose of v2 is to build a strategy that survives repeated historical 20-day windows and then demonstrates the same behavior in new forward data.
