"""Regression tests for delayed scheduled jobs; never backfill after next preopen."""

import pandas as pd
import pytest

from ashare_system.after_close_clock import evaluate_after_close


@pytest.mark.parametrize(
    ("signal_date", "now", "eligible", "reason"),
    [
        ("2026-11-03", "2026-11-03 14:59", False, "before_signal_close_or_future_signal"),
        ("2026-11-03", "2026-11-03 15:10", True, "eligible_after_close"),
        ("2026-11-03", "2026-11-03 23:59", True, "eligible_after_close"),
        ("2026-11-03", "2026-11-04 01:15", True, "eligible_overnight"),
        ("2026-11-04", "2026-11-05 01:24", True, "eligible_overnight"),
        ("2026-11-03", "2026-11-04 09:14", True, "eligible_overnight"),
        ("2026-11-03", "2026-11-04 09:15", False, "expired_before_preopen"),
        ("2026-11-03", "2026-11-04 16:00", False, "expired_before_preopen"),
        ("2026-11-03", "2026-11-05 01:15", False, "expired_before_preopen"),
        ("2026-11-03", "2026-11-02 16:00", False, "before_signal_close_or_future_signal"),
        ("2026-11-06", "2026-11-07 01:20", True, "eligible_overnight"),
        ("2026-11-07", "2026-11-07 16:00", False, "non_weekday_signal"),
    ],
)
def test_delayed_signal_gate(signal_date, now, eligible, reason):
    decision = evaluate_after_close(
        signal_date,
        now=pd.Timestamp(now, tz="Asia/Shanghai"),
    )
    assert decision.eligible is eligible
    assert decision.reason == reason
    assert decision.signal_date == signal_date


def test_utc_timestamp_converts_to_china_time():
    # 17:24 UTC is the following day at 01:24 in mainland China.
    decision = evaluate_after_close(
        "2026-11-04", now=pd.Timestamp("2026-11-04 17:24:00+00:00")
    )
    assert decision.eligible
    assert decision.reason == "eligible_overnight"
    assert decision.evaluated_at.startswith("2026-11-05T01:24")


def test_naive_timestamp_is_interpreted_as_china_local():
    decision = evaluate_after_close(
        "2026-11-04", now=pd.Timestamp("2026-11-05 01:24")
    )
    assert decision.eligible
