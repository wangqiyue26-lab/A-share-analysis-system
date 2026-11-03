import pandas as pd

from ashare_system.historical_amount_probe import build_probe_sample


def test_probe_sample_is_a_share_cohort_balanced_and_deterministic():
    lifecycle = pd.DataFrame(
        [
            {
                "symbol": f"6000{i:02d}",
                "exchange": "SSE",
                "list_date": "2020-01-01",
                "delist_date": None,
                "currently_listed": True,
            }
            for i in range(5)
        ]
        + [
            {
                "symbol": f"0000{i:02d}",
                "exchange": "SZSE",
                "list_date": "2020-01-01",
                "delist_date": None,
                "currently_listed": True,
            }
            for i in range(5)
        ]
        + [
            {
                "symbol": f"6001{i:02d}",
                "exchange": "SSE",
                "list_date": "2010-01-01",
                "delist_date": "2024-06-01",
                "currently_listed": False,
            }
            for i in range(4)
        ]
        + [
            {
                "symbol": f"0001{i:02d}",
                "exchange": "SZSE",
                "list_date": "2010-01-01",
                "delist_date": "2025-06-01",
                "currently_listed": False,
            }
            for i in range(4)
        ]
    )
    first = build_probe_sample(
        lifecycle,
        period_start="2023-01-03",
        period_end="2026-11-03",
        per_cohort=3,
    )
    second = build_probe_sample(
        lifecycle,
        period_start="2023-01-03",
        period_end="2026-11-03",
        per_cohort=3,
    )
    pd.testing.assert_frame_equal(first, second)
    counts = first.groupby("cohort").size().to_dict()
    assert counts == {
        "sse_current": 3,
        "sse_delisted_in_period": 3,
        "szse_current": 3,
        "szse_delisted_in_period": 3,
    }


def test_probe_excludes_delistings_before_research_period():
    lifecycle = pd.DataFrame(
        [
            {
                "symbol": "600001",
                "exchange": "SSE",
                "list_date": "2010-01-01",
                "delist_date": "2022-12-30",
                "currently_listed": False,
            },
            {
                "symbol": "600002",
                "exchange": "SSE",
                "list_date": "2010-01-01",
                "delist_date": "2024-01-01",
                "currently_listed": False,
            },
            {
                "symbol": "000001",
                "exchange": "SZSE",
                "list_date": "1991-01-01",
                "delist_date": None,
                "currently_listed": True,
            },
        ]
    )
    sample = build_probe_sample(
        lifecycle,
        period_start="2023-01-03",
        period_end="2026-11-03",
        per_cohort=5,
    )
    assert "600001" not in set(sample["symbol"])
    assert "600002" in set(sample["symbol"])
