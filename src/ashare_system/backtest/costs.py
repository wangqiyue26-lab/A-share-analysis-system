from __future__ import annotations

from dataclasses import dataclass

from .rules import TradingRuleSet


@dataclass(frozen=True)
class TradeCost:
    commission: float
    stamp_duty: float

    @property
    def total(self) -> float:
        return self.commission + self.stamp_duty


def trade_cost(
    *,
    notional: float,
    side: str,
    trade_date: object,
    rules: TradingRuleSet,
) -> TradeCost:
    if notional < 0:
        raise ValueError("notional cannot be negative")
    if notional == 0:
        return TradeCost(commission=0.0, stamp_duty=0.0)

    commission = max(notional * rules.commission_rate, rules.minimum_commission_cny)
    stamp = notional * rules.stamp_duty_rate(trade_date, side)
    return TradeCost(commission=float(commission), stamp_duty=float(stamp))


def execution_price(open_price: float, side: str, slippage_bps: float) -> float:
    if open_price <= 0:
        raise ValueError("open_price must be positive")
    slip = slippage_bps / 10_000.0
    normalized = side.lower()
    if normalized == "buy":
        return open_price * (1 + slip)
    if normalized == "sell":
        return open_price * (1 - slip)
    raise ValueError(f"Unsupported side: {side}")
