"""Measure whether unresolved SZSE ST-event exceptions entered reconstructed Top60.

This measures *membership impact* only, not causality or historical information
availability. Official 2023-2026 status reconciliation and real trading remain gated.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

MATCH = "historical_state_matches_official_change"
B_SHARE = "missing_exact_change_day_state"
DELIST = "delisting_rename_not_st_removal_evidence"
SUSPENDED = "status_on_suspended_day_requires_review"


def audit_exception_top60_impact(
    official_verdicts: pd.DataFrame,
    lifecycle: pd.DataFrame,
    candidates_root: str | Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    required = {"symbol", "change_date", "verdict", "old_name", "new_name"}
    if not required.issubset(official_verdicts.columns):
        raise ValueError("Missing official verdict fields")
    if "symbol" not in lifecycle.columns:
        raise ValueError("Missing lifecycle symbol")
    events = official_verdicts[official_verdicts["verdict"].ne(MATCH)].copy()
    if events.empty:
        raise ValueError("No ST source exceptions to audit")
    events["symbol"] = events["symbol"].astype(str).str.zfill(6)
    events["change_date"] = pd.to_datetime(
        events["change_date"], errors="coerce"
    ).dt.normalize()
    if events["change_date"].isna().any():
        raise ValueError("Invalid effective ST change date")
    official_a_symbols = set(lifecycle["symbol"].astype(str).str.zfill(6))
    events["is_a_share_member"] = events["symbol"].isin(official_a_symbols)

    # Never assume that missing BaoStock rows of a B-share are A-share gaps.
    invalid_b_share = events[
        events["verdict"].eq(B_SHARE)
        & (
            ~events["symbol"].str.startswith("200")
            | events["is_a_share_member"]
        )
    ]
    if not invalid_b_share.empty:
        raise RuntimeError(
            "Missing-state exceptions cannot all be classified as out-of-scope B shares"
        )
    if not events["verdict"].isin({B_SHARE, DELIST, SUSPENDED}).all():
        raise RuntimeError("Unexpected ST exception class requires manual review")

    candidate_dir = Path(candidates_root)
    files = sorted(candidate_dir.glob("*.csv"))
    if not files:
        raise RuntimeError("No reconstructed candidate dates")
    unique_days = set()
    hits: list[dict[str, object]] = []
    for path in files:
        day = pd.Timestamp(path.stem).normalize()
        if day in unique_days:
            raise RuntimeError(f"Duplicate candidate trading date {day}")
        unique_days.add(day)
        with path.open(newline="", encoding="utf-8-sig") as handle:
            names = [str(row["symbol"]).zfill(6) for row in csv.DictReader(handle)]
        if len(names) != 60 or len(set(names)) != 60:
            raise RuntimeError(f"Bad Top60 membership on {path.stem}")
        selected = set(names)
        for event in events.itertuples(index=False):
            if day < event.change_date or event.symbol not in selected:
                continue
            hits.append({
                "symbol": event.symbol,
                "event_date": event.change_date.date().isoformat(),
                "candidate_date": day.date().isoformat(),
                "verdict": event.verdict,
                "new_name": event.new_name,
            })

    detail = events.loc[
        :,
        ["symbol", "change_date", "old_name", "new_name", "verdict", "is_a_share_member"],
    ].copy()
    detail["change_date"] = detail["change_date"].dt.date.astype(str)
    key_counts: dict[tuple[str, str], int] = {}
    for hit in hits:
        key = (str(hit["symbol"]), str(hit["event_date"]))
        key_counts[key] = key_counts.get(key, 0) + 1
    detail["candidate_days_on_or_after_exception"] = [
        key_counts.get((str(row.symbol), str(row.change_date)), 0)
        for row in detail.itertuples(index=False)
    ]
    detail = detail.sort_values(["change_date", "symbol"]).reset_index(drop=True)
    counts = detail["verdict"].value_counts()
    summary = {
        "status": "retrospective_exception_impact_audited",
        "examined_candidate_dates": len(files),
        "exception_events": len(detail),
        "b_share_missing_states_not_in_a_universe": int(counts.get(B_SHARE, 0)),
        "delisting_name_events_not_auto_st_removals": int(counts.get(DELIST, 0)),
        "suspended_effective_day_events": int(counts.get(SUSPENDED, 0)),
        "post_exception_candidate_membership_hits": len(hits),
        "hit_records": hits[:20],
        "historical_top60_membership_no_detected_exception_overlap": bool(not hits),
        "evidence_scope": "only_46_known_SZSE_ST_exception_events",
        "official_disclosure_availability_verified": False,
        "full_historical_ST_evidence_gate_passed": False,
        "strict_live_observed": False,
        "ready_for_reconstructed_backtest": False,
        "real_money_ready": False,
    }
    return detail, summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="SZSE exception Top60 membership-impact audit")
    parser.add_argument("--szse-verdicts", required=True)
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--candidates-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    verdicts = pd.read_csv(args.szse_verdicts, dtype={"symbol": str})
    lifecycle = pd.read_csv(args.lifecycle_file, dtype={"symbol": str})
    detail, summary = audit_exception_top60_impact(
        verdicts, lifecycle, args.candidates_dir
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    detail.to_csv(out / "exception_impact.csv", index=False)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
