# A-share Analysis System

个人 A 股量化研究与选股系统。目标是构建一套可在 GitHub Actions 云端自动运行的研究平台，而不是单一“荐股脚本”。

## Roadmap

- **Phase 1 — Foundation**：多源数据接口、统一数据格式、缓存、配置、日志、CI、云端 smoke test。
- **Phase 2 — Factor Engine**：股票池、基本面/估值/质量/成长/动量/波动/流动性因子、标准化与综合评分。
- **Phase 3 — China Backtest**：T+1、涨跌停、停牌、ST/退市、费用/滑点、历史股票池、Point-in-Time 财务数据。
- **Phase 4 — Dashboard**：每日选股、个股解释、组合/风险/回测页面，部署 GitHub Pages。
- **Phase 5 — ML Lab**：Qlib/LightGBM、Walk-forward、特征重要性、模型集成。

## Phase 1 architecture

```text
AKShare (primary) / future backup providers
                ↓
          provider interface
                ↓
       canonical bar schema
                ↓
        validation + retry
                ↓
          Parquet cache
                ↓
       factor/backtest layers
```

## Quick start

Requires Python 3.11+.

```bash
python -m pip install -e ".[dev]"
pytest -q
ashare-system health
ashare-system smoke-data --symbol 000001 --start 20240102 --end 20240110
```

The network smoke test intentionally checks a real public A-share data interface. Unit tests do not depend on external network availability.

## Cloud automation

- `.github/workflows/ci.yml`: unit tests on pushes/PRs plus a non-blocking public-data network smoke test.
- `.github/workflows/daily.yml`: weekday cloud health/data check. The schedule uses UTC; `09:17 UTC = 17:17 China Standard Time`.

## Data policy

Large historical datasets are **not committed to Git**. Local/cloud cache files live under `data/cache/` and are ignored. Cache is treated as rebuildable acceleration, never as the only copy of source data.

## Disclaimer

This repository is for quantitative research and software engineering. Outputs are research signals, not guaranteed investment returns or individualized investment advice.
