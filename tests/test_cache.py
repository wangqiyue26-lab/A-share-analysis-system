import pandas as pd

from ashare_system.data.cache import ParquetBarCache


def test_parquet_cache_round_trip(tmp_path):
    frame = pd.DataFrame(
        {
            "symbol": ["000001"],
            "trade_date": ["2024-01-02"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "volume": [1000],
            "amount": [10200],
            "amplitude_pct": [7.0],
            "change_pct": [2.0],
            "change": [0.2],
            "turnover_pct": [1.1],
        }
    )
    cache = ParquetBarCache(tmp_path)
    path = cache.save("000001", frame)
    loaded = cache.load("000001")
    assert path.exists()
    assert len(loaded) == 1
    assert loaded.iloc[0]["close"] == 10.2
