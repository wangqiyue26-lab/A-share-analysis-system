import pandas as pd
import pytest

from ashare_system.data.akshare_sina_provider import normalize_sina_daily, to_sina_symbol


def test_to_sina_symbol():
    assert to_sina_symbol("600000") == "sh600000"
    assert to_sina_symbol("000001") == "sz000001"
    with pytest.raises(ValueError):
        to_sina_symbol("830001")


def test_normalize_sina_daily_computes_missing_metrics():
    raw = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "open": [10.0, 10.2],
            "high": [10.4, 10.5],
            "low": [9.9, 10.1],
            "close": [10.2, 10.4],
            "volume": [1000, 1100],
            "amount": [10200, 11440],
            "outstanding_share": [100000, 100000],
            "turnover": [0.01, 0.011],
        }
    )
    result = normalize_sina_daily(raw, "000001")
    assert len(result) == 2
    assert result.iloc[1]["change"] == pytest.approx(0.2)
    assert result.iloc[1]["turnover_pct"] == pytest.approx(1.1)
