from __future__ import annotations

from pathlib import Path

import pandas as pd


def trade_statistics(equity_curve: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float | int]:
    """Summarize execution activity and costs for a completed backtest."""
    if equity_curve.empty or "equity" not in equity_curve.columns:
        raise ValueError("equity_curve must contain equity rows")

    average_equity = float(pd.to_numeric(equity_curve["equity"], errors="raise").mean())
    if average_equity <= 0:
        raise ValueError("average equity must be positive")

    if trades.empty:
        return {
            "trade_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "gross_traded_notional": 0.0,
            "turnover": 0.0,
            "commission": 0.0,
            "stamp_duty": 0.0,
            "total_cost": 0.0,
        }

    required = {"side", "notional", "commission", "stamp_duty"}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"trades missing columns: {missing}")

    notional = pd.to_numeric(trades["notional"], errors="raise").abs()
    commission = pd.to_numeric(trades["commission"], errors="raise")
    stamp_duty = pd.to_numeric(trades["stamp_duty"], errors="raise")
    sides = trades["side"].astype(str).str.lower()
    gross = float(notional.sum())
    return {
        "trade_count": len(trades),
        "buy_count": int((sides == "buy").sum()),
        "sell_count": int((sides == "sell").sum()),
        "gross_traded_notional": gross,
        "turnover": gross / average_equity,
        "commission": float(commission.sum()),
        "stamp_duty": float(stamp_duty.sum()),
        "total_cost": float((commission + stamp_duty).sum()),
    }


def benchmark_comparison(
    equity_curve: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Align portfolio equity with a benchmark close series and compute excess return."""
    if equity_curve.empty:
        raise ValueError("equity_curve cannot be empty")
    if benchmark.empty:
        raise ValueError("benchmark cannot be empty")

    portfolio_required = {"trade_date", "equity"}
    benchmark_required = {"trade_date", "close"}
    missing_portfolio = sorted(portfolio_required - set(equity_curve.columns))
    missing_benchmark = sorted(benchmark_required - set(benchmark.columns))
    if missing_portfolio:
        raise ValueError(f"equity_curve missing columns: {missing_portfolio}")
    if missing_benchmark:
        raise ValueError(f"benchmark missing columns: {missing_benchmark}")

    portfolio = equity_curve.loc[:, ["trade_date", "equity"]].copy()
    portfolio["trade_date"] = pd.to_datetime(portfolio["trade_date"]).dt.normalize()
    portfolio["equity"] = pd.to_numeric(portfolio["equity"], errors="raise")

    index = benchmark.loc[:, ["trade_date", "close"]].copy()
    index["trade_date"] = pd.to_datetime(index["trade_date"]).dt.normalize()
    index["close"] = pd.to_numeric(index["close"], errors="raise")

    aligned = portfolio.merge(index, on="trade_date", how="inner").sort_values("trade_date")
    if len(aligned) < 2:
        raise ValueError("portfolio and benchmark need at least two aligned dates")
    if (aligned[["equity", "close"]] <= 0).any().any():
        raise ValueError("portfolio equity and benchmark close must be positive")

    aligned["portfolio_index"] = aligned["equity"] / aligned["equity"].iloc[0]
    aligned["benchmark_index"] = aligned["close"] / aligned["close"].iloc[0]
    aligned["excess_index"] = aligned["portfolio_index"] / aligned["benchmark_index"]

    portfolio_return = float(aligned["portfolio_index"].iloc[-1] - 1)
    benchmark_return = float(aligned["benchmark_index"].iloc[-1] - 1)
    excess_return = float(aligned["excess_index"].iloc[-1] - 1)
    return aligned.reset_index(drop=True), {
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
    }


def write_backtest_report(
    output_dir: str | Path,
    *,
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    performance_metrics: dict[str, float] | None = None,
) -> dict[str, Path]:
    """Write machine-readable backtest outputs for later dashboard generation."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    equity_path = output / "equity.csv"
    trades_path = output / "trades.csv"
    summary_path = output / "summary.json"
    equity_curve.to_csv(equity_path, index=False)
    trades.to_csv(trades_path, index=False)

    summary: dict[str, float | int] = {}
    if performance_metrics:
        summary.update(performance_metrics)
    summary.update(trade_statistics(equity_curve, trades))

    paths = {
        "equity_csv": equity_path,
        "trades_csv": trades_path,
        "summary_json": summary_path,
    }

    if benchmark is not None:
        comparison, benchmark_metrics = benchmark_comparison(equity_curve, benchmark)
        comparison_path = output / "benchmark.csv"
        comparison.to_csv(comparison_path, index=False)
        summary.update(benchmark_metrics)
        paths["benchmark_csv"] = comparison_path

    pd.Series(summary).to_json(summary_path, force_ascii=False, indent=2)
    return paths
