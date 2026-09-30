import pandas as pd
import pytest

from ashare_system.data.point_in_time import (
    available_metrics_as_of,
    latest_metrics_as_of,
    pivot_latest_metrics,
    validate_point_in_time_metrics,
)


def sample_pit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001", "000001", "000001", "600000"],
            "metric": ["roe", "roe", "roe", "roe"],
            "value": [0.10, 0.12, 0.125, 0.08],
            "period_end": ["2023-12-31", "2024-03-31", "2024-03-31", "2023-12-31"],
            "available_at": ["2024-03-20", "2024-04-30", "2024-05-15", "2024-03-25"],
            "source": ["demo", "demo", "restatement", "demo"],
        }
    )


def test_available_metrics_as_of_keeps_report_history_and_latest_visible_revision():
    visible = available_metrics_as_of(sample_pit(), "2024-05-10")
    pingan = visible[visible["symbol"] == "000001"].sort_values("period_end")
    assert pingan["value"].tolist() == pytest.approx([0.10, 0.12])

    revised = available_metrics_as_of(sample_pit(), "2024-06-01")
    march = revised[
        (revised["symbol"] == "000001")
        & (revised["period_end"] == pd.Timestamp("2024-03-31", tz="UTC"))
    ].iloc[0]
    assert march["value"] == pytest.approx(0.125)
    assert march["available_at"] == pd.Timestamp("2024-05-15", tz="UTC")


def test_latest_metrics_as_of_excludes_future_announcements():
    latest = latest_metrics_as_of(sample_pit(), "2024-04-15")
    pingan = latest[latest["symbol"] == "000001"].iloc[0]
    assert pingan["value"] == pytest.approx(0.10)
    assert pingan["period_end"] == pd.Timestamp("2023-12-31", tz="UTC")


def test_latest_metrics_as_of_uses_later_report_and_restatement_when_available():
    latest = latest_metrics_as_of(sample_pit(), "2024-06-01")
    pingan = latest[latest["symbol"] == "000001"].iloc[0]
    assert pingan["value"] == pytest.approx(0.125)
    wide = pivot_latest_metrics(sample_pit(), "2024-06-01")
    assert wide.loc["000001", "roe"] == pytest.approx(0.125)


def test_point_in_time_rejects_impossible_availability():
    bad = sample_pit().iloc[[0]].copy()
    bad["available_at"] = "2023-01-01"
    with pytest.raises(ValueError, match="cannot precede"):
        validate_point_in_time_metrics(bad)
