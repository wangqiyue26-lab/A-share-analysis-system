from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ashare_system.data.schema import validate_bars

MIN_MARKET_FACTOR_ROWS = 61


class FactorDataError(ValueError):
    """Raised when a symbol does not have enough valid data for factor calculation."""


def compute_market_factors(frame: pd.DataFrame) -> dict[str, object]:
    """Compute one latest-date snapshot of explainable market factors."""
    bars = validate_bars(frame)
    if len(bars) < MIN_MARKET_FACTOR_ROWS:
        raise FactorDataError(
            f"Need at least {MIN_MARKET_FACTOR_ROWS} daily bars; received {len(bars)}"
        )

    close = bars["close"].astype(float)
    returns = close.pct_change()

    momentum_20 = close.iloc[-1] / close.iloc[-21] - 1.0
    momentum_60 = close.iloc[-1] / close.iloc[-61] - 1.0

    ma20 = close.tail(20).mean()
    ma60 = close.tail(60).mean()
    trend_ma20_ma60 = ma20 / ma60 - 1.0

    recent_returns = returns.tail(20).dropna()
    if len(recent_returns) < 20:
        raise FactorDataError("Need 20 valid daily returns for volatility_20")
    volatility_20 = recent_returns.std(ddof=0) * math.sqrt(252.0)

    trailing_close = close.tail(60)
    running_peak = trailing_close.cummax()
    drawdown = trailing_close / running_peak - 1.0
    max_drawdown_60 = -float(drawdown.min())

    average_amount_20 = pd.to_numeric(bars["amount"].tail(20), errors="coerce").mean()
    if not np.isfinite(average_amount_20) or average_amount_20 <= 0:
        raise FactorDataError("Trailing 20-day average amount must be positive")
    log_amount_20 = math.log1p(float(average_amount_20))

    values = {
        "momentum_20": float(momentum_20),
        "momentum_60": float(momentum_60),
        "trend_ma20_ma60": float(trend_ma20_ma60),
        "volatility_20": float(volatility_20),
        "max_drawdown_60": max_drawdown_60,
        "log_amount_20": log_amount_20,
    }
    if not all(np.isfinite(value) for value in values.values()):
        raise FactorDataError("Computed market factors contain non-finite values")

    return {
        "symbol": str(bars.iloc[-1]["symbol"]).zfill(6),
        "as_of": bars.iloc[-1]["trade_date"],
        "average_amount_20": float(average_amount_20),
        **values,
    }
