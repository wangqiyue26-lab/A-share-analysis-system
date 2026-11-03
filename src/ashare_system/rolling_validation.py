from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class RollingValidationSummary:
    block_days: int
    block_count: int
    median_return: float
    positive_block_rate: float
    median_max_drawdown: float
    median_turnover: float
    median_cost_drag: float
    benchmark_outperformance_rate: float | None
    median_excess_return: float | None
    worst_block_return: float
    best_block_return: float
    return_excluding_best_block: float
    top_1_log_return_share: float | None
    top_2_log_return_share: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "block_days": self.block_days,
            "block_count": self.block_count,
            "median_return": self.median_return,
            "positive_block_rate": self.positive_block_rate,
            "median_max_drawdown": self.median_max_drawdown,
            "median_turnover": self.median_turnover,
            "median_cost_drag": self.median_cost_drag,
            "benchmark_outperformance_rate": self.benchmark_outperformance_rate,
            "median_excess_return": self.median_excess_return,
            "worst_block_return": self.worst_block_return,
            "best_block_return": self.best_block_return,
            "return_excluding_best_block": self.return_excluding_best_block,
            "top_1_log_return_share": self.top_1_log_return_share,
            "top_2_log_return_share": self.top_2_log_return_share,
        }


def _normalize_equity(equity_curve: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "equity"}
    missing = sorted(required - set(equity_curve.columns))
    if missing:
        raise ValueError(f"equity_curve missing columns: {missing}")
    frame = equity_curve.loc[:, ["trade_date", "equity"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    if frame.empty or (frame["equity"] <= 0).any():
        raise ValueError("equity curve must contain positive values")
    frame["return"] = frame["equity"].pct_change()
    return frame.reset_index(drop=True)


def _normalize_benchmark(benchmark: pd.DataFrame | None) -> pd.DataFrame | None:
    if benchmark is None or benchmark.empty:
        return None
    required = {"trade_date", "close"}
    missing = sorted(required - set(benchmark.columns))
    if missing:
        raise ValueError(f"benchmark missing columns: {missing}")
    frame = benchmark.loc[:, ["trade_date", "close"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    frame["benchmark_return"] = frame["close"].pct_change()
    return frame


def evaluate_rolling_blocks(
    equity_curve: pd.DataFrame,
    *,
    trades: pd.DataFrame | None = None,
    benchmark: pd.DataFrame | None = None,
    block_days: int = 20,
) -> tuple[pd.DataFrame, RollingValidationSummary]:
    """Evaluate strategy results in non-overlapping return blocks.

    One block contains block_days daily return observations. That requires
    block_days + 1 equity points for the first block; later blocks reuse the
    previous block's ending equity as their starting point.
    """
    if block_days <= 0:
        raise ValueError("block_days must be positive")
    equity = _normalize_equity(equity_curve)
    benchmark_frame = _normalize_benchmark(benchmark)
    returns = equity.dropna(subset=["return"]).reset_index(drop=True)
    if returns.empty:
        raise ValueError("equity curve has no return observations")

    if benchmark_frame is not None:
        returns = returns.merge(
            benchmark_frame.loc[:, ["trade_date", "benchmark_return"]],
            on="trade_date",
            how="left",
        )

    trade_frame = None
    if trades is not None and not trades.empty:
        trade_frame = trades.copy()
        trade_frame["trade_date"] = pd.to_datetime(trade_frame["trade_date"]).dt.normalize()
        for column in (
            "notional",
            "commission",
            "stamp_duty",
            "transfer_fee",
            "slippage_cost",
        ):
            if column not in trade_frame:
                trade_frame[column] = 0.0
            trade_frame[column] = pd.to_numeric(
                trade_frame[column], errors="coerce"
            ).fillna(0.0)

    blocks: list[dict[str, object]] = []
    for block_index, start in enumerate(range(0, len(returns), block_days), start=1):
        chunk = returns.iloc[start : start + block_days].copy()
        if len(chunk) < block_days:
            break
        start_date = pd.Timestamp(chunk["trade_date"].iloc[0])
        end_date = pd.Timestamp(chunk["trade_date"].iloc[-1])

        strategy_return = float((1.0 + chunk["return"]).prod() - 1.0)
        wealth = (1.0 + chunk["return"]).cumprod()
        drawdown = wealth / wealth.cummax() - 1.0
        max_drawdown = float(drawdown.min())

        benchmark_return: float | None = None
        excess_return: float | None = None
        if "benchmark_return" in chunk and chunk["benchmark_return"].notna().all():
            benchmark_return = float((1.0 + chunk["benchmark_return"]).prod() - 1.0)
            excess_return = strategy_return - benchmark_return

        trade_count = 0
        turnover = 0.0
        explicit_cost = 0.0
        slippage_cost = 0.0
        cost_drag = 0.0
        if trade_frame is not None:
            selected = trade_frame[
                (trade_frame["trade_date"] >= start_date)
                & (trade_frame["trade_date"] <= end_date)
            ]
            trade_count = len(selected)
            explicit_cost = float(
                selected[["commission", "stamp_duty", "transfer_fee"]].sum().sum()
            )
            slippage_cost = float(selected["slippage_cost"].sum())
            earlier = equity[equity["trade_date"] < start_date]
            block_start_equity = float(
                earlier["equity"].iloc[-1] if not earlier.empty else equity["equity"].iloc[0]
            )
            turnover = (
                float(selected["notional"].sum()) / block_start_equity
                if block_start_equity > 0
                else 0.0
            )
            cost_drag = (
                (explicit_cost + slippage_cost) / block_start_equity
                if block_start_equity > 0
                else 0.0
            )

        blocks.append(
            {
                "block": block_index,
                "start_date": start_date,
                "end_date": end_date,
                "trading_days": len(chunk),
                "return": strategy_return,
                "benchmark_return": benchmark_return,
                "excess_return": excess_return,
                "max_drawdown": max_drawdown,
                "positive_day_rate": float((chunk["return"] > 0).mean()),
                "trade_count": trade_count,
                "turnover": turnover,
                "explicit_cost": explicit_cost,
                "slippage_cost": slippage_cost,
                "cost_drag": cost_drag,
            }
        )

    frame = pd.DataFrame(blocks)
    if frame.empty:
        raise ValueError(
            f"not enough observations for one complete {block_days}-trading-day block"
        )

    benchmark_ready = frame["excess_return"].notna()
    benchmark_outperformance_rate = (
        float((frame.loc[benchmark_ready, "excess_return"] > 0).mean())
        if benchmark_ready.any()
        else None
    )
    median_excess = (
        float(frame.loc[benchmark_ready, "excess_return"].median())
        if benchmark_ready.any()
        else None
    )
    ordered_returns = frame["return"].sort_values(ascending=False).reset_index(drop=True)
    best_block_return = float(ordered_returns.iloc[0])
    return_excluding_best = float(
        (1.0 + ordered_returns.iloc[1:]).prod() - 1.0
        if len(ordered_returns) > 1
        else 0.0
    )
    log_returns = [math.log1p(float(value)) for value in ordered_returns if float(value) > -1.0]
    total_log_return = sum(log_returns)
    if total_log_return > 0 and log_returns:
        top_1_log_share = float(log_returns[0] / total_log_return)
        top_2_log_share = float(sum(log_returns[:2]) / total_log_return)
    else:
        top_1_log_share = None
        top_2_log_share = None

    summary = RollingValidationSummary(
        block_days=block_days,
        block_count=len(frame),
        median_return=float(frame["return"].median()),
        positive_block_rate=float((frame["return"] > 0).mean()),
        median_max_drawdown=float(frame["max_drawdown"].median()),
        median_turnover=float(frame["turnover"].median()),
        median_cost_drag=float(frame["cost_drag"].median()),
        benchmark_outperformance_rate=benchmark_outperformance_rate,
        median_excess_return=median_excess,
        worst_block_return=float(frame["return"].min()),
        best_block_return=best_block_return,
        return_excluding_best_block=return_excluding_best,
        top_1_log_return_share=top_1_log_share,
        top_2_log_return_share=top_2_log_share,
    )
    return frame, summary


def pass_development_gate(summary: RollingValidationSummary) -> bool:
    """A demanding research gate; passing it is not real-money approval."""
    benchmark_ok = (
        summary.benchmark_outperformance_rate is not None
        and summary.benchmark_outperformance_rate >= 0.55
        and summary.median_excess_return is not None
        and summary.median_excess_return > 0
    )
    return bool(
        summary.block_count >= 12
        and summary.positive_block_rate >= 0.50
        and summary.median_max_drawdown > -0.08
        and summary.median_turnover <= 2.0
        and summary.median_cost_drag <= 0.01
        and benchmark_ok
    )
