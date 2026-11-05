import pandas as pd
import pytest

from ashare_system.historical_amount_recovery import check_recovered_symbol


def _state():
    return pd.DataFrame({
        "symbol": ["600001", "600001", "600001"],
        "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "is_st": [False, True, False],
        "trade_status": [True, True, False],
        "close": [10.0, 11.0, None],
    })


def _raw():
    return pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "code": ["sh.600001"] * 3,
        "amount": ["101000000", "", ""],
        "tradestatus": ["1", "1", "0"],
        "isST": ["0", "1", "0"],
    })


def test_recovery_keeps_exact_amount_and_skips_unknown_nonrankable_rows():
    rows, summary = check_recovered_symbol(_raw(), _state(), symbol="600001")
    assert rows["trade_date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-02"]
    assert rows.iloc[0]["amount"] == 101000000
    assert summary["rankable_state_dates"] == 1
    assert summary["unresolved_rankable_dates"] == 0
    assert summary["status"] == "complete"


def test_missing_amount_on_rankable_date_is_explicitly_unresolved():
    raw = _raw()
    raw.loc[0, "amount"] = ""
    _, summary = check_recovered_symbol(raw, _state(), symbol="600001")
    assert summary["status"] == "incomplete"
    assert summary["unresolved_rankable_dates"] == 1
    assert summary["unresolved_examples"] == ["2024-01-02"]


def test_recovery_refuses_state_mismatch():
    raw = _raw()
    raw.loc[0, "isST"] = "1"
    with pytest.raises(ValueError, match="status mismatches"):
        check_recovered_symbol(raw, _state(), symbol="600001")


def test_recovery_refuses_negative_amount():
    raw = _raw()
    raw.loc[0, "amount"] = "-1"
    with pytest.raises(ValueError, match="negative BaoStock amount"):
        check_recovered_symbol(raw, _state(), symbol="600001")


def test_recovery_refuses_duplicate_dates():
    raw = pd.concat([_raw(), _raw().iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="invalid or duplicate"):
        check_recovered_symbol(raw, _state(), symbol="600001")
