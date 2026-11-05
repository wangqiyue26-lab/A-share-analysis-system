"""Independent historical SZSE abbreviation/ST state comparison.

Reconciles source-dated abbreviation transitions with BaoStock daily ST rows.
This retrospective validation does NOT establish when announcements became
publicly available, nor does it authorize historical V2 backtests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

REQUIRED_CHANGES = {
    "symbol", "change_date", "old_name", "new_name", "is_st_before",
    "is_st_after", "st_transition", "source",
}
REQUIRED_STATES = {"symbol", "trade_date", "is_st", "trade_status"}


def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true"}:
        return True
    if text in {"0", "false"}:
        return False
    raise ValueError(f"Invalid historical ST flag: {value!r}")


def audit_szse_st_transitions(
    changes: pd.DataFrame,
    states: pd.DataFrame,
    *,
    first_date: str = "2023-01-03",
    last_date: str = "2026-11-03",
    max_prior_age_days: int = 20,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if max_prior_age_days <= 0:
        raise ValueError("max_prior_age_days must be positive")
    missing = REQUIRED_CHANGES - set(changes.columns)
    if missing:
        raise ValueError(f"Official change archive missing {sorted(missing)}")
    missing = REQUIRED_STATES - set(states.columns)
    if missing:
        raise ValueError(f"BaoStock state archive missing {sorted(missing)}")
    first = pd.Timestamp(first_date).normalize()
    last = pd.Timestamp(last_date).normalize()
    if first > last:
        raise ValueError("Invalid historical audit date range")
    source = changes.copy()
    source["symbol"] = source["symbol"].astype(str).str.zfill(6)
    source["change_date"] = pd.to_datetime(
        source["change_date"], errors="coerce"
    ).dt.normalize()
    if source["change_date"].isna().any():
        raise ValueError("Invalid official change date")
    source = source[
        source["change_date"].between(first, last)
        & source["st_transition"].map(_strict_bool)
    ].copy()
    if source.empty:
        raise ValueError("No historical SZSE ST transitions to audit")
    if not source["source"].eq("szse_official_abbreviation_change").all():
        raise ValueError("Unexpected abbreviation change source")
    if source.duplicated(["symbol", "change_date"]).any():
        raise ValueError("Duplicate official transition symbol/date")

    observed = states[states["symbol"].astype(str).str.zfill(6).isin(source["symbol"])].copy()
    observed["symbol"] = observed["symbol"].astype(str).str.zfill(6)
    observed["trade_date"] = pd.to_datetime(
        observed["trade_date"], errors="coerce"
    ).dt.normalize()
    if observed["trade_date"].isna().any() or observed.duplicated(
        ["symbol", "trade_date"]
    ).any():
        raise ValueError("Invalid or duplicate BaoStock historical state keys")
    grouped = {
        key: frame.sort_values("trade_date")
        for key, frame in observed.groupby("symbol")
    }
    rows: list[dict[str, object]] = []
    for item in source.itertuples(index=False):
        symbol = str(item.symbol).zfill(6)
        effective = pd.Timestamp(item.change_date)
        old = _strict_bool(item.is_st_before)
        new = _strict_bool(item.is_st_after)
        if old == new:
            raise ValueError(f"No boolean transition for {symbol}")
        target = grouped.get(symbol)
        previous_st = None
        effective_st = None
        previous_day = None
        previous_age = None
        effective_trading = None
        if target is not None:
            previous = target[target["trade_date"].lt(effective)].tail(1)
            same_day = target[target["trade_date"].eq(effective)]
            if len(previous) == 1:
                previous_st = _strict_bool(previous.iloc[0]["is_st"])
                previous_day = previous.iloc[0]["trade_date"].date().isoformat()
                previous_age = int((effective - previous.iloc[0]["trade_date"]).days)
            if len(same_day) == 1:
                effective_st = _strict_bool(same_day.iloc[0]["is_st"])
                effective_trading = _strict_bool(same_day.iloc[0]["trade_status"])

        # "STxxx" => "xxx退" is not proof that the delisted stock
        # became a normal non-ST *tradable* security.
        delisting_rename = "退" in str(item.new_name) and old and not new
        if delisting_rename:
            verdict = "delisting_rename_not_st_removal_evidence"
        elif effective_st is None:
            verdict = "missing_exact_change_day_state"
        elif previous_st is None or previous_age is None or previous_age > max_prior_age_days:
            verdict = "missing_or_stale_previous_state"
        elif not effective_trading:
            verdict = "status_on_suspended_day_requires_review"
        elif previous_st != old or effective_st != new:
            verdict = "official_vs_baostock_state_conflict"
        else:
            verdict = "historical_state_matches_official_change"

        rows.append({
            "symbol": symbol,
            "exchange": "SZSE",
            "change_date": effective.date().isoformat(),
            "old_name": str(item.old_name),
            "new_name": str(item.new_name),
            "official_previous_st": old,
            "official_effective_st": new,
            "previous_state_date": previous_day,
            "previous_state_st": previous_st,
            "change_day_state_st": effective_st,
            "change_day_trade_status": effective_trading,
            "previous_state_age_days": previous_age,
            "delisting_rename": delisting_rename,
            "verdict": verdict,
            "source": "szse_official_abbreviation_change",
            "independent_state_source": "BaoStock daily isST",
            "evidence_grade": "retrospective_official_change_crosscheck",
        })

    detail = pd.DataFrame(rows).sort_values(
        ["change_date", "symbol"]
    ).reset_index(drop=True)
    counts = detail["verdict"].value_counts().to_dict()
    summary = {
        "status": "research_source_comparison_completed",
        "period_start": first.date().isoformat(),
        "period_end": last.date().isoformat(),
        "official_st_transition_count": len(detail),
        "distinct_symbols": int(detail["symbol"].nunique()),
        "same_day_state_matches": int(
            counts.get("historical_state_matches_official_change", 0)
        ),
        "verdict_counts": {str(k): int(v) for k, v in counts.items()},
        "sse_coverage": "not_in_scope",
        "disclosure_publication_times_checked": False,
        "complete_st_history_claim": False,
        "strict_point_in_time_evidence": False,
        "ready_for_reconstructed_backtest": False,
        "real_money_ready": False,
        "note": (
            "Official abbreviation change history is a retrospective event source. "
            "It does not establish disclosure availability before trading; unresolved "
            "and conflicting names remain excluded from automatic status correction."
        ),
    }
    return detail, summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="historical-szse-st-transition-audit")
    parser.add_argument("--szse-changes-file", required=True)
    parser.add_argument("--states-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    changes = pd.read_csv(args.szse_changes_file, dtype={"symbol": str})
    states = pd.read_parquet(
        args.states_file, columns=["symbol", "trade_date", "is_st", "trade_status"]
    )
    detail, summary = audit_szse_st_transitions(changes, states)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    detail.to_csv(out / "szse_transition_verdicts.csv", index=False)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
