from __future__ import annotations

import argparse
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
from .data.benchmark import AkshareBenchmarkProvider
from .factors import FactorEngine, load_factor_config
from .production import _fetch_one_history
from .rolling_validation import evaluate_rolling_blocks, pass_development_gate
from .shadow_v2 import _fetch_etf_bars


def build_historical_rankings(
    bars_by_symbol: dict[str, pd.DataFrame],
    metadata: pd.DataFrame,
    *,
    factor_config: str | Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[pd.Timestamp, pd.DataFrame]:
    """Build dated rankings from bars observable on each historical date.

    The caller is responsible for the historical universe. When the caller
    passes today's universe, this is a development backtest with survivorship
    bias and must not be presented as strict PIT evidence.
    """
    metadata = metadata.copy()
    metadata["symbol"] = metadata["symbol"].astype(str).str.zfill(6)
    engine = FactorEngine(load_factor_config(factor_config))
    calendar = sorted(
        {
            pd.Timestamp(value).normalize()
            for frame in bars_by_symbol.values()
            for value in pd.to_datetime(frame["trade_date"])
            if start <= pd.Timestamp(value).normalize() <= end
        }
    )
    results: dict[pd.Timestamp, pd.DataFrame] = {}
    for date in calendar:
        slices: dict[str, pd.DataFrame] = {}
        for symbol, frame in bars_by_symbol.items():
            dates = pd.to_datetime(frame["trade_date"]).dt.normalize()
            history = frame[dates <= date].copy()
            if not history.empty:
                slices[symbol] = history
        if not slices:
            continue
        factor_result = engine.run(slices)
        ranking = factor_result.ranking.copy()
        if ranking.empty:
            continue
        ranking["as_of"] = pd.to_datetime(ranking["as_of"]).dt.normalize()
        ranking = ranking[ranking["as_of"] == date].copy()
        if ranking.empty:
            continue
        columns = [
            column
            for column in ["symbol", "name", "exchange", "board", "is_st"]
            if column in metadata.columns
        ]
        ranking = ranking.merge(metadata.loc[:, columns], on="symbol", how="left")
        if "board" not in ranking or "is_st" not in ranking:
            continue
        results[date] = ranking.sort_values(["rank", "symbol"]).reset_index(drop=True)
    return results


def _fetch_histories(
    candidates: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_root: Path,
    workers: int,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]]]:
    bars: dict[str, pd.DataFrame] = {}
    warnings: list[dict[str, str]] = []
    symbols = candidates["symbol"].astype(str).str.zfill(6).tolist()
    with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
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
                warnings.append(
                    {"symbol": symbol, "warning": warning or "history_fetch_failed"}
                )
                continue
            bars[symbol] = frame
            if warning:
                warnings.append(
                    {"symbol": symbol, "provider": provider or "unknown", "warning": warning}
                )
    return bars, warnings


def run_experiment(
    *,
    candidates_file: str | Path,
    start: str,
    end: str,
    output_dir: str | Path = "reports/v2-validation",
    cache_root: str | Path = "data/cache",
    factor_config: str | Path = "config/factors_v2.yml",
    portfolio_config: str | Path = "config/portfolio_v2.yml",
    workers: int = 4,
    block_days: int = 20,
) -> dict[str, object]:
    candidates = pd.read_csv(candidates_file, dtype={"symbol": str})
    candidates["symbol"] = candidates["symbol"].astype(str).str.zfill(6)
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts <= start_ts:
        raise ValueError("end must be after start")

    warmup_start = start_ts - pd.Timedelta(days=240)
    bars, warnings = _fetch_histories(
        candidates,
        start=warmup_start,
        end=end_ts,
        cache_root=Path(cache_root),
        workers=workers,
    )
    if len(bars) < 10:
        raise RuntimeError(f"Only {len(bars)} stock histories available for v2 experiment")

    rankings = build_historical_rankings(
        bars,
        candidates,
        factor_config=factor_config,
        start=start_ts,
        end=end_ts,
    )
    if len(rankings) < block_days + 1:
        raise RuntimeError("Not enough historical ranking dates for one validation block")

    portfolio = load_capital_aware_config(portfolio_config)
    stock_signals = capital_aware_stock_signals(rankings, config=portfolio)
    ranking_dates = sorted(rankings)
    rebalance_dates = ranking_dates[:: portfolio.rebalance_every_trading_days]

    etf_bars_full, etf_warnings = _fetch_etf_bars(
        portfolio_config,
        start=warmup_start,
        end=end_ts,
        cache_root=Path(cache_root),
    )
    etf_signals = etf_rotation_signals(
        etf_bars_full,
        rebalance_dates=rebalance_dates,
        stock_signals=stock_signals,
        config=portfolio,
    )
    signals = combine_portfolio_signals(stock_signals, etf_signals)

    engine_bars: dict[str, pd.DataFrame] = {}
    for symbol, frame in bars.items():
        dates = pd.to_datetime(frame["trade_date"]).dt.normalize()
        trimmed = frame[(dates >= start_ts) & (dates <= end_ts)].copy()
        if not trimmed.empty:
            engine_bars[symbol] = trimmed
    for symbol, frame in etf_bars_full.items():
        dates = pd.to_datetime(frame["trade_date"]).dt.normalize()
        trimmed = frame[(dates >= start_ts) & (dates <= end_ts)].copy()
        if not trimmed.empty:
            engine_bars[symbol] = trimmed

    result = BacktestEngine(
        rules=TradingRuleSet.personal_account_v2(slippage_bps=5.0),
        initial_cash=portfolio.initial_cash,
    ).run(engine_bars, signals)

    benchmark = AkshareBenchmarkProvider().get_daily(
        "000300",
        start_ts.strftime("%Y%m%d"),
        end_ts.strftime("%Y%m%d"),
    )
    windows, summary = evaluate_rolling_blocks(
        result.equity_curve,
        trades=result.trades,
        benchmark=benchmark,
        block_days=block_days,
    )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result.equity_curve.to_csv(output / "equity_curve.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    signals.to_csv(output / "signals.csv", index=False)
    windows.to_csv(output / "rolling_20d.csv", index=False)

    payload: dict[str, object] = {
        "strategy_version": "capital-aware-v2",
        "research_grade": "development_only_current_universe",
        "strict_point_in_time_universe": False,
        "warning": (
            "This fast historical experiment uses a current observable candidate universe. "
            "It is for iteration and bug-finding, not proof of investability."
        ),
        "start": start_ts.date().isoformat(),
        "end": end_ts.date().isoformat(),
        "initial_cash": portfolio.initial_cash,
        "candidate_count": len(candidates),
        "history_count": len(bars),
        "ranking_dates": len(rankings),
        "trade_count": len(result.trades),
        "total_return": result.metrics["total_return"],
        "max_drawdown": result.metrics["max_drawdown"],
        "rolling": summary.as_dict(),
        "development_gate_passed": pass_development_gate(summary),
        "history_warnings": warnings,
        "etf_warnings": etf_warnings,
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.experiment_v2")
    parser.add_argument("--candidates-file", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-dir", default="reports/v2-validation")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--factor-config", default="config/factors_v2.yml")
    parser.add_argument("--portfolio-config", default="config/portfolio_v2.yml")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--block-days", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_experiment(
        candidates_file=args.candidates_file,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        cache_root=args.cache_root,
        factor_config=args.factor_config,
        portfolio_config=args.portfolio_config,
        workers=args.workers,
        block_days=args.block_days,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
