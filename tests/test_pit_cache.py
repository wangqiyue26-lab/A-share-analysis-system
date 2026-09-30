import pandas as pd
import pytest

from ashare_system.data.pit_cache import ParquetPointInTimeCache


def _facts(value: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001"],
            "metric": ["利润表::净利润"],
            "value": [value],
            "period_end": ["2024-06-30"],
            "available_at": ["2024-08-30"],
            "source": ["demo"],
        }
    )


def test_pit_cache_round_trip_and_freshness(tmp_path):
    cache = ParquetPointInTimeCache(tmp_path)
    refreshed = pd.Timestamp("2026-09-30T01:00:00Z")
    cache.upsert("000001", _facts(), refreshed_at=refreshed)

    loaded = cache.load("000001")
    assert len(loaded) == 1
    assert loaded.iloc[0]["value"] == pytest.approx(10.0)
    assert cache.is_fresh(
        "000001",
        max_age_days=7,
        now=pd.Timestamp("2026-10-05T01:00:00Z"),
    )
    assert not cache.is_fresh(
        "000001",
        max_age_days=7,
        now=pd.Timestamp("2026-10-10T01:00:00Z"),
    )


def test_pit_cache_rejects_silent_value_rewrite_at_same_availability_identity(tmp_path):
    cache = ParquetPointInTimeCache(tmp_path)
    cache.upsert("000001", _facts(10.0))

    with pytest.raises(ValueError, match="Conflicting PIT values"):
        cache.upsert("000001", _facts(11.0))
