from datetime import date

import pandas as pd
import pytest

from ashare_system.data.calendar import latest_trade_date_on_or_before, normalize_trade_calendar


def test_trade_calendar_normalization_and_latest_date():
    raw = pd.DataFrame({"trade_date": ["2024-01-05", "2024-01-02", "2024-01-05"]})
    calendar = normalize_trade_calendar(raw)
    assert calendar["trade_date"].tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-05")]
    assert latest_trade_date_on_or_before(calendar, date(2024, 1, 7)) == date(2024, 1, 5)


def test_latest_trade_date_rejects_pre_history_cutoff():
    calendar = pd.DataFrame({"trade_date": ["2024-01-02"]})
    with pytest.raises(ValueError, match="No trading date"):
        latest_trade_date_on_or_before(calendar, "2023-12-31")
