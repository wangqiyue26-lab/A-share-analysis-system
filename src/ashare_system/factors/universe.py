from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ashare_system.data.schema import validate_bars
from ashare_system.data.security_master import REQUIRED_EXCHANGES, validate_security_master

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


def current_universe_symbols(
    master: pd.DataFrame,
    *,
    as_of: str | pd.Timestamp,
    min_listing_days: int = 120,
    exclude_st: bool = True,
) -> list[str]:
    """Select today's research universe without pretending a current snapshot is historical."""
    data = validate_security_master(master)
    exchanges = set(data["exchange"].unique())
    if exchanges != REQUIRED_EXCHANGES:
        missing = sorted(REQUIRED_EXCHANGES - exchanges)
        raise ValueError(f"Current universe requires a complete security master; missing={missing}")

    cutoff = pd.Timestamp(as_of).normalize()
    observed_date = data["observed_at"].max().tz_convert("Asia/Shanghai").tz_localize(None).normalize()
    if cutoff < observed_date:
        raise ValueError(
            "A current security-master snapshot cannot be used to reconstruct an earlier universe"
        )

    minimum_list_date = cutoff - pd.Timedelta(days=min_listing_days)
    mask = data["is_listed"] & data["list_date"].notna() & (data["list_date"] <= minimum_list_date)
    if exclude_st:
        mask &= ~data["is_st"]
    return sorted(data.loc[mask, "symbol"].astype(str).tolist())
