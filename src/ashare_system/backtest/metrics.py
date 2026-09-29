from __future__ import annotations

import math

import pandas as pd


def performance_summary(equity_curve: pd.DataFrame) -> dict[str, float]:
    if equity_curve.empty or "equity" not in equity_curve.columns:
        raise ValueError("equity_curve must contain equity rows")
    frame = equity_curve.sort_values("trade_date").copy()
    equity = pd.to_numeric(frame["equity"], errors="raise")
    if (equity <= 0).any():
        raise ValueError("equity must stay positive")

    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_drawdown = float(drawdown.min())

    day_count = max((pd.Timestamp(frame["trade_date"].iloc[-1]) - pd.Timestamp(frame["trade_date"].iloc[0])).days, 1)
    annualized_return = float((equity.iloc[-1] / equity.iloc[0]) ** (365.25 / day_count) - 1)
    annualized_volatility = float(returns.std(ddof=1) * math.sqrt(252)) if len(returns) > 1 else 0.0
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * math.sqrt(252))
        if len(returns) > 1 and returns.std(ddof=1) > 0
        else 0.0
    )
    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }
