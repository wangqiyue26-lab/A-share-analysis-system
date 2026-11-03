from __future__ import annotations

import argparse
import html
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .backtest import BacktestEngine, TradingRuleSet
from .capital_aware import (
    capital_aware_stock_signals,
    combine_portfolio_signals,
    etf_rotation_signals,
    load_capital_aware_config,
)
from .config import load_yaml
from .data.cache import ParquetBarCache
from .data.etf import AkshareEtfProvider
from .production import _fetch_one_history

STRATEGY_VERSION = "capital-aware-v2"
_START = "<!-- SHADOW-V2-START -->"
_END = "<!-- SHADOW-V2-END -->"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def capture_ranking(
    selected_file: str | Path,
    ledger_root: str | Path,
    *,
    observed_at: pd.Timestamp | None = None,
) -> Path:
    selected = pd.read_csv(selected_file, dtype={"symbol": str})
    required = {
        "symbol",
        "as_of",
        "board",
        "is_st",
        "rank",
        "composite_score",
        "close",
        "momentum_20",
        "trend_close_ma20",
    }
    missing = sorted(required - set(selected.columns))
    if missing:
        raise ValueError(f"selected.csv missing v2 ranking columns: {missing}")
    selected["symbol"] = selected["symbol"].astype(str).str.zfill(6)
    selected["signal_date"] = pd.to_datetime(selected["as_of"]).dt.normalize()
    dates = selected["signal_date"].drop_duplicates()
    if len(dates) != 1:
        raise ValueError("selected.csv must contain exactly one market date")

    observed = observed_at if observed_at is not None else pd.Timestamp.now(tz="UTC")
    if observed.tzinfo is None:
        observed = observed.tz_localize("UTC")
    else:
        observed = observed.tz_convert("UTC")
    selected["observed_at"] = observed.isoformat()
    selected["strategy_version"] = STRATEGY_VERSION
    if "name" not in selected:
        selected["name"] = ""
    if "screen_source" not in selected:
        selected["screen_source"] = "unknown"

    columns = [
        "signal_date",
        "symbol",
        "name",
        "board",
        "is_st",
        "rank",
        "composite_score",
        "close",
        "momentum_20",
        "trend_close_ma20",
        "screen_source",
        "strategy_version",
        "observed_at",
    ]
    ranking = selected.loc[:, columns].sort_values(["rank", "symbol"]).reset_index(drop=True)
    date = pd.Timestamp(ranking["signal_date"].iloc[0]).date().isoformat()
    output = Path(ledger_root) / "rankings" / f"{date}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists():
        existing = pd.read_csv(output, dtype={"symbol": str})
        compare_columns = [column for column in columns if column != "observed_at"]
        left = existing.loc[:, compare_columns].copy()
        right = ranking.loc[:, compare_columns].copy()
        left["signal_date"] = pd.to_datetime(left["signal_date"])
        right["signal_date"] = pd.to_datetime(right["signal_date"])
        try:
            pd.testing.assert_frame_equal(
                left.reset_index(drop=True),
                right.reset_index(drop=True),
                check_dtype=False,
                check_exact=False,
                rtol=1e-12,
                atol=1e-12,
            )
        except AssertionError as exc:
            raise RuntimeError(
                f"Refusing to rewrite immutable v2 ranking for {date}"
            ) from exc
        return output

    ranking.to_csv(output, index=False, date_format="%Y-%m-%d")
    return output


def load_rankings(ledger_root: str | Path) -> dict[pd.Timestamp, pd.DataFrame]:
    files = sorted((Path(ledger_root) / "rankings").glob("*.csv"))
    if not files:
        raise FileNotFoundError("No v2 ranking snapshots found")
    result: dict[pd.Timestamp, pd.DataFrame] = {}
    for path in files:
        frame = pd.read_csv(path, dtype={"symbol": str})
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.normalize()
        date = pd.Timestamp(frame["signal_date"].iloc[0]).normalize()
        result[date] = frame
    return result


def _fetch_stock_bars(
    rankings: dict[pd.Timestamp, pd.DataFrame],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_root: Path,
    workers: int,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]]]:
    symbols = sorted(
        {
            str(symbol).zfill(6)
            for frame in rankings.values()
            for symbol in frame["symbol"].tolist()
        }
    )
    bars: dict[str, pd.DataFrame] = {}
    warnings: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(len(symbols), 1))) as pool:
        futures = [
            pool.submit(
                _fetch_one_history,
                symbol,
                start=start,
                end=end,
                cache_root=cache_root,
                adjust="",
            )
            for symbol in symbols
        ]
        for future in as_completed(futures):
            symbol, frame, provider, warning = future.result()
            if frame is None:
                raise RuntimeError(f"Missing v2 stock history for {symbol}: {warning}")
            bars[symbol] = frame
            if warning:
                warnings.append(
                    {"symbol": symbol, "provider": provider or "unknown", "warning": warning}
                )
    return bars, warnings


def _fetch_one_etf(
    symbol: str,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_root: Path,
) -> tuple[str, pd.DataFrame | None, str | None]:
    cache = ParquetBarCache(cache_root / "etf_bars_raw")
    cached: pd.DataFrame | None = None
    try:
        cached = cache.load(symbol)
    except FileNotFoundError:
        pass

    fetch_start = start
    if cached is not None and not cached.empty:
        cached_min = pd.Timestamp(cached["trade_date"].min()).normalize()
        cached_max = pd.Timestamp(cached["trade_date"].max()).normalize()
        if cached_min <= start:
            fetch_start = max(start, cached_max + pd.Timedelta(days=1))

    if cached is None or cached.empty or fetch_start <= end:
        try:
            incoming = AkshareEtfProvider().get_daily_bars(
                symbol,
                fetch_start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                "",
            )
            cache.upsert(symbol, incoming)
            cached = cache.load(symbol)
        except Exception as exc:  # noqa: BLE001 - ETF sleeve may fall back to cash
            if cached is None or cached.empty:
                return symbol, None, f"{type(exc).__name__}: {exc}"
            warning = f"refresh_failed_using_cache: {type(exc).__name__}: {exc}"
            return symbol, cached, warning

    return symbol, cached, None


def _fetch_etf_bars(
    portfolio_config_path: str | Path,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_root: Path,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]]]:
    payload = load_yaml(portfolio_config_path)
    pool = payload.get("etf_sleeve", {}).get("pool", [])
    bars: dict[str, pd.DataFrame] = {}
    warnings: list[dict[str, str]] = []
    for item in pool:
        symbol = str(item["symbol"]).zfill(6)
        returned, frame, warning = _fetch_one_etf(
            symbol,
            start=start,
            end=end,
            cache_root=cache_root,
        )
        if frame is not None and not frame.empty:
            bars[returned] = frame
        if warning:
            warnings.append({"symbol": returned, "warning": warning})
    return bars, warnings


def _render_page(
    metrics: dict[str, object],
    signals: pd.DataFrame,
    output: Path,
) -> None:
    latest_date = str(metrics.get("latest_ranking_date", ""))
    latest = signals[signals["target_weight"] > 0].copy()
    if not latest.empty:
        date = latest["trade_date"].max()
        latest = latest[latest["trade_date"] == date]
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['symbol']))}</td>"
        f"<td>{html.escape(str(row['instrument_type']))}</td>"
        f"<td>{float(row['target_weight']) * 100:.1f}%</td>"
        "</tr>"
        for _, row in latest.iterrows()
    )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>2万元影子实盘 v2</title><style>
body{{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;background:#f4f6f8;color:#17212b;margin:0}}
main{{width:min(1000px,calc(100% - 24px));margin:0 auto;padding:24px 0 50px}}
section{{background:white;border:1px solid #dce2e8;border-radius:14px;padding:18px;margin-bottom:14px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.metric{{background:white;border:1px solid #dce2e8;border-radius:12px;padding:14px}}
.metric span{{display:block;color:#65717d;font-size:12px}}.metric strong{{font-size:20px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #e4e8ec;text-align:left}}
.notice{{border-left:4px solid #d59a42;background:#fffaf1;padding:12px}}
</style></head><body><main>
<section><a href="index.html">← 返回主排名</a><h1>2万元影子实盘 · v2</h1>
<p>低换手、资本约束、真实佣金与 ETF/现金袖套的第二轮前瞻实验。旧版 v1 账本保持冻结，不与本轮混合。</p></section>
<div class="metrics">
<div class="metric"><span>初始资金</span><strong>¥{float(metrics.get('initial_cash', 0)):,.0f}</strong></div>
<div class="metric"><span>累计收益</span><strong>{float(metrics.get('total_return', 0) or 0)*100:.2f}%</strong></div>
<div class="metric"><span>最大回撤</span><strong>{float(metrics.get('max_drawdown', 0) or 0)*100:.2f}%</strong></div>
<div class="metric"><span>观察收益日</span><strong>{int(metrics.get('observed_return_days', 0) or 0)}</strong></div>
<div class="metric"><span>交易笔数</span><strong>{int(metrics.get('trade_count', 0) or 0)}</strong></div>
</div>
<section><div class="notice">本页仍属于研究模式。历史20日窗口用于快速开发，前瞻影子盘用于最终验证；二者不能互相替代。real_money_ready 始终为 false。</div></section>
<section><h2>最近一次组合目标</h2><p>最新排名日期：{html.escape(latest_date)}</p>
<table><thead><tr><th>代码</th><th>资产类型</th><th>目标权重</th></tr></thead><tbody>{rows}</tbody></table></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


def _patch_index(index_path: Path, metrics: dict[str, object]) -> None:
    if not index_path.exists():
        return
    block = f"""{_START}
<section class="panel" id="shadow-v2">
<h2>2万元影子实盘 v2</h2>
<div class="subtitle">第二轮资本约束实验：初始资金 ¥20,000，周频再平衡，个股最多3只，ETF/现金可承接未使用仓位。当前累计排名证据 {int(metrics.get('ranking_dates', 0) or 0)} 日。</div>
<div class="notice"><a href="shadow_v2.html">查看 v2 模拟组合 →</a></div>
</section>
{_END}"""
    text = index_path.read_text(encoding="utf-8")
    if _START in text and _END in text:
        prefix, tail = text.split(_START, 1)
        _, suffix = tail.split(_END, 1)
        text = prefix + block + suffix
    else:
        text = text.replace("</main>", block + "\n</main>", 1)
    index_path.write_text(text, encoding="utf-8")


def run_shadow_v2_pipeline(
    *,
    selected_file: str | Path = "reports/production/selection/selected.csv",
    ledger_root: str | Path = "research/shadow_v2",
    output_dir: str | Path = "reports/production/shadow_v2",
    site_dir: str | Path = "site",
    cache_root: str | Path = "data/cache",
    portfolio_config: str | Path = "config/portfolio_v2.yml",
    workers: int = 2,
) -> dict[str, object]:
    ranking_file = capture_ranking(selected_file, ledger_root)
    rankings = load_rankings(ledger_root)
    config = load_capital_aware_config(portfolio_config)
    dates = sorted(rankings)
    market_as_of = dates[-1]
    start = dates[0]

    stock_signals = capital_aware_stock_signals(rankings, config=config)
    rebalance_dates = dates[:: config.rebalance_every_trading_days]

    stock_bars, stock_warnings = _fetch_stock_bars(
        rankings,
        start=start,
        end=market_as_of,
        cache_root=Path(cache_root),
        workers=workers,
    )
    etf_history_start = start - pd.Timedelta(days=180)
    etf_bars_full, etf_warnings = _fetch_etf_bars(
        portfolio_config,
        start=etf_history_start,
        end=market_as_of,
        cache_root=Path(cache_root),
    )
    etf_signals = etf_rotation_signals(
        etf_bars_full,
        rebalance_dates=rebalance_dates,
        stock_signals=stock_signals,
        config=config,
    )
    signals = combine_portfolio_signals(stock_signals, etf_signals)

    engine_bars = dict(stock_bars)
    for symbol, frame in etf_bars_full.items():
        dates_col = pd.to_datetime(frame["trade_date"])
        trimmed = frame[(dates_col >= start) & (dates_col <= market_as_of)].copy()
        if not trimmed.empty:
            engine_bars[symbol] = trimmed

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    signals.to_csv(output / "portfolio_signals.csv", index=False, date_format="%Y-%m-%d")

    result = BacktestEngine(
        rules=TradingRuleSet.personal_account_v2(slippage_bps=5.0),
        initial_cash=config.initial_cash,
    ).run(engine_bars, signals)
    equity = result.equity_curve.copy()
    trades = result.trades.copy()
    equity.to_csv(output / "equity_curve.csv", index=False, date_format="%Y-%m-%d")
    trades.to_csv(output / "trades.csv", index=False, date_format="%Y-%m-%d")

    daily_returns = equity["equity"].pct_change().dropna()
    metrics: dict[str, object] = dict(result.metrics)
    metrics.update(
        {
            "status": "ok",
            "strategy_version": STRATEGY_VERSION,
            "initial_cash": config.initial_cash,
            "latest_equity": float(equity["equity"].iloc[-1]),
            "ranking_dates": len(rankings),
            "rebalance_dates": int(signals["trade_date"].nunique()) if not signals.empty else 0,
            "trade_count": len(trades),
            "observed_return_days": len(daily_returns),
            "daily_win_rate": float((daily_returns > 0).mean()) if len(daily_returns) else None,
            "latest_ranking_date": market_as_of.date().isoformat(),
            "ranking_file": str(ranking_file),
            "real_money_ready": False,
            "cost_profile": "personal_account_v2",
            "stock_history_warnings": stock_warnings,
            "etf_history_warnings": etf_warnings,
            "portfolio_config": str(portfolio_config),
        }
    )
    _write_json(output / "metrics.json", metrics)
    _render_page(metrics, signals, Path(site_dir) / "shadow_v2.html")
    _patch_index(Path(site_dir) / "index.html", metrics)
    return metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.shadow_v2")
    parser.add_argument("--selected-file", default="reports/production/selection/selected.csv")
    parser.add_argument("--ledger-root", default="research/shadow_v2")
    parser.add_argument("--output-dir", default="reports/production/shadow_v2")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--portfolio-config", default="config/portfolio_v2.yml")
    parser.add_argument("--workers", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    metrics = run_shadow_v2_pipeline(
        selected_file=args.selected_file,
        ledger_root=args.ledger_root,
        output_dir=args.output_dir,
        site_dir=args.site_dir,
        cache_root=args.cache_root,
        portfolio_config=args.portfolio_config,
        workers=args.workers,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
