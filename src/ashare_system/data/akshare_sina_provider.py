from __future__ import annotations

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import MarketDataProvider
from .schema import validate_bars


def to_sina_symbol(symbol: str) -> str:
    code = str(symbol).zfill(6)
    if code.startswith(("5", "6")):
        return f"sh{code}"
    if code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    raise ValueError(f"Sina fallback currently supports Shanghai/Shenzhen symbols only: {code}")


def normalize_sina_daily(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise RuntimeError(f"AKShare Sina returned no daily bars for {symbol}")

    frame = raw.rename(columns={"date": "trade_date"}).copy()
    required = {"trade_date", "open", "high", "low", "close", "volume", "amount"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Sina payload missing columns: {missing}")

    previous_close = pd.to_numeric(frame["close"], errors="coerce").shift(1)
    frame["change"] = pd.to_numeric(frame["close"], errors="coerce") - previous_close
    frame["change_pct"] = frame["change"] / previous_close * 100
    frame["amplitude_pct"] = (
        (pd.to_numeric(frame["high"], errors="coerce") - pd.to_numeric(frame["low"], errors="coerce"))
        / previous_close
        * 100
    )
    if "turnover" in frame.columns:
        frame["turnover_pct"] = pd.to_numeric(frame["turnover"], errors="coerce") * 100
    else:
        frame["turnover_pct"] = float("nan")
    frame["symbol"] = str(symbol).zfill(6)
    return validate_bars(frame)


class AkshareSinaProvider(MarketDataProvider):
    """AKShare daily bars backed by Sina, used as a cloud fallback."""

    name = "akshare_sina"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        import akshare as ak

        raw = ak.stock_zh_a_daily(
            symbol=to_sina_symbol(symbol),
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
        )
        return normalize_sina_daily(raw, symbol)
