from __future__ import annotations

import pandas as pd

PIT_COLUMNS = (
    "symbol",
    "metric",
    "value",
    "period_end",
    "available_at",
    "source",
)


def validate_point_in_time_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate long-form financial/fundamental observations with explicit availability time."""
    missing = [column for column in PIT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing point-in-time columns: {missing}")

    result = frame.loc[:, PIT_COLUMNS].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["metric"] = result["metric"].astype(str)
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result["period_end"] = pd.to_datetime(result["period_end"], utc=True, errors="raise")
    result["available_at"] = pd.to_datetime(result["available_at"], utc=True, errors="raise")
    result["source"] = result["source"].astype(str)

    if result["value"].isna().any():
        raise ValueError("Point-in-time metric values must be numeric")
    if (result["available_at"] < result["period_end"]).any():
        raise ValueError("available_at cannot precede period_end")

    key = ["symbol", "metric", "period_end", "available_at", "source"]
    if result.duplicated(key).any():
        raise ValueError("Duplicate point-in-time observations detected")
    return result.sort_values(["symbol", "metric", "period_end", "available_at", "source"]).reset_index(drop=True)


def available_metrics_as_of(frame: pd.DataFrame, as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Return every report-period metric version that was actually visible at a cutoff.

    When a metric/report-period has multiple revisions, only the latest revision that
    had become available by ``as_of`` is retained. Future announcements and future
    restatements are excluded before de-duplication.
    """
    data = validate_point_in_time_metrics(frame)
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")

    eligible = data[(data["available_at"] <= cutoff) & (data["period_end"] <= cutoff)].copy()
    if eligible.empty:
        return eligible

    eligible = eligible.sort_values(
        ["symbol", "metric", "period_end", "available_at", "source"],
        ascending=True,
    )
    return eligible.drop_duplicates(["symbol", "metric", "period_end"], keep="last").reset_index(drop=True)


def latest_metrics_as_of(frame: pd.DataFrame, as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Return the latest actually-available metric for every symbol/metric at a cutoff."""
    eligible = available_metrics_as_of(frame, as_of)
    if eligible.empty:
        return eligible

    eligible = eligible.sort_values(
        ["symbol", "metric", "period_end", "available_at", "source"],
        ascending=True,
    )
    return eligible.drop_duplicates(["symbol", "metric"], keep="last").reset_index(drop=True)


def pivot_latest_metrics(frame: pd.DataFrame, as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Create a symbol × metric matrix from point-in-time-safe latest observations."""
    latest = latest_metrics_as_of(frame, as_of)
    if latest.empty:
        return pd.DataFrame()
    return latest.pivot(index="symbol", columns="metric", values="value").sort_index()
