# A-share Analysis System

个人 A 股量化研究与选股系统。目标是构建一套可在 GitHub Actions 云端自动运行的研究平台，而不是单一“荐股脚本”。

## Roadmap

- ✅ **Phase 1 — Foundation**：多端点数据接口、统一数据格式、缓存、配置、日志、CI、云端 smoke test。
- 🚧 **Phase 2 — Factor Engine**：股票池、市场/技术因子、基本面/估值/质量/成长因子、标准化与综合评分。
- **Phase 3 — China Backtest**：T+1、涨跌停、停牌、ST/退市、费用/滑点、历史股票池、Point-in-Time 财务数据。
- **Phase 4 — Dashboard**：每日选股、个股解释、组合/风险/回测页面，部署 GitHub Pages。
- **Phase 5 — ML Lab**：Qlib/LightGBM、Walk-forward、特征重要性、模型集成。

## Current architecture

```text
AKShare Eastmoney ─┐
                   ├─ DataRouter ─ canonical bar schema ─ Parquet cache
AKShare Sina ──────┘                                      │
                                                          ↓
                                                universe eligibility
                                                          ↓
                                                interpretable factors
                                                          ↓
                                             winsorize + cross-section z-score
                                                          ↓
                                               weighted score + ranking
```

## Phase 2A factors

The first factor layer is deliberately explainable and uses only information observable from daily market data:

- `momentum_20`: 20-trading-day price momentum.
- `momentum_60`: 60-trading-day price momentum.
- `trend_ma20_ma60`: MA20 / MA60 trend spread.
- `volatility_20`: annualized 20-day realized volatility; lower is better in the default score.
- `max_drawdown_60`: trailing 60-day maximum drawdown magnitude; lower is better.
- `log_amount_20`: log of trailing 20-day average trading amount as a liquidity proxy.

Raw values and per-factor standardized scores are both retained. Factor weights and directions live in `config/factors.yml`.

Financial statement factors are intentionally deferred to Phase 2B so that announcement/availability timestamps can be designed correctly instead of introducing look-ahead bias.

## Quick start

Requires Python 3.11+.

```bash
python -m pip install -e ".[dev]"
pytest -q
ashare-system health
ashare-system smoke-data --symbol 000001 --start 20240102 --end 20240110
```

The network smoke test intentionally checks real public A-share data interfaces. Unit tests do not depend on external network availability.

## Cloud automation

- `.github/workflows/ci.yml`: lint, unit tests, CLI health and a non-blocking public-data network smoke test.
- `.github/workflows/daily.yml`: weekday cloud health/data check after the A-share close.

## Data policy

Large historical datasets are **not committed to Git**. Local/cloud cache files live under `data/cache/` and are ignored. Cache is rebuildable acceleration, never the only copy of source data.

## Disclaimer

This repository is for quantitative research and software engineering. Outputs are research signals, not guaranteed investment returns or individualized investment advice.
