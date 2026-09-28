from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from .base import MarketDataProvider


class DataRouter:
    """Try providers in priority order and fail over when a provider errors."""

    def __init__(self, providers: Iterable[MarketDataProvider]):
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("At least one market-data provider is required")

    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        failures: list[str] = []
        for provider in self.providers:
            try:
                return provider.get_daily_bars(symbol, start, end, adjust)
            except Exception as exc:  # provider boundary: collect and continue
                failures.append(f"{provider.name}: {type(exc).__name__}: {exc}")
        raise RuntimeError("All market-data providers failed: " + " | ".join(failures))
