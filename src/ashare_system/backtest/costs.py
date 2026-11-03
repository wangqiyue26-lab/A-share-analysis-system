from __future__ import annotations

from dataclasses import dataclass

from .rules import TradingRuleSet


@dataclass(frozen=True)
class TradeCost:
    commission: float
    stamp_duty: float
    transfer_fee: float = 0.0

    @property
    def total(self) -> float:
        return self.commission + self.stamp_duty + self.transfer_fee


def trade_cost(
    *,
    notional: float,
    side: str,
    trade_date: object,
    rules: TradingRuleSet,
    instrument_type: str = "stock",
) -> TradeCost:
    if notional < 0:
        raise ValueError("notional cannot be negative")
    if notional == 0:
        return TradeCost(commission=0.0, stamp_duty=0.0, transfer_fee=0.0)

    if rules.commission_threshold_cny is not None and rules.commission_excess_rate is not None:
        threshold = float(rules.commission_threshold_cny)
        if notional <= threshold:
            commission = float(rules.minimum_commission_cny)
        else:
            commission = float(rules.minimum_commission_cny) + (
                notional - threshold
            ) * float(rules.commission_excess_rate)
    else:
        commission = max(notional * rules.commission_rate, rules.minimum_commission_cny)

    normalized_type = instrument_type.strip().lower()
    if normalized_type == "etf":
        stamp = 0.0
        transfer = 0.0
    else:
        stamp = notional * rules.stamp_duty_rate(trade_date, side)
        transfer = notional * rules.transfer_fee_rate_stock

    return TradeCost(
        commission=round(float(commission) + 1e-12, 2),
        stamp_duty=round(float(stamp) + 1e-12, 2),
        transfer_fee=round(float(transfer) + 1e-12, 2),
    )


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
