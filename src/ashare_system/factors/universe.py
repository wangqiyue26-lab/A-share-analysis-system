from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ashare_system.data.schema import validate_bars

from .market import MIN_MARKET_FACTOR_ROWS


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str
    average_amount_20: float | None = None


def check_market_eligibility(
    frame: pd.DataFrame,
    *,
    min_history: int = MIN_MARKET_FACTOR_ROWS,
    min_average_amount_20: float = 20_000_000.0,
) -> Eligibility:
    """Apply market-data-only universe gates used by Phase 2A."""
    try:
        bars = validate_bars(frame)
    except (TypeError, ValueError) as exc:
        return Eligibility(False, f"invalid_bars:{type(exc).__name__}")

    if len(bars) < min_history:
        return Eligibility(False, f"insufficient_history:{len(bars)}<{min_history}")

    average_amount_20 = pd.to_numeric(bars["amount"].tail(20), errors="coerce").mean()
    if not np.isfinite(average_amount_20):
        return Eligibility(False, "invalid_average_amount_20")
    value = float(average_amount_20)
    if value < min_average_amount_20:
        return Eligibility(False, "below_min_average_amount_20", value)

    return Eligibility(True, "eligible", value)
