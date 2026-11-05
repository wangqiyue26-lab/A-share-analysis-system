"""Reconcile retrospectively verified ST PDF events against an independent daily state archive.

This is a *sample diagnostic* only. It never upgrades reconstructed status
to contemporaneously observed PIT evidence, and never edits either source.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

REQUIRED_EVENTS = {
    "symbol", "cohort", "announcement_time", "effective_date", "action",
    "is_st_before", "is_st_after", "pdf_url", "pdf_sha256",
}
REQUIRED_STATES = {"symbol", "trade_date", "is_st", "trade_status"}


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"true", "1"}:
        return True
    if str(value).strip().lower() in {"false", "0"}:
        return False
    raise ValueError(f"Invalid ST boolean: {value!r}")


def _date(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError(f"Invalid event date: {value!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("Asia/Shanghai").tz_localize(None)
    return parsed.normalize()


def reconcile_st_events(
    verified: pd.DataFrame,
    states: pd.DataFrame,
    *,
    max_prior_age_days: int = 20,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Check disclosure chronology, cohort direction and exact state transitions.

    Exact effective-day evidence and the *previous* recorded market day
    are both required. A matching sample is NOT a whole-market validation.
    """
    if max_prior_age_days <= 0:
        raise ValueError("max_prior_age_days must be positive")
    missing = REQUIRED_EVENTS - set(verified.columns)
    if missing:
        raise ValueError(f"Verified events lack columns: {sorted(missing)}")
    missing = REQUIRED_STATES - set(states.columns)
    if missing:
        raise ValueError(f"Daily state archive lacks columns: {sorted(missing)}")
    if verified.empty:
        raise ValueError("Cannot certify an empty PDF event sample")

    events = verified.copy()
    events["symbol"] = events["symbol"].astype(str).str.zfill(6)
    if events[["symbol", "effective_date", "pdf_sha256"]].duplicated().any():
        raise ValueError("Duplicate verified PDF event keys")
    observed = states[states["symbol"].astype(str).str.zfill(6).isin(events["symbol"])].copy()
    observed["symbol"] = observed["symbol"].astype(str).str.zfill(6)
    observed["trade_date"] = pd.to_datetime(
        observed["trade_date"], errors="coerce"
    ).dt.normalize()
    if observed["trade_date"].isna().any():
        raise ValueError("Daily ST archive contains invalid dates")
    if observed.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Daily ST archive contains duplicate security/date rows")
    groups = {
        symbol: frame.sort_values("trade_date")
        for symbol, frame in observed.groupby("symbol")
    }

    rows = []
    for event in events.itertuples(index=False):
        symbol = str(event.symbol).zfill(6)
        effective = _date(event.effective_date)
        publication = pd.Timestamp(event.announcement_time)
        if pd.isna(publication):
            raise ValueError(f"Invalid publication time for {symbol}")
        if publication.tzinfo is not None:
            publication = publication.tz_convert("Asia/Shanghai").tz_localize(None)
        expected_before = _bool(event.is_st_before)
        expected_after = _bool(event.is_st_after)
        if expected_before == expected_after:
            raise ValueError(f"No ST boolean transition for {symbol}")
        expected_action = "apply_st" if expected_after else "remove_st"
        cohort = str(event.cohort)
        cohort_action = "apply_st" if cohort.endswith("_apply") else (
            "remove_st" if cohort.endswith("_remove") else "unknown"
        )
        event_action = str(event.action)
        digest = str(event.pdf_sha256)
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"Missing immutable PDF SHA256 for {symbol}")
        if not str(event.pdf_url).startswith("http"):
            raise ValueError(f"Missing PDF source URL for {symbol}")

        series = groups.get(symbol)
        before = None
        at = None
        prior_date = None
        prior_age = None
        if series is not None:
            prev = series[series["trade_date"].lt(effective)].tail(1)
            same = series[series["trade_date"].eq(effective)]
            if not prev.empty:
                before = _bool(prev.iloc[0]["is_st"])
                prior_date = prev.iloc[0]["trade_date"].date().isoformat()
                prior_age = int((effective - prev.iloc[0]["trade_date"]).days)
            if len(same) == 1:
                at = _bool(same.iloc[0]["is_st"])

        if publication.normalize() > effective:
            verdict = "publication_after_effective_date"
        elif publication.normalize() == effective:
            verdict = "same_day_disclosure_timing_unverified"
        elif cohort_action != expected_action or event_action != expected_action:
            verdict = "pdf_direction_conflicts_with_announcement_cohort"
        elif at is None:
            verdict = "missing_effective_date_state"
        elif before is None or prior_age is None or prior_age > max_prior_age_days:
            verdict = "missing_or_stale_prior_state"
        elif before != expected_before or at != expected_after:
            verdict = "independent_state_conflict"
        else:
            verdict = "sample_match"

        rows.append({
            "symbol": symbol,
            "cohort": cohort,
            "announcement_time": str(event.announcement_time),
            "effective_date": effective.date().isoformat(),
            "pdf_url": str(event.pdf_url),
            "pdf_sha256": digest,
            "announcement_action": cohort_action,
            "parsed_action": event_action,
            "expected_before": expected_before,
            "expected_after": expected_after,
            "archived_previous_date": prior_date,
            "archived_previous_st": before,
            "archived_effective_st": at,
            "previous_state_age_days": prior_age,
            "verdict": verdict,
            "independent_archive": "BaoStock historical isST",
            "evidence_grade": "retrospective_cross_source_sample",
        })

    detail = pd.DataFrame(rows).sort_values(
        ["symbol", "effective_date", "pdf_sha256"]
    ).reset_index(drop=True)
    counts = detail["verdict"].value_counts().to_dict()
    summary = {
        "status": "sample_audit_complete",
        "verified_pdf_event_count": len(detail),
        "matched_events": int(counts.get("sample_match", 0)),
        "verdict_counts": {str(key): int(value) for key, value in counts.items()},
        "sample_scope": "CNINFO sampled 2023-2024 notices, not exhaustive SSE/SZSE",
        "pdf_parser_scale_gate_passed": False,
        "full_historical_st_reconciliation_passed": False,
        "strict_point_in_time_evidence": False,
        "ready_for_reconstructed_backtest": False,
        "real_money_ready": False,
        "note": (
            "No backfilled ST state and no automatic source corrections. "
            "Conflicts, direction mismatches and unobserved days require independent review."
        ),
    }
    return detail, summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="historical-st-cross-source-audit")
    parser.add_argument("--verified-events", required=True)
    parser.add_argument("--states-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    events = pd.read_csv(args.verified_events, dtype={"symbol": str})
    # Scope the big archive to only event symbols before cross-source comparison.
    states = pd.read_parquet(
        args.states_file, columns=["symbol", "trade_date", "is_st", "trade_status"]
    )
    detail, summary = reconcile_st_events(events, states)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    detail.to_csv(out / "event_verdicts.csv", index=False)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
