import pandas as pd

from ashare_system.historical_amount_archive import shard_for_symbol, select_shard


def test_shard_assignment_is_deterministic_and_bounded():
    first = shard_for_symbol("600000", 32)
    second = shard_for_symbol("600000", 32)
    assert first == second
    assert 0 <= first < 32


def test_select_shard_partitions_relevant_lifecycle_without_overlap():
    lifecycle = pd.DataFrame(
        [
            {
                "symbol": f"{600000 + index:06d}",
                "list_date": "2020-01-01",
                "delist_date": None,
            }
            for index in range(30)
        ]
        + [
            {
                "symbol": f"{1 + index:06d}",
                "list_date": "2010-01-01",
                "delist_date": "2024-06-01",
            }
            for index in range(10)
        ]
        + [
            {
                "symbol": "600999",
                "list_date": "2010-01-01",
                "delist_date": "2022-12-30",
            }
        ]
    )
    shards = [
        select_shard(
            lifecycle,
            shard_count=4,
            shard_index=index,
            period_start="2023-01-03",
            period_end="2026-11-03",
        )
        for index in range(4)
    ]
    symbols = [set(frame["symbol"]) for frame in shards]
    union = set().union(*symbols)
    assert "600999" not in union
    assert len(union) == 40
    for left in range(4):
        for right in range(left + 1, 4):
            assert symbols[left].isdisjoint(symbols[right])
