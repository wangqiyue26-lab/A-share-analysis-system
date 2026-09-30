# A-share Analysis System

个人 A 股量化研究与选股系统。目标是构建一套可在 GitHub Actions 云端自动运行的研究平台，而不是单一“荐股脚本”。

## Roadmap

- ✅ **Phase 1 — Foundation**：多端点数据接口、统一数据格式、缓存、配置、日志、CI、云端 smoke test。
- ✅ **Phase 2A — Market Factor Engine**：股票池、市场/技术因子、标准化、综合评分与可解释排名。
- ✅ **Phase 3 — China Backtest Foundation**：T+1、涨跌停、停牌、ST/退市、费用/滑点、历史股票池、Point-in-Time 基础设施与沪深300基准报告。
- ✅ **Phase 4B — Cloud Daily Production**：真实 A 股候选池、自动降级、每日历史更新、Top10、研究产物、静态 Dashboard、GitHub Pages 发布链。
- ✅ **Phase 4C — Point-in-Time Fundamentals**：公告时间约束的财务事实缓存、修订版本保留、成长/质量/杠杆因子与真实公网校验。
- ✅ **Phase 4D — Daily Fundamental Research Layer**：Top10 自动补充 PIT 基本面、独立 Dashboard、财务缓存与失败隔离；暂不改变正式市场因子排名。
- ✅ **Phase 4F — Strict Rolling Evidence**：持续留存实际观察到的 security-master 快照，为严格滚动股票池和组合研究积累可追溯证据。
- ✅ **Phase 4G — Resilient Market Data Routing**：新浪/东方财富一级实时源、完整性门槛、已验证缓存、近期成交额重建与显式降级状态。
- 🚧 **Phase 4H — Forward Shadow Trading**：每天锁定首次观察到的 Top10，下一交易日执行虚拟组合，记录收益、回撤、日胜率、交易成本和不可改写的前瞻证据。
- ⏭️ **Next — Strict Rolling Validation**：扩大股票池，在历史快照、PIT 财务和影子实盘证据基础上验证基准超额、行业偏置、换手/成本和不同市场阶段表现，再决定是否进入小资金实盘门槛。
- **Phase 5 — ML Lab**：Qlib/LightGBM、Walk-forward、特征重要性、模型集成。

## Current architecture

```text
Exchange security master ──────────────────────────────────────────────┐
        │                                                             │
        └─ append-only snapshots + 90-day research artifacts          │
                                                                      │
Sina all-A spot ──────┐                                               │
Eastmoney all-A spot ─┼─ validated live routing + explicit fallback ──┤
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
                                                          ├──────────────┐
                                                          ↓              │
                                                static research page      │
                                                          │              │
PIT financial statements ─ revision-safe cache ─ factors ─┤              │
                                                          ↓              │
                                         supplemental fundamentals page   │
                                                                         ↓
                                                           immutable shadow signals
                                                                         ↓
                                                   next-day paper execution
                                                                         ↓
                                            return / drawdown / win-rate evidence
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

## Forward shadow trading

Scheduled post-close runs now form the evidence layer for future real-money readiness. The baseline uses Top10 equal weights and CNY 1,000,000 of simulated capital. A signal observed after the close may execute no earlier than the next trading-day open through the same conservative A-share execution model used by the backtester.

Each daily signal is stored under `research/shadow/signals/YYYY-MM-DD.csv`. Once a market-date signal has been persisted, a rerun may reuse an identical file but cannot overwrite it with a different ranking. Large market histories are still excluded from Git; only the small decision ledger is retained for auditability.

The first 20 observed return days are evidence collection only. Short-term wins or losses do not trigger factor-weight changes. At 20 days the system may perform a first diagnostic review; 60+ observed days are required before more serious rolling validation. `real_money_ready` remains false during Phase 4H.

See `docs/shadow_trading.md`.

## Cloud production behavior

The scheduled pipeline runs after the China A-share close on GitHub-hosted runners. It currently uses a bounded 30-name SSE/SZSE candidate pool and selects the Top10 research ranking. This is an endpoint-stability starting point, not a claim that 30 names are sufficient for final research.

Market-wide public endpoints can be unstable from cloud IPs. The universe stage therefore validates independent Sina and Eastmoney live snapshots and can degrade through a recent revalidated candidate cache and then a bounded history-liquidity path based on actual traded amount. Degraded current-universe data is never written as a canonical historical security-master snapshot.

The dashboard visibly marks fallback runs and shows the source, so degraded data cannot silently appear as a normal live-data run. PIT fundamental enrichment is best-effort and cannot block the validated market-factor ranking if the financial provider is temporarily unavailable.

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
- `.github/workflows/daily.yml`: scheduled production universe, 90-day security-master research archive, history/factor run, PIT fundamental enrichment, scheduled shadow portfolio, research artifacts and main-branch Pages deployment.
- `.github/workflows/pages.yml`: manual sample-dashboard preview; it queues behind production and does not cancel a production Pages run.

Daily workflow concurrency is scoped by branch, so feature validation cannot occupy the `main` production queue. Expensive push-triggered daily production is restricted to `main`; forward shadow signals are persisted only by post-close schedule/manual production runs, not ordinary source-code pushes.

## Data policy

Large historical datasets are **not committed to Git**. Rebuildable market/security-master caches live in GitHub Actions cache or `data/cache/` locally. Candidate handoffs and daily research outputs are short-lived workflow artifacts. Complete security-master observations additionally receive 90-day research artifacts so strict rolling experiments can use genuinely observed historical universes.

The exception is the compact shadow decision ledger under `research/shadow/signals/`: it records only the daily selected symbols, ranks, scores, weights and first-observed timestamp so forward results can be audited without hindsight edits. GitHub Pages receives the generated static site; daily market-bar histories are not committed into repository history.

## Disclaimer

This repository is for quantitative research and software engineering. Outputs are research signals, not guaranteed investment returns or individualized investment advice.
