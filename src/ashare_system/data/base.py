from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class MarketDataProvider(ABC):
    """Interface implemented by every market-data backend."""

    name: str

    @abstractmethod
    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Return normalized daily bars for one A-share symbol."""
        raise NotImplementedError
