import pandas as pd
import pytest

from ashare_system.st_effective_reconciliation import reconcile_st_events


def _event(**overrides):
    row = {
        "symbol": "600001",
        "cohort": "SSE_apply",
        "announcement_time": "2023-05-01",
        "effective_date": "2023-05-04",
        "action": "apply_st",
        "is_st_before": False,
        "is_st_after": True,
        "pdf_url": "https://example.test/provenance.pdf",
        "pdf_sha256": "a" * 64,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _states(**overrides):
    columns = {
        "symbol": ["600001", "600001"],
        "trade_date": ["2023-04-28", "2023-05-04"],
        "is_st": [False, True],
        "trade_status": [True, True],
    }
    columns.update(overrides)
    return pd.DataFrame(columns)


def _verdict(event=None, states=None):
    detail, summary = reconcile_st_events(
        event if event is not None else _event(),
        states if states is not None else _states(),
    )
    assert summary["ready_for_reconstructed_backtest"] is False
    assert summary["full_historical_st_reconciliation_passed"] is False
    assert summary["strict_point_in_time_evidence"] is False
    assert summary["real_money_ready"] is False
    return detail.iloc[0]["verdict"]


def test_matching_independent_effective_and_previous_state_is_only_a_sample():
    assert _verdict() == "sample_match"


def test_cohort_direction_mismatch_blocks_even_if_baostock_matches():
    assert _verdict(_event(cohort="SSE_remove")) == (
        "pdf_direction_conflicts_with_announcement_cohort"
    )


def test_same_day_and_later_disclosures_are_not_treated_as_prior_information():
    assert _verdict(_event(announcement_time="2023-05-04 15:30:00")) == (
        "same_day_disclosure_timing_unverified"
    )
    assert _verdict(_event(announcement_time="2023-05-05")) == (
        "publication_after_effective_date"
    )


def test_contradictory_effective_state_is_reported():
    states = _states(is_st=[False, False])
    assert _verdict(states=states) == "independent_state_conflict"


def test_missing_effective_date_is_not_filled_from_neighboring_day():
    states = _states(trade_date=["2023-04-28", "2023-05-05"])
    assert _verdict(states=states) == "missing_effective_date_state"


def test_missing_prior_and_stale_prior_are_not_promoted():
    states = _states(trade_date=["2023-05-04", "2023-05-05"])
    assert _verdict(states=states) == "missing_or_stale_prior_state"
    states = _states(trade_date=["2023-04-01", "2023-05-04"])
    assert _verdict(states=states) == "missing_or_stale_prior_state"


def test_duplicate_source_records_fail_closed():
    duplicate = pd.concat([_states(), _states().iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate security/date"):
        reconcile_st_events(_event(), duplicate)


def test_missing_pdf_hash_and_bogus_boolean_fail_closed():
    with pytest.raises(ValueError, match="PDF SHA256"):
        reconcile_st_events(_event(pdf_sha256="fake"), _states())
    with pytest.raises(ValueError, match="Invalid ST boolean"):
        reconcile_st_events(_event(is_st_before="maybe"), _states())
