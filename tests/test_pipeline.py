from datetime import date

import pandas as pd

from ashare_system.data.base import MarketDataProvider
from ashare_system.data.cache import ParquetBarCache
from ashare_system.data.router import DataRouter
from ashare_system.factors.engine import FactorEngine
from ashare_system.factors.scoring import load_factor_config
from ashare_system.pipeline.daily import BatchBarUpdater, DailyResearchPipeline, write_pipeline_result


class SyntheticProvider(MarketDataProvider):
    name = "synthetic"

    def get_daily_bars(self, symbol, start, end, adjust="qfq"):
        dates = pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end))
        base = 10.0 + (int(symbol[-1]) * 0.05)
        closes = pd.Series([base + 0.02 * index for index in range(len(dates))])
        return pd.DataFrame(
            {
                "symbol": [symbol] * len(dates),
                "trade_date": dates,
                "open": closes * 0.997,
                "high": closes * 1.01,
                "low": closes * 0.99,
                "close": closes,
                "volume": [2_000_000] * len(dates),
                "amount": [50_000_000 + int(symbol[-1]) * 1_000_000] * len(dates),
                "amplitude_pct": [2.0] * len(dates),
                "change_pct": closes.pct_change().fillna(0.0) * 100,
                "change": closes.diff().fillna(0.0),
                "turnover_pct": [1.0] * len(dates),
            }
        )


def router_factory():
    return DataRouter([SyntheticProvider()])


def test_daily_pipeline_updates_cache_and_ranks(tmp_path):
    cache = ParquetBarCache(tmp_path / "cache")
    updater = BatchBarUpdater(
        router_factory,
        cache,
        max_workers=2,
        bootstrap_calendar_days=180,
    )
    engine = FactorEngine(load_factor_config("config/factors.yml"), min_average_amount_20=0)
    pipeline = DailyResearchPipeline(updater, engine)
    result = pipeline.run(["000001", "000002", "000003"], date(2024, 6, 28))

    assert len(result.ranking) == 3
    assert set(result.updates["status"]) == {"updated"}
    assert result.ranking["rank"].notna().all()

    output = tmp_path / "output"
    summary = write_pipeline_result(result, output)
    assert summary["ranked_symbols"] == 3
    assert (output / "ranking.csv").exists()
    assert (output / "summary.json").exists()


def test_daily_pipeline_reuses_current_cache(tmp_path):
    cache = ParquetBarCache(tmp_path / "cache")
    updater = BatchBarUpdater(router_factory, cache, max_workers=1, bootstrap_calendar_days=180)
    engine = FactorEngine(load_factor_config("config/factors.yml"), min_average_amount_20=0)
    pipeline = DailyResearchPipeline(updater, engine)
    target = date(2024, 6, 28)
    pipeline.run(["000001"], target)
    second = pipeline.run(["000001"], target)
    assert second.updates.iloc[0]["status"] == "cached"
    assert second.updates.iloc[0]["rows_fetched"] == 0
