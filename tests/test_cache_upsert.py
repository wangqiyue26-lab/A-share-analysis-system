import pandas as pd

from ashare_system.data.cache import ParquetBarCache


def _bars(dates, closes):
    rows = []
    for date, close in zip(dates, closes, strict=True):
        rows.append(
            {
                "symbol": "000001",
                "trade_date": date,
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1_000_000,
                "amount": close * 1_000_000,
                "amplitude_pct": 2.0,
                "change_pct": 0.0,
                "change": 0.0,
                "turnover_pct": 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_upsert_extends_cache_and_replaces_duplicate_date(tmp_path):
    cache = ParquetBarCache(tmp_path)
    cache.save("000001", _bars(["2026-09-25", "2026-09-28"], [10.0, 10.1]))
    cache.upsert("000001", _bars(["2026-09-28", "2026-09-29"], [10.2, 10.3]))
    frame = cache.load("000001")
    assert len(frame) == 3
    assert frame.loc[frame["trade_date"] == pd.Timestamp("2026-09-28"), "close"].iloc[0] == 10.2
    assert frame.iloc[-1]["close"] == 10.3
