from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PriceLimitRule:
    lower: float
    upper: float


@dataclass(frozen=True)
class TradingRuleSet:
    board_lot: int = 100
    settlement_days: int = 1
    commission_rate: float = 0.0003
    minimum_commission_cny: float = 5.0
    slippage_bps: float = 5.0

    def stamp_duty_rate(self, trade_date: str | pd.Timestamp, side: str) -> float:
        """Return historical A-share stamp duty for supported periods.

        The engine intentionally supports the seller-only regime beginning on
        2008-09-19. Earlier regimes are rejected instead of silently applying a
        modern rule to old backtests.
        """
        date = pd.Timestamp(trade_date).normalize()
        if side.lower() != "sell":
            return 0.0
        if date < pd.Timestamp("2008-09-19"):
            raise ValueError("Stamp-duty history before 2008-09-19 is not modelled")
        if date >= pd.Timestamp("2023-08-28"):
            return 0.0005
        return 0.001

    def price_limit_pct(
        self,
        *,
        trade_date: str | pd.Timestamp,
        board: str,
        is_st: bool = False,
    ) -> float:
        date = pd.Timestamp(trade_date).normalize()
        normalized = board.strip().lower()

        if normalized in {"科创板", "star", "star_market"}:
            return 0.20
        if normalized in {"北交所", "bse", "beijing"}:
            return 0.30
        if normalized in {"创业板", "chinext"}:
            return 0.20 if date >= pd.Timestamp("2020-08-24") else 0.10

        if is_st:
            if date >= pd.Timestamp("2026-07-06"):
                return 0.10
            return 0.05
        return 0.10

    def round_lot(self, shares: float) -> int:
        if shares <= 0:
            return 0
        return int(shares // self.board_lot) * self.board_lot


def limit_prices(
    previous_close: float,
    limit_pct: float,
) -> PriceLimitRule:
    if previous_close <= 0:
        raise ValueError("previous_close must be positive")
    if not 0 < limit_pct < 1:
        raise ValueError("limit_pct must be between 0 and 1")
    return PriceLimitRule(
        lower=round(previous_close * (1 - limit_pct), 2),
        upper=round(previous_close * (1 + limit_pct), 2),
    )


def is_limit_blocked(
    *,
    side: str,
    open_price: float,
    previous_close: float,
    limit_pct: float,
    tolerance: float = 0.001,
) -> bool:
    """Conservative daily-bar execution gate for one-price limit opens."""
    limits = limit_prices(previous_close, limit_pct)
    normalized = side.lower()
    if normalized == "buy":
        return open_price >= limits.upper - tolerance
    if normalized == "sell":
        return open_price <= limits.lower + tolerance
    raise ValueError(f"Unsupported side: {side}")
