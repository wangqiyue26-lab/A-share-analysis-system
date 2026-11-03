import pandas as pd
import pytest

from ashare_system.historical_candidate_reconstruction import (
    normalize_amounts,
    normalize_lifecycle,
    normalize_states,
    reconstruct_candidate_day,
)


def _lifecycle():
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "600000", "600001", "600002"],
            "name": ["A", "B", "C", "D", "E"],
            "exchange": ["SZSE", "SZSE", "SSE", "SSE", "SSE"],
            "board": ["深证主板", "深证主板", "上证主板", "上证主板", "上证主板"],
            "list_date": [
                "2020-01-01",
                "2020-01-01",
                "2020-01-01",
                "2020-01-01",
                "2020-01-01",
            ],
            "delist_date": [None, None, None, None, "2024-05-10"],
        }
    )


def _amounts(date="2024-05-09"):
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "600000", "600001", "600002"],
            "trade_date": [date] * 5,
            "amount": [100e6, 90e6, 80e6, 70e6, 60e6],
        }
    )


def _states(date="2024-05-09"):
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "600000", "600001", "600002"],
            "trade_date": [date] * 5,
            "is_st": [False, True, False, False, False],
            "trade_status": [True, True, True, False, True],
            "close": [10, 11, 12, 13, 14],
        }
    )


def test_reconstruction_filters_st_and_suspension_and_keeps_pre_delist_member():
    lifecycle = normalize_lifecycle(_lifecycle())
    amounts = normalize_amounts(_amounts())
    states = normalize_states(_states())
    selected, audit = reconstruct_candidate_day(
        date="2024-05-09",
        lifecycle=lifecycle,
        amount_rows=amounts,
        state_rows=states,
        limit=3,
    )
    assert selected["symbol"].tolist() == ["000001", "600000", "600002"]
    assert audit["candidate_count"] == 3
    assert audit["state_coverage"] == pytest.approx(1.0)
    assert audit["strict_point_in_time_evidence"] is False


def test_delist_date_is_exclusive_for_membership():
    lifecycle = normalize_lifecycle(_lifecycle())
    amounts = normalize_amounts(_amounts("2024-05-10"))
    states = normalize_states(_states("2024-05-10"))
    with pytest.raises(RuntimeError, match="Only 2 reconstructed liquid eligible rows"):
        reconstruct_candidate_day(
            date="2024-05-10",
            lifecycle=lifecycle,
            amount_rows=amounts,
            state_rows=states,
            limit=3,
        )


def test_missing_state_is_fail_closed_even_when_enough_other_symbols_exist():
    lifecycle = normalize_lifecycle(_lifecycle())
    amounts = normalize_amounts(_amounts())
    states = normalize_states(_states().iloc[:-1].copy())
    with pytest.raises(RuntimeError, match="State archive is incomplete"):
        reconstruct_candidate_day(
            date="2024-05-09",
            lifecycle=lifecycle,
            amount_rows=amounts,
            state_rows=states,
            limit=2,
        )


def test_listing_age_filter_matches_production_rule():
    lifecycle = _lifecycle()
    lifecycle.loc[len(lifecycle)] = [
        "300001",
        "NEW",
        "SZSE",
        "创业板",
        "2024-03-01",
        None,
    ]
    amounts = _amounts()
    amounts.loc[len(amounts)] = ["300001", "2024-05-09", 500e6]
    states = _states()
    states.loc[len(states)] = ["300001", "2024-05-09", False, True, 20]

    selected, _ = reconstruct_candidate_day(
        date="2024-05-09",
        lifecycle=normalize_lifecycle(lifecycle),
        amount_rows=normalize_amounts(amounts),
        state_rows=normalize_states(states),
        limit=3,
        min_listing_days=120,
    )
    assert "300001" not in set(selected["symbol"])


def test_exact_amount_tie_is_explicitly_a_reconstruction_difference():
    lifecycle = normalize_lifecycle(_lifecycle())
    amounts = _amounts()
    amounts.loc[0, "amount"] = 100e6
    amounts.loc[2, "amount"] = 100e6
    states = _states()
    states.loc[1, "is_st"] = False
    states.loc[3, "trade_status"] = True
    selected, audit = reconstruct_candidate_day(
        date="2024-05-09",
        lifecycle=lifecycle,
        amount_rows=normalize_amounts(amounts),
        state_rows=normalize_states(states),
        limit=1,
    )
    assert selected.iloc[0]["symbol"] == "000001"
    assert audit["cutoff_amount_tie_count"] == 2
    assert "float_market_cap" in audit["production_sort_difference"]


def test_suspended_state_may_have_missing_close_but_tradable_state_may_not():
    suspended = pd.DataFrame(
        {
            "symbol": ["600000"],
            "trade_date": ["2024-05-09"],
            "is_st": [False],
            "trade_status": [False],
            "close": [None],
        }
    )
    normalized = normalize_states(suspended)
    assert pd.isna(normalized.iloc[0]["close"])

    tradable = suspended.copy()
    tradable["trade_status"] = True
    with pytest.raises(ValueError, match="tradable rows without a positive close"):
        normalize_states(tradable)


def test_missing_amount_for_tradable_non_st_name_fails_closed():
    lifecycle = normalize_lifecycle(_lifecycle())
    amounts = normalize_amounts(_amounts().query("symbol != '600000'"))
    states = normalize_states(_states())
    with pytest.raises(RuntimeError, match="Amount archive is incomplete"):
        reconstruct_candidate_day(
            date="2024-05-09",
            lifecycle=lifecycle,
            amount_rows=amounts,
            state_rows=states,
            limit=2,
        )


def test_missing_amount_for_suspended_or_st_name_does_not_bias_ranking():
    lifecycle = normalize_lifecycle(_lifecycle())
    amounts = normalize_amounts(
        _amounts().query("symbol not in ['000002', '600001']")
    )
    states = normalize_states(_states())
    selected, audit = reconstruct_candidate_day(
        date="2024-05-09",
        lifecycle=lifecycle,
        amount_rows=amounts,
        state_rows=states,
        limit=2,
    )
    assert selected["symbol"].tolist() == ["000001", "600000"]
    assert audit["missing_amount_count"] == 2
    assert audit["missing_rankable_amount_count"] == 0
