from __future__ import annotations

import argparse
import json
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
from .experiment_v2 import _fetch_histories
from .factors import FactorEngine, load_factor_config
from .pit_evidence import PITEvidenceStore
from .rolling_validation import evaluate_rolling_blocks, pass_development_gate
from .shadow_v2 import _fetch_etf_bars

STRATEGY_VERSION = "capital-aware-v2"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audit_evidence_calendar(
    store: PITEvidenceStore,
    benchmark: pd.DataFrame,
) -> dict[str, object]:
    """Require one exact candidate observation for every trading day in the ledger span."""
    available = store.available_dates()
    if not available:
        return {
            "status": "no_evidence",
            "available_dates": 0,
            "expected_trading_dates": 0,
            "missing_dates": [],
            "extra_nontrading_dates": [],
        }

    dates = pd.to_datetime(benchmark["trade_date"]).dt.normalize()
    expected = sorted(
        date
        for date in dates
        if available[0] <= date <= available[-1]
    )
    expected_set = set(expected)
    available_set = set(available)
    missing = sorted(expected_set - available_set)
    extra = sorted(available_set - expected_set)
    if extra:
        status = "invalid_nontrading_evidence"
    elif missing:
        status = "gap"
    else:
        status = "complete"
    return {
        "status": status,
        "available_dates": len(available),
        "expected_trading_dates": len(expected),
        "first_evidence_date": available[0].date().isoformat(),
        "last_evidence_date": available[-1].date().isoformat(),
        "missing_dates": [date.date().isoformat() for date in missing],
        "extra_nontrading_dates": [date.date().isoformat() for date in extra],
    }


def build_strict_rankings(
    bars_by_symbol: dict[str, pd.DataFrame],
    candidates_by_date: dict[pd.Timestamp, pd.DataFrame],
    *,
    factor_config: str | Path,
) -> dict[pd.Timestamp, pd.DataFrame]:
    """Rank only the exact candidate set that was actually observed on each historical date."""
    engine = FactorEngine(load_factor_config(factor_config))
    rankings: dict[pd.Timestamp, pd.DataFrame] = {}

    for date in sorted(candidates_by_date):
        candidates = candidates_by_date[date].copy()
        candidates["symbol"] = candidates["symbol"].astype(str).str.zfill(6)
        slices: dict[str, pd.DataFrame] = {}
        for symbol in candidates["symbol"]:
            frame = bars_by_symbol.get(symbol)
            if frame is None or frame.empty:
                continue
            trade_dates = pd.to_datetime(frame["trade_date"]).dt.normalize()
            history = frame[trade_dates <= date].copy()
            if not history.empty:
                slices[symbol] = history

        if not slices:
            raise RuntimeError(f"No histories available for strict PIT date {date.date()}")

        factor_result = engine.run(slices)
        ranking = factor_result.ranking.copy()
        if ranking.empty:
            raise RuntimeError(f"No eligible factor rows for strict PIT date {date.date()}")
        ranking["as_of"] = pd.to_datetime(ranking["as_of"]).dt.normalize()
        ranking = ranking[ranking["as_of"] == date].copy()
        if ranking.empty:
            raise RuntimeError(
                f"No same-day factor rows for strict PIT date {date.date()}; "
                "future/stale substitution is forbidden"
            )

        metadata_columns = [
            column
            for column in ["symbol", "name", "exchange", "board", "is_st"]
            if column in candidates.columns
        ]
        ranking = ranking.merge(
            candidates.loc[:, metadata_columns],
            on="symbol",
            how="left",
            validate="one_to_one",
        )
        if ranking["board"].isna().any() or ranking["is_st"].isna().any():
            raise RuntimeError(f"Missing point-in-time metadata on {date.date()}")
        rankings[date] = ranking.sort_values(["rank", "symbol"]).reset_index(drop=True)

    return rankings


def run_strict_pit_experiment(
    *,
    ledger_root: str | Path = "research/pit",
    output_dir: str | Path = "reports/strict-pit-v2",
    cache_root: str | Path = "data/cache",
    factor_config: str | Path = "config/factors_v2.yml",
    portfolio_config: str | Path = "config/portfolio_v2.yml",
    workers: int = 4,
    block_days: int = 20,
    slippage_bps: float = 5.0,
) -> dict[str, object]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    store = PITEvidenceStore(ledger_root)
    available = store.available_dates()

    if not available:
        payload = {
            "status": "no_evidence",
            "strategy_version": STRATEGY_VERSION,
            "strict_point_in_time_universe": True,
            "real_money_ready": False,
            "message": "No immutable PIT candidate evidence has been collected yet.",
        }
        _write_json(output / "summary.json", payload)
        return payload

    first = available[0]
    last = available[-1]
    benchmark = AkshareBenchmarkProvider().get_daily(
        "000300",
        first.strftime("%Y%m%d"),
        last.strftime("%Y%m%d"),
    )
    audit = audit_evidence_calendar(store, benchmark)
    _write_json(output / "coverage.json", audit)

    if audit["status"] == "gap":
        payload = {
            "status": "evidence_gap",
            "strategy_version": STRATEGY_VERSION,
            "strict_point_in_time_universe": True,
            "real_money_ready": False,
            "coverage": audit,
            "message": (
                "Strict PIT run refused because one or more trading dates are missing exact "
                "candidate evidence. The runner will not substitute a later/current universe."
            ),
        }
        _write_json(output / "summary.json", payload)
        return payload

    expected_dates = [
        pd.Timestamp(value).normalize()
        for value in pd.to_datetime(benchmark["trade_date"])
        if first <= pd.Timestamp(value).normalize() <= last
    ]
    required_equity_dates = block_days + 1
    if len(expected_dates) < required_equity_dates:
        manifests = [store.day_manifest(date) for date in expected_dates]
        degraded = [
            str(manifest["evidence_date"])
            for manifest in manifests
            if bool(manifest.get("degraded_mode", False))
        ]
        payload = {
            "status": "collecting",
            "strategy_version": STRATEGY_VERSION,
            "strict_point_in_time_universe": True,
            "real_money_ready": False,
            "evidence_dates": len(expected_dates),
            "required_for_first_block": required_equity_dates,
            "remaining_dates": required_equity_dates - len(expected_dates),
            "first_evidence_date": first.date().isoformat(),
            "last_evidence_date": last.date().isoformat(),
            "degraded_evidence_dates": degraded,
            "coverage": audit,
        }
        _write_json(output / "summary.json", payload)
        return payload

    candidates_by_date = {
        date: store.load_candidates(date)
        for date in expected_dates
    }
    union_symbols = sorted(
        {
            symbol
            for frame in candidates_by_date.values()
            for symbol in frame["symbol"].astype(str).str.zfill(6)
        }
    )
    union = pd.DataFrame({"symbol": union_symbols})
    warmup_start = first - pd.Timedelta(days=240)
    bars, history_warnings = _fetch_histories(
        union,
        start=warmup_start,
        end=last,
        cache_root=Path(cache_root),
        workers=workers,
    )
    missing_history = sorted(set(union_symbols) - set(bars))
    if missing_history:
        payload = {
            "status": "history_gap",
            "strategy_version": STRATEGY_VERSION,
            "strict_point_in_time_universe": True,
            "real_money_ready": False,
            "missing_history_symbols": missing_history,
            "history_warnings": history_warnings,
            "message": "Strict PIT run refused because candidate histories are incomplete.",
        }
        _write_json(output / "summary.json", payload)
        return payload

    rankings = build_strict_rankings(
        bars,
        candidates_by_date,
        factor_config=factor_config,
    )
    portfolio = load_capital_aware_config(portfolio_config)
    stock_signals = capital_aware_stock_signals(rankings, config=portfolio)
    rebalance_dates = expected_dates[:: portfolio.rebalance_every_trading_days]

    etf_warmup_start = first - pd.Timedelta(days=180)
    etf_bars_full, etf_warnings = _fetch_etf_bars(
        portfolio_config,
        start=etf_warmup_start,
        end=last,
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
        trimmed = frame[(dates >= first) & (dates <= last)].copy()
        if not trimmed.empty:
            engine_bars[symbol] = trimmed
    for symbol, frame in etf_bars_full.items():
        dates = pd.to_datetime(frame["trade_date"]).dt.normalize()
        trimmed = frame[(dates >= first) & (dates <= last)].copy()
        if not trimmed.empty:
            engine_bars[symbol] = trimmed

    result = BacktestEngine(
        rules=TradingRuleSet.personal_account_v2(slippage_bps=slippage_bps),
        initial_cash=portfolio.initial_cash,
    ).run(engine_bars, signals)

    windows, rolling = evaluate_rolling_blocks(
        result.equity_curve,
        trades=result.trades,
        benchmark=benchmark,
        block_days=block_days,
    )

    result.equity_curve.to_csv(output / "equity_curve.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    signals.to_csv(output / "signals.csv", index=False)
    windows.to_csv(output / "rolling_20d.csv", index=False)
    ranking_rows = pd.concat(
        [
            frame.assign(evidence_date=date)
            for date, frame in rankings.items()
        ],
        ignore_index=True,
    )
    ranking_rows.to_csv(output / "rankings.csv", index=False)

    manifests = [store.day_manifest(date) for date in expected_dates]
    degraded = [
        str(manifest["evidence_date"])
        for manifest in manifests
        if bool(manifest.get("degraded_mode", False))
    ]
    screen_sources = sorted(
        {str(manifest.get("screen_source", "unknown")) for manifest in manifests}
    )
    payload = {
        "status": "complete",
        "strategy_version": STRATEGY_VERSION,
        "research_grade": "strict_point_in_time_observed_candidate_ledger",
        "strict_point_in_time_universe": True,
        "real_money_ready": False,
        "start": first.date().isoformat(),
        "end": last.date().isoformat(),
        "initial_cash": portfolio.initial_cash,
        "slippage_bps": slippage_bps,
        "evidence_dates": len(expected_dates),
        "candidate_union_count": len(union_symbols),
        "screen_sources": screen_sources,
        "degraded_evidence_dates": degraded,
        "history_warnings": history_warnings,
        "etf_warnings": etf_warnings,
        "trade_count": len(result.trades),
        "total_return": result.metrics["total_return"],
        "max_drawdown": result.metrics["max_drawdown"],
        "rolling": rolling.as_dict(),
        "development_gate_passed": pass_development_gate(rolling),
        "coverage": audit,
    }
    _write_json(output / "summary.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.strict_pit_v2")
    parser.add_argument("--ledger-root", default="research/pit")
    parser.add_argument("--output-dir", default="reports/strict-pit-v2")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--factor-config", default="config/factors_v2.yml")
    parser.add_argument("--portfolio-config", default="config/portfolio_v2.yml")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--block-days", type=int, default=20)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_strict_pit_experiment(
        ledger_root=args.ledger_root,
        output_dir=args.output_dir,
        cache_root=args.cache_root,
        factor_config=args.factor_config,
        portfolio_config=args.portfolio_config,
        workers=args.workers,
        block_days=args.block_days,
        slippage_bps=args.slippage_bps,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
