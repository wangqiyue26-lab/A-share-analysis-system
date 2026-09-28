import pandas as pd

from ashare_system.fundamentals import point_in_time


def _facts() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "000001",
                "report_period": "2024-03-31",
                "announcement_date": "2024-04-26",
                "statement": "利润表",
                "item": "净利润",
                "value": 100.0,
                "currency": "CNY",
                "audited": "否",
                "source_update_date": "2024-04-26",
                "source": "test",
            },
            {
                "symbol": "000001",
                "report_period": "2024-03-31",
                "announcement_date": "2024-04-26",
                "statement": "利润表",
                "item": "净利润",
                "value": 110.0,
                "currency": "CNY",
                "audited": "否",
                "source_update_date": "2024-06-10",
                "source": "test",
            },
        ]
    )


def test_point_in_time_blocks_future_announcement():
    assert point_in_time(_facts(), "2024-04-20").empty


def test_point_in_time_blocks_future_revision():
    visible = point_in_time(_facts(), "2024-05-01")
    assert len(visible) == 1
    assert visible.iloc[0]["value"] == 100.0


def test_point_in_time_uses_revision_after_update_date():
    visible = point_in_time(_facts(), "2024-06-11")
    assert len(visible) == 1
    assert visible.iloc[0]["value"] == 110.0
