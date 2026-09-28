import pandas as pd

from ashare_system.data.cache import ParquetBarCache


def make_frame(dates, closes):
    rows = len(dates)
    return pd.DataFrame(
        {
            "symbol": ["000001"] * rows,
            "trade_date": dates,
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1000] * rows,
            "amount": [10200] * rows,
            "amplitude_pct": [2.0] * rows,
            "change_pct": [0.0] * rows,
            "change": [0.0] * rows,
            "turnover_pct": [1.1] * rows,
        }
    )


def test_parquet_cache_round_trip(tmp_path):
    cache = ParquetBarCache(tmp_path)
    path = cache.save("000001", make_frame(["2024-01-02"], [10.2]))
    loaded = cache.load("000001")
    assert path.exists()
    assert len(loaded) == 1
    assert loaded.iloc[0]["close"] == 10.2


def test_parquet_cache_upsert_replaces_duplicate_date(tmp_path):
    cache = ParquetBarCache(tmp_path)
    cache.save("000001", make_frame(["2024-01-02", "2024-01-03"], [10.0, 10.1]))
    cache.upsert("000001", make_frame(["2024-01-03", "2024-01-04"], [10.2, 10.3]))
    loaded = cache.load("000001")
    assert loaded["trade_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
    ]
    assert loaded.loc[loaded["trade_date"] == pd.Timestamp("2024-01-03"), "close"].iloc[0] == 10.2
