from __future__ import annotations

from datetime import date

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential


def normalize_trade_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a trade-calendar payload to unique ascending midnight timestamps."""
    if "trade_date" not in frame.columns:
        raise ValueError("Trade calendar must contain a trade_date column")
    dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna().dt.normalize()
    if dates.empty:
        raise ValueError("Trade calendar contains no valid dates")
    return pd.DataFrame({"trade_date": sorted(dates.unique())})


def latest_trade_date_on_or_before(frame: pd.DataFrame, day: date | str | pd.Timestamp) -> date:
    """Resolve the latest known A-share trading day at or before a calendar date."""
    calendar = normalize_trade_calendar(frame)
    target = pd.Timestamp(day).normalize()
    eligible = calendar.loc[calendar["trade_date"] <= target, "trade_date"]
    if eligible.empty:
        raise ValueError(f"No trading date on or before {target.date().isoformat()}")
    return eligible.iloc[-1].date()


class AkshareSinaTradeCalendarProvider:
    name = "akshare_sina_trade_calendar"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_calendar(self) -> pd.DataFrame:
        import akshare as ak

        return normalize_trade_calendar(ak.tool_trade_date_hist_sina())
