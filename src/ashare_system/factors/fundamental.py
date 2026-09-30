from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from ashare_system.data.point_in_time import available_metrics_as_of

FUNDAMENTAL_FACTOR_COLUMNS = (
    "revenue_yoy",
    "net_profit_yoy",
    "net_margin",
    "ocf_to_net_profit",
    "debt_to_assets",
)

_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "利润表::营业总收入",
        "利润表::营业收入",
    ),
    "net_profit": (
        "利润表::归属于母公司所有者的净利润",
        "利润表::归属于母公司股东的净利润",
        "利润表::净利润",
    ),
    "operating_cash_flow": (
        "现金流量表::经营活动产生的现金流量净额",
        "现金流量表::经营活动现金流量净额",
    ),
    "total_assets": (
        "资产负债表::资产总计",
        "资产负债表::资产总额",
    ),
    "total_liabilities": (
        "资产负债表::负债合计",
        "资产负债表::负债总计",
    ),
}


def _matching(frame: pd.DataFrame, aliases: Iterable[str]) -> pd.DataFrame:
    alias_set = set(aliases)
    return frame[frame["metric"].isin(alias_set)].copy()


def _latest_period(frame: pd.DataFrame, aliases: Iterable[str]) -> pd.Timestamp | None:
    matched = _matching(frame, aliases)
    if matched.empty:
        return None
    return pd.Timestamp(matched["period_end"].max())


def _value_at(frame: pd.DataFrame, period_end: pd.Timestamp, aliases: Iterable[str]) -> float:
    for alias in aliases:
        matched = frame[(frame["metric"] == alias) & (frame["period_end"] == period_end)]
        if not matched.empty:
            return float(matched.iloc[-1]["value"])
    return float("nan")


def _growth(current: float, previous: float) -> float:
    if not np.isfinite(current) or not np.isfinite(previous) or previous <= 0:
        return float("nan")
    return current / previous - 1.0


def _ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator <= 0:
        return float("nan")
    return numerator / denominator


def compute_fundamental_factors(
    facts: pd.DataFrame,
    as_of: str | pd.Timestamp,
) -> pd.DataFrame:
    """Build point-in-time-safe fundamental factors for each symbol.

    The anchor period is the latest revenue report that was visible by ``as_of``.
    Every current-period accounting input must match that exact report period. YoY
    growth additionally requires the same report date one year earlier. Profit
    growth is intentionally left missing when the prior-year profit is non-positive,
    because a simple percentage growth rate is not economically stable across a
    loss/profit sign change.
    """
    visible = available_metrics_as_of(facts, as_of)
    columns = [
        "symbol",
        "fundamental_as_of",
        "fundamental_period_end",
        *FUNDAMENTAL_FACTOR_COLUMNS,
        "fundamental_coverage",
    ]
    if visible.empty:
        return pd.DataFrame(columns=columns)

    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")

    rows: list[dict[str, object]] = []
    for symbol, symbol_facts in visible.groupby("symbol", sort=True):
        anchor = _latest_period(symbol_facts, _METRIC_ALIASES["revenue"])
        if anchor is None:
            continue

        prior_period = anchor - pd.DateOffset(years=1)
        revenue = _value_at(symbol_facts, anchor, _METRIC_ALIASES["revenue"])
        prior_revenue = _value_at(symbol_facts, prior_period, _METRIC_ALIASES["revenue"])
        net_profit = _value_at(symbol_facts, anchor, _METRIC_ALIASES["net_profit"])
        prior_net_profit = _value_at(symbol_facts, prior_period, _METRIC_ALIASES["net_profit"])
        operating_cash_flow = _value_at(
            symbol_facts,
            anchor,
            _METRIC_ALIASES["operating_cash_flow"],
        )
        total_assets = _value_at(symbol_facts, anchor, _METRIC_ALIASES["total_assets"])
        total_liabilities = _value_at(
            symbol_facts,
            anchor,
            _METRIC_ALIASES["total_liabilities"],
        )

        factors = {
            "revenue_yoy": _growth(revenue, prior_revenue),
            "net_profit_yoy": _growth(net_profit, prior_net_profit),
            "net_margin": _ratio(net_profit, revenue),
            "ocf_to_net_profit": _ratio(operating_cash_flow, net_profit),
            "debt_to_assets": _ratio(total_liabilities, total_assets),
        }
        coverage = sum(np.isfinite(value) for value in factors.values()) / len(FUNDAMENTAL_FACTOR_COLUMNS)
        rows.append(
            {
                "symbol": str(symbol).zfill(6),
                "fundamental_as_of": cutoff,
                "fundamental_period_end": anchor,
                **factors,
                "fundamental_coverage": coverage,
            }
        )

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values("symbol").reset_index(drop=True)
