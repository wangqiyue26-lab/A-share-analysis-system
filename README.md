<div align="center">

# A-share Analysis System

### 个人 A 股量化研究、选股与前瞻验证平台

从公开市场数据开始，完成股票池构建、因子评分、Point-in-Time 基本面、A 股交易规则回测、云端日更与不可回改的影子实盘记录。

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.2.2-4C78A8)](./pyproject.toml)
[![CI](https://github.com/wangqiyue26-lab/A-share-analysis-system/actions/workflows/ci.yml/badge.svg)](https://github.com/wangqiyue26-lab/A-share-analysis-system/actions/workflows/ci.yml)
[![Daily Production](https://github.com/wangqiyue26-lab/A-share-analysis-system/actions/workflows/daily.yml/badge.svg)](https://github.com/wangqiyue26-lab/A-share-analysis-system/actions/workflows/daily.yml)
[![Research Status](https://img.shields.io/badge/status-Research%20%2B%20Shadow%20Trading-F2A900)](#当前状态)
[![Real Money](https://img.shields.io/badge/real__money__ready-false-B3261E)](#影子实盘与前瞻验证)

**[🌐 在线研究 Dashboard](https://wangqiyue26-lab.github.io/A-share-analysis-system/)** · **[📚 研究文档](./docs)** · **[⚙️ GitHub Actions](https://github.com/wangqiyue26-lab/A-share-analysis-system/actions)**

</div>

> [!IMPORTANT]
> 这个项目目前是**量化研究与影子实盘验证系统**，不是一个已经通过长期实盘检验的“自动荐股程序”。v2 已完成开发回测与 Phase 4J 稳健性初筛；当前进入 **Phase 4K：永久 Point-in-Time 股票池证据 + 严格 fail-closed walk-forward + current-universe 偏差对照**。2万元前瞻影子盘继续独立运行，`real_money_ready` 仍固定为 `false`。

---

## 目录

- [项目解决什么问题](#项目解决什么问题)
- [当前状态](#当前状态)
- [系统工作流](#系统工作流)
- [数据源与可靠性](#数据源与可靠性)
- [市场因子模型](#市场因子模型)
- [Point-in-Time 基本面](#point-in-time-基本面)
- [影子实盘与前瞻验证](#影子实盘与前瞻验证)
- [A 股交易规则建模](#a-股交易规则建模)
- [每日自动化](#每日自动化)
- [输出与研究证据](#输出与研究证据)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [Roadmap](#roadmap)
- [研究纪律](#研究纪律)
- [常见问题](#常见问题)
- [免责声明](#免责声明)

---

## 项目解决什么问题

大多数个人选股脚本只回答一个问题：**“今天谁的分数最高？”**

这个项目希望解决的是更完整的一条链路：

1. 数据从哪里来，数据源失效时怎么办；
2. 当天真实可交易的股票池是什么；
3. 哪些因子贡献了排名；
4. 财报是否在当时已经公开，而不是事后回填；
5. A 股 T+1、涨跌停、停牌、ST、手续费和滑点是否被正确模拟；
6. 每天真正产生过什么信号，能否防止事后修改历史；
7. 一个策略究竟是在历史上“拟合得漂亮”，还是能够持续通过前瞻验证。

因此，这个仓库更接近一个**个人量化研究基础设施**，而不是单一的选股公式。

### 当前已具备的能力

| 能力 | 状态 | 说明 |
| --- | :---: | --- |
| 多数据源行情路由 | ✅ | 新浪 / 东方财富 + 缓存与历史成交额降级链 |
| 股票池与流动性筛选 | ✅ | 上市状态、ST、上市天数、交易所、成交额检查 |
| 可解释市场因子 | ✅ | 动量、趋势、波动、回撤、流动性 |
| 横截面标准化与综合评分 | ✅ | Winsorize + z-score + 权重配置 |
| PIT 财务数据 | ✅ | 保留公告/可见时间与后续修订版本 |
| A 股交易规则回测 | ✅ | T+1、涨跌停、停牌、手续费、印花税、滑点 |
| 沪深300基准研究基础 | ✅ | 用于后续严格相对收益验证 |
| GitHub Actions 云端日更 | ✅ | 不依赖本地电脑持续开机 |
| GitHub Pages Dashboard | ✅ | 每日研究结果自动发布 |
| 永久 PIT 股票池证据 | 🟡 | 每日候选集永久锁定；Security Master 状态按内容哈希去重 |
| 前瞻影子实盘 v1 | ✅ | 第一轮实验已冻结，失败结果保留作为研究证据 |
| 2万元资本约束影子实盘 v2 | 🟡 | 周频调仓、最多3只个股、ETF/现金袖套、真实佣金 |
| 20交易日滚动开发回测 | 🟡 | 多历史窗口快速筛错；不替代严格 PIT 验证 |
| 严格 PIT walk-forward | 🟡 | 缺一天就 fail closed；满21个连续交易日后形成首个20日收益块 |
| 小资金实盘门槛 | 🔒 | 尚未开放 |
| ML / Qlib / LightGBM | ⏳ | 后续阶段，不急于提前引入 |

---

## 当前状态

当前系统已经能在 GitHub 云端自动完成一整套每日研究任务，但项目刻意区分了“**能运行**”与“**值得拿真钱运行**”。

```text
数据管线可用            ██████████  ✅
每日选股生产            ██████████  ✅
PIT 基本面              ██████████  ✅
交易规则回测            ██████████  ✅
v1 前瞻实验             ██████████  ✅ 已冻结并保留失败证据
v2 2万元影子实盘        ███░░░░░░░  🟡 新一轮前瞻验证
20日历史开发回测         ██████░░░░  🟡 自动化框架已建立
永久 PIT 证据账本       ███░░░░░░░  🟡 开始向前积累
严格 PIT walk-forward   ██░░░░░░░░  🟡 等待连续证据窗口
真实资金准备度          ░░░░░░░░░░  🔒 false
```

### 当前生产基线

- **市场范围**：沪深 A 股（当前生产候选池暂不含北交所）。
- **候选池**：每日构建 60 只高流动性沪深股票。
- **研究排名**：保留 Top10 解释层，但 v2 不再机械买入全部 Top10。
- **生产评分**：v2 使用更偏风险控制的可解释市场因子权重。
- **基本面因子**：继续作为补充解释层，暂不进入正式综合分。
- **v2 模拟资金**：¥20,000。
- **v2 个股仓位**：最多 3 只，每只目标约 25%，每 5 个交易日再平衡。
- **ETF / 现金**：独立袖套承接未使用仓位；趋势不合格时允许持有现金。
- **真实资金状态**：`real_money_ready = false`。

> 60 只仍是零付费公网数据和 GitHub 云端可靠性之间的阶段性折中，不代表最终研究范围已经充分。

---

## 系统工作流

```mermaid
flowchart TD
    A[交易所 Security Master] --> B[可交易股票池校验]
    S1[新浪全市场快照] --> C{多源数据路由}
    S2[东方财富全市场快照] --> C
    S3[最近已验证候选缓存] --> C
    S4[近期历史成交额重建] --> C

    B --> C
    C --> D[60只候选池]
    D --> E[逐股日线历史数据]
    E --> F[统一 Bar Schema + Parquet Cache]
    F --> G[市场因子计算]
    G --> H[Winsorize + Cross-sectional Z-score]
    H --> I[加权综合分 + Top10]

    P[PIT 财务报表] --> Q[公告时间 / 修订版本约束]
    Q --> R[成长 / 质量 / 杠杆因子]
    R --> J[基本面补充页]

    I --> K[每日研究 Dashboard]
    D --> PIT[永久 PIT Candidate Ledger]
    B --> PIT
    PIT --> STRICT[Strict PIT Walk-forward]
    I --> L[不可回改 Shadow Signal]
    L --> M[下一交易日模拟执行]
    M --> N[收益 / 回撤 / 胜率 / 成本证据]

    K --> O[GitHub Pages]
    J --> O
    N --> O
```

这条链路最重要的设计原则是：**研究结果必须能够解释、复现、追溯，并且尽量避免未来数据泄漏。**

---

## 数据源与可靠性

免费公网 A 股数据的最大问题不是“没有数据”，而是**云端运行时端点会随机超时、限流或断开连接**。因此系统没有押注单一数据源。

### 当前候选池数据路由

```text
一级实时源 A：新浪全市场行情
        ↓ 失败 / 覆盖不足
一级实时源 B：东方财富全市场行情
        ↓ 失败 / 覆盖不足
最近 3 天已验证候选缓存（重新经过 security-master 校验）
        ↓ 不可用
当前股票池 + 逐股近期历史成交额重建
        ↓ 极端故障
显式灾难级 fallback
```

### 数据质量门槛

实时全市场接口并不是“返回 HTTP 200 就算成功”。当前生产还会检查：

- 沪深当前上市证券覆盖率；
- ST 状态；
- 上市天数；
- 交易所范围；
- 成交额完整性；
- Security Master 是否完整；
- 行情日期是否一致；
- 缓存是否已经过期。

全市场实时快照覆盖不足时，即使接口本身有返回，也会被拒绝作为正常一级源。

### 为什么保留缓存和历史重建

免费网页行情接口本身没有 SLA。系统的目标不是假装它们永远可靠，而是做到：

> **单个数据端点故障，不应该直接导致整个每日研究任务失败；但降级数据也不能被悄悄伪装成正常数据。**

因此 Dashboard 和 manifest 会保留数据源、warning 与 fallback 状态，方便后续审计。

---

## 市场因子模型

当前正式生产评分故意保持简单、透明、可解释。

| 因子 | 含义 | 默认方向 |
| --- | --- | :---: |
| `momentum_20` | 20 个交易日价格动量 | ↑ 越高越好 |
| `momentum_60` | 60 个交易日价格动量 | ↑ 越高越好 |
| `trend_ma20_ma60` | MA20 / MA60 趋势差 | ↑ 越高越好 |
| `volatility_20` | 20 日年化实现波动率 | ↓ 越低越好 |
| `max_drawdown_60` | 过去 60 日最大回撤幅度 | ↓ 越低越好 |
| `log_amount_20` | 20 日平均成交额的对数 | ↑ 流动性更高 |

因子先经过极值处理，再进行横截面 z-score 标准化。v1 权重保留在 [`config/factors.yml`](./config/factors.yml)，当前 v2 生产使用 [`config/factors_v2.yml`](./config/factors_v2.yml)：动量/趋势权重从 55% 降到 40%，波动率与最大回撤合计提高到 50%，并记录 close/MA20 状态作为资本约束组合的入场与续持门槛。

系统同时保留：

- 原始因子值；
- 标准化后因子值；
- 综合分；
- 排名；
- 因子覆盖率；
- 被排除股票与原因。

这样可以在结果异常时回答“**为什么这只股票排在这里**”，而不是只看到一个无法解释的模型分数。

---

## Point-in-Time 基本面

财务数据最容易出现的研究错误之一，是用“今天知道的财报”去解释“过去当时不知道的市场”。

本项目的 PIT 财务层要求每条事实至少具备：

- `period_end`：财报对应报告期；
- `published_at / available_at`：该信息什么时候真实可见；
- 修订版本：后来修订的数据不能覆盖掉早期可见版本；
- `source`：来源；
- 缓存与过期策略。

当前补充因子包括：

| 基本面因子 | 说明 |
| --- | --- |
| 营收同比 | 收入增长质量的基础观察 |
| 净利润同比 | 盈利增长 |
| 净利率 | 盈利能力 |
| 经营现金流 / 净利润 | 盈利与现金流匹配程度 |
| 资产负债率 | 财务杠杆 |
| Fundamental Coverage | 当前时点可计算因子的覆盖率 |

这些基本面因子已经显示在独立页面中，但**暂不进入正式生产综合分**。只有在积累足够严格历史股票池快照和多个财报周期后，才会验证其是否具有真实的样本外增量价值。

进一步阅读：[`docs/pit_fundamental_enrichment.md`](./docs/pit_fundamental_enrichment.md) · [`docs/strict_rolling_research.md`](./docs/strict_rolling_research.md)

---

## 影子实盘与前瞻验证

第一轮 Phase 4H 的百万资金 Top10 日频等权模拟已经完成其最重要的任务：**暴露系统问题，而不是证明系统赚钱**。v1 的不可回改账本继续保留在 `research/shadow/signals/`，不会因为策略升级而删除或重算。

### Phase 4I：2万元资本约束 v2

| 项目 | v2 规则 |
| --- | --- |
| 初始虚拟资金 | ¥20,000 |
| 个股数量 | 最多 3 只 |
| 单只目标权重 | 约 25% |
| 个股总权重 | 最多 75% |
| 最低现金 | 10% |
| 调仓频率 | 每 5 个交易日 |
| 新进排名 | Top5 |
| 持有缓冲 | 排名仍在 Top10 且趋势门槛通过可继续持有 |
| 最低持有期 | 5 个交易日 |
| 可买性 | 目标仓位必须买得起至少 1 手 |
| ETF | 独立趋势袖套，不与个股因子直接混排 |
| 最早成交 | 收盘信号后的下一交易日 |
| 真实资金状态 | `false` |

v2 使用用户实际账户费用模型：股票 1 万元以内佣金 5 元；超过 1 万元的部分按万 0.86，股票过户费和卖出印花税单独计入。ETF 买卖佣金均为万 0.5、单笔最低 0.5 元，无印花税、无过户费及其他显性费用。股票与 ETF 均继续单独计入 5 bps 模拟滑点。

v2 每日排名证据保存在：

```text
research/shadow_v2/rankings/YYYY-MM-DD.csv
```

这些文件同时保存综合分、20日动量、收盘价与 close/MA20 趋势状态，因此以后可以审计“当时为什么允许买入/续持”。

### 20交易日滚动开发回测

为了避免每次策略修改都现实等待 20 个交易日，Phase 4I 新增快速历史实验：

```text
历史数据 → 多个互不重叠的20交易日窗口
         → 收益 / 沪深300超额 / 最大回撤
         → 换手 / 交易笔数 / 费用 / 滑点
         → 版本比较与淘汰
```

这条轨道可以一次评估很多 20 日区间，加快发现设计错误。但当前快速历史实验使用“当前可观察候选池”回放更早历史，因此存在 survivorship bias，只能作为**开发回测**，不能冒充严格 PIT 证据。

真正的证据层次是：

1. 历史 20 日开发回测：快速筛错；
2. 严格 PIT / 历史股票池验证：防止未来数据与幸存者偏差；
3. 新的 v2 前瞻影子盘：最终样本外检验。

详细规则见 [`docs/phase4i_capital_aware_validation.md`](./docs/phase4i_capital_aware_validation.md)。

---

## A 股交易规则建模

回测和影子实盘尽量避免使用“理论上可以买卖”的理想化假设。

当前交易引擎已经处理：

- **T+1**：当天买入不能当天卖出；
- **涨跌停**：不同板块使用对应价格限制；
- **创业板 / 科创板 / 主板差异**；
- **ST 价格限制**；
- **停牌 / 零成交量**：订单延后；
- **100 股整数手**；
- **佣金最低收费**；
- **历史印花税规则**；
- **滑点**；
- **订单无法成交时延后重试**。

策略信号与真实成交被分开：收盘后的信号只能在下一交易日及以后执行，这也是防止未来函数的重要边界。

---

## 每日自动化

系统的生产环境是 **GitHub Actions**，因此日常运行不要求本地电脑保持开机。

### 生产时间

主工作流在工作日中国 A 股收盘后运行。目前 cron 为：

```yaml
23 9 * * 1-5
```

即 **09:23 UTC / 北京时间 17:23**。

### 三类自动化任务

| Workflow | 作用 |
| --- | --- |
| `.github/workflows/ci.yml` | Ruff、单元测试、CLI health、真实公网 smoke test |
| `.github/workflows/daily.yml` | 60只股票池 → v2 因子 → PIT 基本面 → 2万元 Shadow v2 → Pages |
| `.github/workflows/v2-validation.yml` | 历史多窗口 20 交易日开发回测与版本淘汰 |
| `.github/workflows/v2-robustness.yml` | v2 滑点压力、时间外与收益集中度测试 |
| `.github/workflows/strict-pit-v2.yml` | 永久 PIT 账本驱动的 fail-closed 严格 walk-forward |
| `.github/workflows/pages.yml` | 手工预览 / Pages 辅助任务 |

普通代码 `push` 可以验证生产链，但**不会写入正式影子交易信号**。只有主分支的定时/手工正式运行才允许锁定模拟交易证据，避免开发行为污染前瞻样本。

---

## 输出与研究证据

一次完整生产会产生多层输出。

### 每日选股

```text
reports/production/selection/
├── ranking.csv
├── ranking.json
├── selected.csv
├── selected.json
├── exclusions.csv
├── summary.json
├── fundamentals.csv
└── fundamental_summary.json
```

### 影子实盘 v2

```text
reports/production/shadow_v2/
├── equity_curve.csv
├── trades.csv
├── portfolio_signals.csv
└── metrics.json
```

### 长期证据

```text
research/shadow/signals/       # 冻结的 v1 决策证据
research/shadow_v2/rankings/   # 当前 v2 不可回改排名证据
research/pit/
├── days/                      # 每个交易日的 provenance / checksum
├── candidates/                # 当天实际使用的60只候选股
└── security_master/states/    # 按内容哈希去重的主档状态
```

### 20日开发回测

```text
reports/v2-validation/
├── rolling_20d.csv
├── equity_curve.csv
├── trades.csv
├── signals.csv
└── summary.json
```

此外，完整 Security Master 仍保留 workflow artifact 备份；Phase 4K 进一步把真正用于严格研究的候选集和主档状态写入永久、不可回改的 Git 证据账本。历史缺口不会用今天的股票池补齐。

### GitHub Pages

在线 Dashboard：

**https://wangqiyue26-lab.github.io/A-share-analysis-system/**

页面会展示每日研究排名、数据源状态和 PIT 基本面；影子实盘形成正式记录后，会继续展示模拟组合表现与验证阶段。

---

## 项目结构

```text
A-share-analysis-system/
├── .github/workflows/        # CI、每日生产、Pages
├── config/                   # 因子权重与系统配置
├── data/                     # 本地/Actions 缓存目录（大数据不进 Git）
├── docs/                     # 生产、PIT、严格滚动、Shadow 等研究文档
├── reports/                  # 运行时研究产物
├── research/shadow/signals/  # 冻结的 v1 前瞻证据
├── research/shadow_v2/       # v2 2万元前瞻排名证据
├── research/pit/             # 永久 PIT universe 证据账本
├── src/ashare_system/
│   ├── backtest/             # A 股交易规则、成本、回测引擎、绩效指标
│   ├── data/                 # 行情、财务、Security Master、PIT、缓存、路由
│   ├── factors/              # 市场因子与基本面因子
│   ├── production.py         # 每日选股生产
│   ├── robust_universe.py    # 多源候选池路由
│   ├── shadow_trading.py     # 冻结的 v1 前瞻影子实盘
│   ├── shadow_v2.py          # 2万元资本约束前瞻实盘
│   ├── capital_aware.py      # 少持仓 / 周频 / ETF / 可买性组合构造
│   ├── rolling_validation.py # 20交易日窗口评估
│   ├── experiment_v2.py      # 快速历史开发回测
│   ├── pit_evidence.py       # 不可回改 PIT 候选池 / 主档证据
│   ├── strict_pit_v2.py      # fail-closed 严格 PIT walk-forward
│   ├── pit_bias_report.py    # current-universe vs strict PIT 偏差对照
│   ├── enrich_daily.py       # PIT 基本面增强
│   ├── reporting.py          # 研究输出
│   └── site.py               # 静态 Dashboard
├── tests/                    # 单元测试 / 回归测试
├── pyproject.toml
└── README.md
```

---

## 快速开始

要求 Python **3.11+**。

```bash
git clone https://github.com/wangqiyue26-lab/A-share-analysis-system.git
cd A-share-analysis-system

python -m pip install -e ".[dev]"
```

运行测试：

```bash
ruff check src tests
pytest -q
```

检查 CLI：

```bash
ashare-system health
```

真实公网行情 smoke test：

```bash
ashare-system smoke-data \
  --symbol 000001 \
  --start 20240102 \
  --end 20240110
```

> 网络 smoke test 会真实访问公开 A 股数据接口，因此它反映的是“代码 + 当前外部数据源”的联合状态；普通单元测试不依赖外部网络。

---

## Roadmap

| 阶段 | 状态 | 内容 |
| --- | :---: | --- |
| Phase 1 — Foundation | ✅ | 数据接口、统一格式、缓存、配置、日志、CI |
| Phase 2A — Market Factor Engine | ✅ | 股票池、技术/市场因子、标准化、综合评分 |
| Phase 3 — China Backtest Foundation | ✅ | T+1、涨跌停、停牌、ST、成本、PIT 基础设施、基准 |
| Phase 4B — Cloud Daily Production | ✅ | 云端候选池、Top10、Dashboard、Pages |
| Phase 4C — PIT Fundamentals | ✅ | 公告时间约束的财务事实、修订版本与因子 |
| Phase 4D — Fundamental Research Layer | ✅ | Top10 基本面补充页与失败隔离 |
| Phase 4F — Strict Rolling Evidence | ✅ | Security Master 快照与严格历史证据 |
| Phase 4G — Resilient Data Routing | ✅ | 新浪 / 东方财富一级源与多层 fallback |
| Phase 4H — Forward Shadow Trading v1 | ✅ | 第一轮前瞻实验已冻结，失败证据永久保留 |
| Phase 4I — Capital-aware Validation | 🟡 | 2万元、60只候选池、少持仓、ETF/现金、真实费用、20日滚动开发回测 |
| Phase 4J — Robustness Suite | ✅ | v2 通过滑点压力与2023–2024时间外初筛，但仍存在 current-universe bias |
| Phase 4K — Strict PIT Validation | 🟡 | 永久候选池证据、主档状态去重、缺口 fail closed、同期 bias comparison |
| Strict Rolling Validation | 🟡 | 首个完整严格20日窗口需等待连续 PIT 证据积累 |
| Portfolio Risk Layer | ⏳ | 仓位、集中度、现金比例、组合回撤与暂停机制 |
| Real-money Readiness Gate | 🔒 | 达到硬性门槛后才考虑小资金真实测试 |
| Phase 5 — ML Lab | ⏳ | Qlib / LightGBM / Walk-forward / 模型集成 |

机器学习被放在后面是刻意的：**先证明数据、回测、执行和前瞻证据可靠，再增加模型复杂度。**

---

## 研究纪律

这个项目有几条比“某个因子权重是多少”更重要的原则。

### 1. 不用未来股票池重建过去

Phase 4K 从永久 `research/pit/` 账本读取当天实际候选集；如果基准交易日历中缺少任何一天的 exact candidate evidence，严格回测直接返回 `evidence_gap`。没有真实留存证据的历史日期保持不可验证，不拿今天的股票名单补齐。

### 2. 不用未来财报解释过去

财务事实按 `available_at` 控制可见时间，并保留后续修订版本。

### 3. 不为了短期盈利修改历史

每日 Shadow Signal 一旦锁定，就不能被新排名覆盖。

### 4. 不把数据源故障隐藏起来

实时源、缓存、历史重建等状态必须显式记录。

### 5. 不把复杂度当作优势

在简单市场因子和执行链没有通过足够前瞻验证之前，不急于上线 ML 黑箱。

### 6. 不用单一指标判断策略

收益率之外，还必须同时看最大回撤、换手率、成本、波动、基准超额、行业集中度和不同市场阶段的稳定性。

---

## 常见问题

<details>
<summary><strong>这个系统现在会直接告诉我买哪只股票吗？</strong></summary>

会产生每日 Top10 **研究排名**，但当前不把它定义为真实资金买入建议。系统正在通过影子实盘积累前瞻证据，真实资金门槛仍然关闭。

</details>

<details>
<summary><strong>为什么现在使用 60 只候选股票？</strong></summary>

第一轮 30 只候选池过窄，容易把“高成交热门股”偏好再次放大。v2 将范围扩大到 60 只，同时仍控制 GitHub Runner 的逐股历史请求规模。60 只仍是阶段性工程折中，不是最终研究宇宙。

</details>

<details>
<summary><strong>为什么不用收费数据？</strong></summary>

当前目标是在零额外行情数据成本下先证明研究流程本身是否有效，因此优先使用公开数据、多源路由、缓存和显式降级。若未来策略本身经过验证，再评估是否有必要为更高 SLA 的数据服务付费。

</details>

<details>
<summary><strong>为什么基本面已经有了，却不直接放进综合评分？</strong></summary>

“有因子”不等于“因子有增量价值”。基本面必须在严格 PIT 数据和足够多报告周期上完成样本外验证，确认不会增加过拟合、行业偏置或换手问题后，才可能进入正式评分。

</details>

<details>
<summary><strong>影子实盘盈利后是不是就可以直接上真钱？</strong></summary>

不是。影子收益只是证据的一部分。真正的实盘准备度还需要更广股票池、基准相对表现、最大回撤、成本、换手、市场环境稳定性和组合风险规则共同通过门槛。

</details>

<details>
<summary><strong>需要每天开着自己的电脑吗？</strong></summary>

不需要。生产任务在 GitHub Actions 上运行，研究结果通过 GitHub Pages 发布。本地环境主要用于开发和手工调试。

</details>

---

## 进一步阅读

- [`docs/production_pipeline.md`](./docs/production_pipeline.md) — 云端生产与存储规则
- [`docs/pit_fundamental_enrichment.md`](./docs/pit_fundamental_enrichment.md) — PIT 基本面增强
- [`docs/strict_rolling_research.md`](./docs/strict_rolling_research.md) — 严格滚动研究边界
- [`docs/shadow_trading.md`](./docs/shadow_trading.md) — v1 前瞻影子交易协议
- [`docs/phase4i_capital_aware_validation.md`](./docs/phase4i_capital_aware_validation.md) — 2万元 v2 与20日快速验证协议
- [`docs/phase4j_v2_robustness.md`](./docs/phase4j_v2_robustness.md) — v2 滑点压力、时间外与收益集中度检验
- [`docs/phase4k_strict_pit.md`](./docs/phase4k_strict_pit.md) — 永久 PIT 证据、fail-closed 严格回测与偏差对照
- [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md) — 第三方组件说明

---

## 数据与存储策略

大规模行情历史**不提交进 Git**。

- 可重建的行情与财务数据：GitHub Actions cache / `data/cache/`；
- 每日中间产物：短期 workflow artifacts；
- 完整 Security Master artifact：继续作为短中期备份；
- `research/pit/candidates/`：永久保存每天实际使用的60只候选；
- `research/pit/security_master/states/`：仅在状态变化时新增内容寻址主档；
- GitHub Pages：只发布生成后的静态研究页面；
- `research/shadow/signals/` / `research/shadow_v2/`：保存不可回改的前瞻决策证据。

这种设计尽量在**仓库体积、可复现性、免费云端运行成本和研究严谨性**之间取得平衡。

---

## 免责声明

本项目用于**量化研究、软件工程实验与策略验证**。

项目输出是研究信号，不构成证券投资建议、收益承诺或任何形式的保证。历史回测和模拟交易均不能保证未来表现。A 股交易存在市场风险、流动性风险、模型风险、数据质量风险和实际成交偏差；任何真实资金决策都应由使用者自行判断并承担结果。

---

<div align="center">

**Build evidence first. Add complexity later.**

先让系统证明自己，再决定是否值得承担真实资金风险。

</div>
