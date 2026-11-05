import pandas as pd
import pytest

from ashare_system.historical_szse_st_audit import audit_szse_st_transitions


def _changes(**overrides):
    columns = {
        "symbol": ["002001"],
        "change_date": ["2024-05-06"],
        "old_name": ["某某股份"],
        "new_name": ["*ST某某"],
        "is_st_before": [False],
        "is_st_after": [True],
        "st_transition": [True],
        "source": ["szse_official_abbreviation_change"],
    }
    columns.update(overrides)
    return pd.DataFrame(columns)


def _states(**overrides):
    columns = {
        "symbol": ["002001", "002001"],
        "trade_date": ["2024-04-30", "2024-05-06"],
        "is_st": [False, True],
        "trade_status": [True, True],
    }
    columns.update(overrides)
    return pd.DataFrame(columns)


def _verdict(changes=None, states=None):
    detail, summary = audit_szse_st_transitions(
        changes if changes is not None else _changes(),
        states if states is not None else _states(),
        first_date="2024-04-30",
        last_date="2024-05-07",
    )
    assert summary["ready_for_reconstructed_backtest"] is False
    assert summary["disclosure_publication_times_checked"] is False
    assert summary["strict_point_in_time_evidence"] is False
    assert summary["sse_coverage"] == "not_in_scope"
    return detail.iloc[0]["verdict"]


def test_official_change_and_independent_st_agree_but_do_not_approve_pit():
    assert _verdict() == "historical_state_matches_official_change"


def test_independent_state_conflict_is_not_overridden():
    assert _verdict(states=_states(is_st=[False, False])) == (
        "official_vs_baostock_state_conflict"
    )


def test_delisting_rename_does_not_count_as_removing_st():
    changes = _changes(
        old_name=["*ST某某"],
        new_name=["某某退"],
        is_st_before=[True],
        is_st_after=[False],
    )
    assert _verdict(changes=changes) == (
        "delisting_rename_not_st_removal_evidence"
    )


def test_status_on_suspended_day_requires_review():
    assert _verdict(states=_states(trade_status=[True, False])) == (
        "status_on_suspended_day_requires_review"
    )


def test_missing_or_stale_previous_state_blocks_transition():
    assert _verdict(states=_states(trade_date=["2024-05-06", "2024-05-07"])) == (
        "missing_or_stale_previous_state"
    )
    assert _verdict(states=_states(trade_date=["2024-04-01", "2024-05-06"])) == (
        "missing_or_stale_previous_state"
    )


def test_missing_exact_date_state_is_not_synthesized():
    assert _verdict(states=_states(trade_date=["2024-04-30", "2024-05-07"])) == (
        "missing_exact_change_day_state"
    )


def test_duplicate_official_events_fail_closed():
    rows = pd.concat([_changes(), _changes()], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate official transition"):
        audit_szse_st_transitions(rows, _states(), first_date="2024-01-01", last_date="2024-12-31")


def test_wrong_official_source_fails_closed():
    with pytest.raises(ValueError, match="Unexpected abbreviation change source"):
        audit_szse_st_transitions(
            _changes(source=["some_unverified_source"]), _states(),
            first_date="2024-01-01", last_date="2024-12-31",
        )


def test_false_st_transition_is_not_processed_as_event():
    with pytest.raises(ValueError, match="No historical SZSE ST transitions"):
        audit_szse_st_transitions(
            _changes(st_transition=[False]), _states(),
            first_date="2024-01-01", last_date="2024-12-31",
        )
