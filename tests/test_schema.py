import pandas as pd
import pytest

from ashare_system.data.schema import BAR_COLUMNS, validate_bars


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["1", "1"],
            "trade_date": ["2024-01-03", "2024-01-02"],
            "open": [10.1, 10.0],
            "high": [10.4, 10.2],
            "low": [10.0, 9.9],
            "close": [10.3, 10.1],
            "volume": [1000, 900],
            "amount": [10300, 9090],
            "amplitude_pct": [4.0, 3.0],
            "change_pct": [2.0, 1.0],
            "change": [0.2, 0.1],
            "turnover_pct": [1.2, 1.0],
        }
    )


def test_validate_bars_normalizes_symbol_and_sort_order():
    result = validate_bars(sample_frame())
    assert tuple(result.columns) == BAR_COLUMNS
    assert result["symbol"].tolist() == ["000001", "000001"]
    assert result["trade_date"].is_monotonic_increasing


def test_validate_bars_rejects_missing_columns():
    with pytest.raises(ValueError, match="Missing canonical bar columns"):
        validate_bars(sample_frame().drop(columns=["amount"]))


def test_validate_bars_rejects_multiple_symbols():
    frame = sample_frame()
    frame.loc[1, "symbol"] = "2"
    with pytest.raises(ValueError, match="exactly one symbol"):
        validate_bars(frame)
