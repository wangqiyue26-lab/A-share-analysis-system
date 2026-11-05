from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

REQUIRED_LIFECYCLE_COLUMNS = {
    "symbol",
    "name",
    "exchange",
    "board",
    "list_date",
    "delist_date",
}
REQUIRED_AMOUNT_COLUMNS = {"symbol", "trade_date", "amount"}
REQUIRED_STATE_COLUMNS = {
    "symbol",
    "trade_date",
    "is_st",
    "trade_status",
    "close",
}


def _normalize_bool(series: pd.Series, *, name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    mapping = {
        "true": True,
        "false": False,
        "1": True,
        "0": False,
    }
    invalid = ~normalized.isin(mapping)
    if invalid.any():
        values = sorted(normalized[invalid].dropna().unique())
        raise ValueError(f"{name} contains unsupported boolean values: {values[:10]}")
    return normalized.map(mapping).astype(bool)


def normalize_lifecycle(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_LIFECYCLE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"lifecycle missing columns: {missing}")
    result = frame.copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce").dt.normalize()
    result["delist_date"] = pd.to_datetime(result["delist_date"], errors="coerce").dt.normalize()
    if result["symbol"].duplicated().any():
        duplicates = sorted(result.loc[result["symbol"].duplicated(False), "symbol"].unique())
        raise ValueError(f"lifecycle contains duplicate symbols: {duplicates[:10]}")
    if result["list_date"].isna().any():
        raise ValueError("lifecycle contains missing list_date")
    return result.sort_values("symbol").reset_index(drop=True)


def normalize_amounts(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_AMOUNT_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"amount archive missing columns: {missing}")
    result = frame.loc[:, ["symbol", "trade_date", "amount"]].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.normalize()
    result["amount"] = pd.to_numeric(result["amount"], errors="coerce")
    if result[["trade_date", "amount"]].isna().any().any():
        raise ValueError("amount archive contains invalid trade_date or amount")
    if result.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("amount archive contains duplicate symbol/date rows")
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def normalize_states(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_STATE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"state archive missing columns: {missing}")
    result = frame.loc[
        :,
        ["symbol", "trade_date", "is_st", "trade_status", "close"],
    ].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.normalize()
    result["is_st"] = _normalize_bool(result["is_st"], name="is_st")
    result["trade_status"] = _normalize_bool(
        result["trade_status"],
        name="trade_status",
    )
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    if result["trade_date"].isna().any():
        raise ValueError("state archive contains invalid trade_date")
    invalid_tradable_close = result["trade_status"] & (~result["close"].gt(0))
    if invalid_tradable_close.any():
        raise ValueError(
            "state archive contains tradable rows without a positive close"
        )
    if result.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("state archive contains duplicate symbol/date rows")
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _membership_for_date(
    lifecycle: pd.DataFrame,
    date: pd.Timestamp,
    *,
    min_listing_days: int,
    allowed_exchanges: tuple[str, ...],
) -> pd.DataFrame:
    allowed = {item.upper() for item in allowed_exchanges}
    listed_before = date - pd.Timedelta(days=min_listing_days)
    mask = (
        lifecycle["exchange"].astype(str).str.upper().isin(allowed)
        & lifecycle["list_date"].le(listed_before)
        & (lifecycle["delist_date"].isna() | lifecycle["delist_date"].gt(date))
    )
    return lifecycle[mask].copy()


def reconstruct_candidate_day(
    *,
    date: str | pd.Timestamp,
    lifecycle: pd.DataFrame,
    amount_rows: pd.DataFrame,
    state_rows: pd.DataFrame,
    limit: int = 60,
    min_amount: float = 50_000_000.0,
    min_listing_days: int = 120,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    require_full_state_coverage: bool = True,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Reconstruct the historical liquidity universe without future survivors.

    This deliberately does not claim exact production equivalence because historical
    float-market-cap is not yet part of the reconstruction. Amount is the primary
    production sort key; exact amount ties fall back to symbol order here.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if min_amount < 0:
        raise ValueError("min_amount cannot be negative")
    if min_listing_days < 0:
        raise ValueError("min_listing_days cannot be negative")

    target = pd.Timestamp(date).normalize()
    members = _membership_for_date(
        lifecycle,
        target,
        min_listing_days=min_listing_days,
        allowed_exchanges=allowed_exchanges,
    )
    if members.empty:
        raise RuntimeError(f"No reconstructed listed members for {target.date()}")

    amounts = amount_rows[amount_rows["trade_date"].eq(target)].copy()
    states = state_rows[state_rows["trade_date"].eq(target)].copy()

    member_symbols = set(members["symbol"])
    amount_symbols = set(amounts["symbol"])
    state_symbols = set(states["symbol"])

    missing_amount = sorted(member_symbols - amount_symbols)
    missing_state = sorted(member_symbols - state_symbols)
    state_coverage = 1.0 - (len(missing_state) / len(member_symbols))
    amount_coverage = 1.0 - (len(missing_amount) / len(member_symbols))

    if require_full_state_coverage and missing_state:
        raise RuntimeError(
            f"State archive is incomplete on {target.date()}: "
            f"{len(missing_state)} of {len(member_symbols)} eligible members missing"
        )

    # Missing amount for a suspended or ST security cannot affect the Top60.
    # Missing amount for an otherwise tradable non-ST security *can*: it might
    # have outranked every observed candidate. Never silently drop that name.
    rankable_states = states[
        states["symbol"].isin(member_symbols)
        & ~states["is_st"]
        & states["trade_status"]
        & states["close"].gt(0)
    ]
    missing_rankable_amount = sorted(set(rankable_states["symbol"]) - amount_symbols)
    if missing_rankable_amount:
        raise RuntimeError(
            f"Amount archive is incomplete on {target.date()}: "
            f"{len(missing_rankable_amount)} tradable non-ST members missing amount; "
            f"examples={missing_rankable_amount[:10]}"
        )

    merged = members.merge(
        amounts,
        on="symbol",
        how="inner",
        validate="one_to_one",
    ).merge(
        states,
        on=["symbol", "trade_date"],
        how="inner",
        validate="one_to_one",
    )

    eligible = merged[
        ~merged["is_st"]
        & merged["trade_status"]
        & merged["close"].gt(0)
        & merged["amount"].ge(min_amount)
    ].copy()
    if len(eligible) < limit:
        raise RuntimeError(
            f"Only {len(eligible)} reconstructed liquid eligible rows on "
            f"{target.date()}; need {limit}"
        )

    eligible = eligible.sort_values(
        ["amount", "symbol"],
        ascending=[False, True],
    ).reset_index(drop=True)

    cutoff_amount = float(eligible.iloc[limit - 1]["amount"])
    tied_at_cutoff = eligible["amount"].eq(cutoff_amount)
    cutoff_tie_count = int(tied_at_cutoff.sum())

    selected = eligible.head(limit).copy()
    selected["rank_by_amount"] = range(1, len(selected) + 1)
    selected["screen_source"] = "historical_amount_reconstruction"
    selected["reconstruction_grade"] = "historical_reconstructed_candidate"
    selected["float_market_cap_tiebreak_available"] = False

    audit = {
        "trade_date": target.date().isoformat(),
        "strict_point_in_time_evidence": False,
        "reconstruction_grade": "historical_reconstructed_candidate",
        "listed_age_eligible_count": len(members),
        "amount_coverage": amount_coverage,
        "state_coverage": state_coverage,
        "missing_amount_count": len(missing_amount),
        "missing_rankable_amount_count": len(missing_rankable_amount),
        "missing_state_count": len(missing_state),
        "liquid_non_st_tradable_count": len(eligible),
        "candidate_count": len(selected),
        "min_amount": float(min_amount),
        "min_listing_days": min_listing_days,
        "cutoff_amount": cutoff_amount,
        "cutoff_amount_tie_count": cutoff_tie_count,
        "production_sort_difference": (
            "Production sorts exact amount ties by historical float_market_cap then symbol. "
            "Reconstruction currently lacks historical float_market_cap and uses symbol "
            "for exact amount ties."
        ),
    }
    return selected, audit


def reconstruct_calendar(
    *,
    lifecycle: pd.DataFrame,
    amounts: pd.DataFrame,
    states: pd.DataFrame,
    output_dir: str | Path,
    limit: int = 60,
    min_amount: float = 50_000_000.0,
    min_listing_days: int = 120,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    trading_dates: Iterable[str | pd.Timestamp] | None = None,
) -> dict[str, object]:
    """Audit every observed/source-calendar day, never silently drop missing data.

    A supplied, independently sourced trading calendar detects dates where both
    archives are absent. Without it, the archive union is only a diagnostic
    calendar; neither case alone can authorize a reconstructed V2 backtest.
    """
    lifecycle = normalize_lifecycle(lifecycle)
    amounts = normalize_amounts(amounts)
    states = normalize_states(states)

    amount_groups = amounts.groupby("trade_date", sort=False).indices
    state_groups = states.groupby("trade_date", sort=False).indices
    archive_dates = set(amount_groups) | set(state_groups)
    if trading_dates is None:
        dates = sorted(archive_dates)
        supplied_calendar = False
    else:
        supplied = pd.to_datetime(pd.Series(list(trading_dates)), errors="coerce").dt.normalize()
        if supplied.empty or supplied.isna().any():
            raise ValueError("Trading calendar is empty or contains invalid dates")
        if supplied.duplicated().any():
            raise ValueError("Trading calendar contains duplicate dates")
        dates = sorted(set(supplied.tolist()))
        supplied_calendar = True
        unlisted_dates = archive_dates - set(dates)
        if unlisted_dates:
            examples = sorted(str(value.date()) for value in unlisted_dates)[:10]
            raise ValueError(f"Archive dates not in trading calendar: {examples}")
    if not dates:
        raise RuntimeError("No historical trading dates supplied or observed")

    output = Path(output_dir)
    candidate_dir = output / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    audits: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    # Group indices avoid rescanning multi-million-row archives on every date.
    for date in dates:
        amount_day = amounts.iloc[amount_groups[date]] if date in amount_groups else amounts.iloc[0:0]
        state_day = states.iloc[state_groups[date]] if date in state_groups else states.iloc[0:0]
        try:
            if amount_day.empty or state_day.empty:
                missing = [
                    label for label, frame in (("amount", amount_day), ("state", state_day))
                    if frame.empty
                ]
                raise RuntimeError(
                    f"Missing complete market date {date.date()} in "
                    f"{', '.join(missing)} archive(s)"
                )
            selected, audit = reconstruct_candidate_day(
                date=date,
                lifecycle=lifecycle,
                amount_rows=amount_day,
                state_rows=state_day,
                limit=limit,
                min_amount=min_amount,
                min_listing_days=min_listing_days,
                allowed_exchanges=allowed_exchanges,
            )
        except Exception as exc:  # noqa: BLE001 - calendar report records fail-closed dates
            failures.append(
                {
                    "trade_date": date.date().isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        selected.to_csv(
            candidate_dir / f"{date.date().isoformat()}.csv",
            index=False,
        )
        audits.append(audit)

    audit_frame = pd.DataFrame(audits)
    failure_frame = pd.DataFrame(failures)
    audit_frame.to_csv(output / "audit.csv", index=False)
    failure_frame.to_csv(output / "failures.csv", index=False)

    payload = {
        "status": "complete" if not failures else "incomplete",
        "strict_point_in_time_evidence": False,
        "reconstruction_grade": "historical_reconstructed_candidate",
        "input_calendar_dates": len(dates),
        "archive_union_dates": len(archive_dates),
        "independent_calendar_supplied": supplied_calendar,
        "calendar_source": "supplied_external" if supplied_calendar else "archive_union_only",
        "successful_candidate_dates": len(audits),
        "failed_candidate_dates": len(failures),
        "success_rate": len(audits) / len(dates),
        "first_date": dates[0].date().isoformat(),
        "last_date": dates[-1].date().isoformat(),
        "candidate_limit": limit,
        "min_amount": min_amount,
        "min_listing_days": min_listing_days,
        "production_sort_difference": (
            "Historical float_market_cap is not available. Exact amount ties use symbol "
            "instead of float_market_cap then symbol."
        ),
        "candidate_calendar_complete": bool(
            supplied_calendar and not failures and len(audits) >= 21
        ),
        # Calendar completeness is a necessary but insufficient gate. ST event
        # reconciliation, Top60 tie-breaks, and live-observed reproduction remain
        # separate human/audited prerequisites (Issue #39).
        "ready_for_reconstructed_backtest": False,
        "pending_evidence_gates": [
            "independent_trading_calendar_and_zero_daily_gaps",
            "historical_st_effective_date_reconciliation",
            "historical_top60_tiebreak_and_full_rankable_amount_coverage",
            "crosscheck_against_immutable_live_pit_observations",
            "frozen_v2_research_governance_review",
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ashare_system.historical_candidate_reconstruction"
    )
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--amounts-file", required=True)
    parser.add_argument("--states-file", required=True)
    parser.add_argument("--output-dir", default="reports/historical-pit/candidates")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--min-amount", type=float, default=50_000_000.0)
    parser.add_argument("--min-listing-days", type=int, default=120)
    parser.add_argument(
        "--trading-calendar-file",
        help="Independent exchange/benchmark trading calendar CSV with trade_date column",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    lifecycle = pd.read_csv(args.lifecycle_file, dtype={"symbol": str})
    amounts = pd.read_parquet(args.amounts_file)
    states = pd.read_parquet(args.states_file)
    trading_dates = None
    if args.trading_calendar_file:
        calendar = pd.read_csv(args.trading_calendar_file)
        if "trade_date" not in calendar.columns:
            raise ValueError("Trading calendar CSV missing trade_date column")
        trading_dates = calendar["trade_date"].tolist()
    payload = reconstruct_calendar(
        lifecycle=lifecycle,
        amounts=amounts,
        states=states,
        output_dir=args.output_dir,
        limit=args.limit,
        min_amount=args.min_amount,
        min_listing_days=args.min_listing_days,
        trading_dates=trading_dates,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
