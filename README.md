# A-share Analysis System

个人 A 股量化研究与选股系统。目标是构建一套可在 GitHub Actions 云端自动运行的研究平台，而不是单一“荐股脚本”。

## Roadmap

- ✅ **Phase 1 — Foundation**：多端点数据接口、统一数据格式、缓存、配置、日志、CI、云端 smoke test。
- ✅ **Phase 2A — Market Factor Engine**：股票池、市场/技术因子、标准化、综合评分与可解释排名。
- ✅ **Phase 3 — China Backtest Foundation**：T+1、涨跌停、停牌、ST/退市、费用/滑点、历史股票池、Point-in-Time 基础设施与沪深300基准报告。
- ✅ **Phase 4B — Cloud Daily Production**：真实 A 股候选池、自动降级、每日历史更新、Top10、研究产物、静态 Dashboard、GitHub Pages 发布链。
- ✅ **Phase 4C — Point-in-Time Fundamentals**：公告时间约束的财务事实缓存、修订版本保留、成长/质量/杠杆因子与真实公网校验。
- ✅ **Phase 4D — Daily Fundamental Research Layer**：Top10 自动补充 PIT 基本面、独立 Dashboard、财务缓存与失败隔离；暂不改变正式市场因子排名。
- 🚧 **Phase 4F — Strict Rolling Evidence**：持续留存实际观察到的 security-master 快照，为严格滚动股票池和组合研究积累可追溯证据。
- ⏭️ **Next — Strict Rolling Validation**：在历史快照、PIT 财务覆盖和多个报告周期足够后，验证基本面因子、换手/成本、行业偏置和滚动组合表现，再决定是否进入生产评分。
- **Phase 5 — ML Lab**：Qlib/LightGBM、Walk-forward、特征重要性、模型集成。

## Current architecture

```text
Exchange security master ──────────────────────────────────────────────┐
        │                                                             │
        └─ append-only snapshots + 90-day research artifacts          │
                                                                      │
Eastmoney all-A spot ─┐                                               │
Sina all-A spot ──────┼─ bounded universe + explicit fallback state ──┤
recent candidate cache┤                                               │
recent history amount ┘                                               ↓
                                                             candidate artifact
                                                                      ↓
AKShare Eastmoney ─┐                                                  │
                   ├─ DataRouter ─ canonical bar schema ─ Parquet cache
AKShare Sina ──────┘                                      │
                                                          ↓
                                                history eligibility
                                                          ↓
                                                interpretable factors
                                                          ↓
                                             winsorize + cross-section z-score
                                                          ↓
                                               weighted score + Top10
                                                          ↓
                                                static research dashboard
                                                          │
PIT financial statements ─ revision-safe cache ─ factors ─┤
                                                          ↓
                                         supplemental fundamentals page
                                                          ↓
                                                   GitHub Pages
```

## Current market factors

The production score is deliberately explainable and uses only information observable from daily market data:

- `momentum_20`: 20-trading-day price momentum.
- `momentum_60`: 60-trading-day price momentum.
- `trend_ma20_ma60`: MA20 / MA60 trend spread.
- `volatility_20`: annualized 20-day realized volatility; lower is better in the default score.
- `max_drawdown_60`: trailing 60-day maximum drawdown magnitude; lower is better.
- `log_amount_20`: log of trailing 20-day average trading amount as a liquidity proxy.

Raw values and per-factor standardized scores are both retained. Factor weights and directions live in `config/factors.yml`.

## Current point-in-time fundamental factors

Financial-statement facts now enforce `available_at` timestamps end to end and preserve later revisions as later-observable versions. The supplemental layer currently computes:

- revenue year-over-year growth;
- net-profit year-over-year growth;
- net margin;
- operating cash flow / net profit;
- liabilities / assets;
- factor coverage for each selected stock.

These factors are shown for research and explanation but remain outside the production composite score. That separation is intentional until enough strict historical universe snapshots and reporting cycles exist for rolling validation.

See `docs/pit_fundamental_enrichment.md` and `docs/strict_rolling_research.md`.

## Cloud production behavior

The scheduled pipeline runs after the China A-share close on GitHub-hosted runners. It currently uses a bounded 30-name SSE/SZSE candidate pool and selects the Top10 research ranking. This is an endpoint-stability starting point, not a claim that 30 names are sufficient for final research.

Market-wide public endpoints can be unstable from cloud IPs. The universe stage therefore records an explicit source and can degrade through Eastmoney spot, Sina spot, a recent candidate cache, and finally a bounded history-liquidity path based on actual traded amount. Degraded current-universe data is never written as a canonical historical security-master snapshot.

The dashboard visibly marks fallback runs as **备用源** and shows the source, so degraded data cannot silently appear as a normal primary-data run. PIT fundamental enrichment is best-effort and cannot block the validated market-factor ranking if the financial provider is temporarily unavailable.

Complete canonical security-master observations are copied into checksumed 90-day workflow artifacts for strict rolling research. A missing archive does not silently become a reconstructed historical universe.

See `docs/production_pipeline.md` for the detailed production and storage rules.

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

- `.github/workflows/ci.yml`: lint, unit tests, CLI health and public-data smoke checks.
- `.github/workflows/daily.yml`: scheduled production universe, 90-day security-master research archive, history/factor run, PIT fundamental enrichment, research artifact and main-branch Pages deployment.
- `.github/workflows/pages.yml`: manual sample-dashboard preview; it queues behind production and does not cancel a production Pages run.

Daily workflow concurrency is scoped by branch, so feature validation cannot occupy the `main` production queue. Expensive push-triggered daily production is restricted to `main`; other refs can use manual workflow dispatch when a full production-path validation is needed.

## Data policy

Large historical datasets are **not committed to Git**. Rebuildable market/security-master caches live in GitHub Actions cache or `data/cache/` locally. Candidate handoffs and daily research outputs are short-lived workflow artifacts. Complete security-master observations additionally receive 90-day research artifacts so strict rolling experiments can use genuinely observed historical universes. GitHub Pages receives the generated static site; daily market data is not committed into repository history.

## Disclaimer

This repository is for quantitative research and software engineering. Outputs are research signals, not guaranteed investment returns or individualized investment advice.
