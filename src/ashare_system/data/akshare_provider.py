from __future__ import annotations

from typing import ClassVar

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import MarketDataProvider
from .schema import validate_bars


class AkshareProvider(MarketDataProvider):
    name = "akshare"

    _COLUMN_MAP: ClassVar[dict[str, str]] = {
        "日期": "trade_date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude_pct",
        "涨跌幅": "change_pct",
        "涨跌额": "change",
        "换手率": "turnover_pct",
    }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Fetch A-share daily history through AKShare and normalize it."""
        import akshare as ak

        raw = ak.stock_zh_a_hist(
            symbol=str(symbol).zfill(6),
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
        )
        if raw is None or raw.empty:
            raise RuntimeError(f"AKShare returned no daily bars for {symbol} ({start}..{end})")

        frame = raw.rename(columns=self._COLUMN_MAP).copy()
        frame["symbol"] = str(symbol).zfill(6)
        return validate_bars(frame)
