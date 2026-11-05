"""Conservative after-close clock for immutable forward research evidence.

Scheduled GitHub Actions runs can start after midnight in China. A signal from
the preceding calendar day can still be recorded before the next morning's
pre-open cutoff, provided no later trading session has begun. This is NOT a
license to backfill past days or to reclassify reconstructed history as live PIT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

CHINA_TZ = ZoneInfo("Asia/Shanghai")
MARKET_CLOSE_CUTOFF = time(15, 10)
NEXT_CALENDAR_DAY_PREOPEN_CUTOFF = time(9, 15)


@dataclass(frozen=True)
class AfterCloseDecision:
    eligible: bool
    reason: str
    signal_date: str
    evaluated_at: str
    earliest_at: str
    expires_at: str


def evaluate_after_close(
    signal_date: str | date | pd.Timestamp,
    *,
    now: pd.Timestamp | None = None,
) -> AfterCloseDecision:
    """Accept only a closed weekday signal before the following day's 09:15 CST.

    The next-calendar-day limit is intentionally more restrictive than a true
    exchange holiday calendar. Missing a delayed run is preferable to silently
    accepting stale or future-informed signals.
    """
    day = pd.Timestamp(signal_date).date()
    current = pd.Timestamp.now(tz=CHINA_TZ) if now is None else pd.Timestamp(now)
    current = current.tz_localize(CHINA_TZ) if current.tzinfo is None else current.tz_convert(
        CHINA_TZ
    )
    earliest = pd.Timestamp(datetime.combine(day, MARKET_CLOSE_CUTOFF), tz=CHINA_TZ)
    expires = pd.Timestamp(
        datetime.combine(day + timedelta(days=1), NEXT_CALENDAR_DAY_PREOPEN_CUTOFF),
        tz=CHINA_TZ,
    )

    if day.weekday() >= 5:
        reason = "non_weekday_signal"
    elif current < earliest:
        reason = "before_signal_close_or_future_signal"
    elif current >= expires:
        reason = "expired_before_preopen"
    elif current.date() == day:
        reason = "eligible_after_close"
    else:
        reason = "eligible_overnight"

    return AfterCloseDecision(
        eligible=reason.startswith("eligible_"),
        reason=reason,
        signal_date=day.isoformat(),
        evaluated_at=current.isoformat(),
        earliest_at=earliest.isoformat(),
        expires_at=expires.isoformat(),
    )
