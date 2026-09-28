import pandas as pd

from ashare_system.data.base import MarketDataProvider
from ashare_system.data.router import DataRouter
from ashare_system.data.schema import validate_bars


class BrokenProvider(MarketDataProvider):
    name = "broken"

    def get_daily_bars(self, symbol, start, end, adjust="qfq"):
        raise ConnectionError("synthetic provider outage")


class WorkingProvider(MarketDataProvider):
    name = "working"

    def get_daily_bars(self, symbol, start, end, adjust="qfq"):
        return validate_bars(
            pd.DataFrame(
                {
                    "symbol": [symbol],
                    "trade_date": ["2024-01-02"],
                    "open": [10],
                    "high": [11],
                    "low": [9],
                    "close": [10.5],
                    "volume": [100],
                    "amount": [1050],
                    "amplitude_pct": [20],
                    "change_pct": [5],
                    "change": [0.5],
                    "turnover_pct": [1],
                }
            )
        )


def test_router_fails_over_to_next_provider():
    router = DataRouter([BrokenProvider(), WorkingProvider()])
    bars = router.get_daily_bars("000001", "20240101", "20240103")
    assert len(bars) == 1
    assert bars.iloc[0]["symbol"] == "000001"
    assert router.last_provider_name == "working"
