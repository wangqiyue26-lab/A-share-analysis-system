import csv

import pandas as pd
import pytest

from ashare_system.st_exception_top60_impact import audit_exception_top60_impact


def _events():
    return pd.DataFrame(
        [
            {
                "symbol": "200016",
                "change_date": "2024-05-06",
                "old_name": "深康佳Ｂ",
                "new_name": "*ST康佳B",
                "verdict": "missing_exact_change_day_state",
            },
            {
                "symbol": "000002",
                "change_date": "2024-05-06",
                "old_name": "*ST样本",
                "new_name": "样本退",
                "verdict": "delisting_rename_not_st_removal_evidence",
            },
            {
                "symbol": "300280",
                "change_date": "2024-05-06",
                "old_name": "样本",
                "new_name": "*ST样本",
                "verdict": "status_on_suspended_day_requires_review",
            },
        ]
    )


def _candidate_file(directory, day, selected):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{day}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol"])
        writer.writeheader()
        for symbol in selected + [f"{i:06d}" for i in range(100000, 100060 - len(selected))]:
            writer.writerow({"symbol": symbol})


def _lifecycle():
    return pd.DataFrame({"symbol": ["000002", "300280", "000001"]})


def test_exception_symbols_do_not_affect_later_top60(tmp_path):
    directory = tmp_path / "candidates"
    _candidate_file(directory, "2024-05-07", [])
    detail, result = audit_exception_top60_impact(_events(), _lifecycle(), directory)
    assert len(detail) == 3
    assert result["post_exception_candidate_membership_hits"] == 0
    assert result["b_share_missing_states_not_in_a_universe"] == 1
    assert result["ready_for_reconstructed_backtest"] is False


def test_candidate_preceding_delisting_rename_does_not_count_as_post_event_hit(tmp_path):
    directory = tmp_path / "candidates"
    _candidate_file(directory, "2024-05-03", ["000002"])
    _, result = audit_exception_top60_impact(_events(), _lifecycle(), directory)
    assert result["post_exception_candidate_membership_hits"] == 0


def test_candidate_selected_after_rename_is_explicitly_flagged(tmp_path):
    directory = tmp_path / "candidates"
    _candidate_file(directory, "2024-05-07", ["000002"])
    _, result = audit_exception_top60_impact(_events(), _lifecycle(), directory)
    assert result["post_exception_candidate_membership_hits"] == 1
    assert result["hit_records"][0]["symbol"] == "000002"
    assert result["ready_for_reconstructed_backtest"] is False


def test_unexplained_missing_A_share_state_refuses_B_share_classification(tmp_path):
    directory = tmp_path / "candidates"
    _candidate_file(directory, "2024-05-07", [])
    bad = _events()
    bad.loc[0, "symbol"] = "300001"
    with pytest.raises(RuntimeError, match="out-of-scope B shares"):
        audit_exception_top60_impact(bad, _lifecycle(), directory)


def test_B_share_in_lifecycle_is_not_automatically_dismissed(tmp_path):
    directory = tmp_path / "candidates"
    _candidate_file(directory, "2024-05-07", [])
    lifecycle = pd.concat([
        _lifecycle(), pd.DataFrame({"symbol": ["200016"]})
    ], ignore_index=True)
    with pytest.raises(RuntimeError, match="out-of-scope B shares"):
        audit_exception_top60_impact(_events(), lifecycle, directory)
