"""Research-only BaoStock amount recovery for symbols omitted by the initial archive.

Nothing here creates contemporaneously observed PIT evidence. Source data are
recovered after the fact, checked against the archived daily ST/trading states,
and kept separate from the immutable amount archive and forward V2 ledgers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .baostock_state_archive import _read_query_result, _to_bs_code
from .historical_candidate_reconstruction import normalize_states


def check_recovered_symbol(
    raw: pd.DataFrame, state: pd.DataFrame, *, symbol: str
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Reject status disagreement and count every still-unresolved rankable day."""
    code = str(symbol).zfill(6)
    required = {"date", "code", "amount", "tradestatus", "isST"}
    if not required.issubset(raw.columns):
        raise ValueError(f"{code}: missing BaoStock fields {sorted(required - set(raw.columns))}")
    if raw.empty:
        raise ValueError(f"{code}: empty BaoStock result")
    if state.empty:
        raise ValueError(f"{code}: missing independent archived daily state")
    frame = raw.copy()
    frame["trade_date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    if frame["trade_date"].isna().any() or frame["trade_date"].duplicated().any():
        raise ValueError(f"{code}: invalid or duplicate BaoStock dates")
    if state["trade_date"].duplicated().any():
        raise ValueError(f"{code}: duplicate archived state dates")
    returned_symbols = frame["code"].astype(str).str.split(".").str[-1].str.zfill(6)
    if not returned_symbols.eq(code).all():
        raise ValueError(f"{code}: BaoStock symbol mismatch")
    for col in ("tradestatus", "isST"):
        if not frame[col].astype(str).isin({"0", "1"}).all():
            raise ValueError(f"{code}: invalid {col} flags")
    frame["provider_trade_status"] = frame["tradestatus"].astype(str).eq("1")
    frame["provider_is_st"] = frame["isST"].astype(str).eq("1")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    if frame["amount"].lt(0).any():
        raise ValueError(f"{code}: negative BaoStock amount")
    overlap = state.merge(
        frame[["trade_date", "provider_trade_status", "provider_is_st"]],
        on="trade_date",
        how="inner",
        validate="one_to_one",
    )
    inconsistent = (
        overlap["trade_status"].ne(overlap["provider_trade_status"])
        | overlap["is_st"].ne(overlap["provider_is_st"])
    )
    if inconsistent.any():
        raise ValueError(f"{code}: {int(inconsistent.sum())} BaoStock/ST archive status mismatches")
    valid = frame[frame["amount"].notna()].loc[:, ["trade_date", "amount"]].copy()
    valid.insert(0, "symbol", code)
    rankable = state[
        state["trade_status"] & ~state["is_st"] & state["close"].gt(0)
    ]
    gaps = rankable[~rankable["trade_date"].isin(valid["trade_date"])]
    return valid, {
        "symbol": code,
        "source": "baostock_historical_unadjusted",
        "returned_date_count": int(len(frame)),
        "recovered_amount_rows": int(len(valid)),
        "archived_state_dates": int(len(state)),
        "rankable_state_dates": int(len(rankable)),
        "unresolved_rankable_dates": int(len(gaps)),
        "unresolved_examples": [
            value.date().isoformat() for value in gaps["trade_date"].head(10)
        ],
        "status": "complete" if gaps.empty else "incomplete",
    }


def run_recovery(
    *,
    lifecycle_file: str | Path,
    state_file: str | Path,
    amount_manifest_file: str | Path,
    output_dir: str | Path,
    period_start: str = "2023-01-03",
    period_end: str = "2026-11-03",
) -> dict[str, object]:
    import baostock as bs

    manifest = json.loads(Path(amount_manifest_file).read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or manifest["shard_count"] != 32:
        raise RuntimeError("Initial amount archive has not passed its source-level gate")
    if manifest["period_start"] != period_start or manifest["period_end"] != period_end:
        raise RuntimeError("Initial amount archive period differs from fixed recovery window")
    failed = sorted(str(value).zfill(6) for value in manifest["failed_symbols"])
    if not failed:
        raise RuntimeError("No missing archive symbols to recover")
    lifecycle = pd.read_csv(lifecycle_file, dtype={"symbol": str})
    lifecycle["symbol"] = lifecycle["symbol"].str.zfill(6)
    lifecycle["list_date"] = pd.to_datetime(lifecycle["list_date"], errors="coerce")
    lifecycle["delist_date"] = pd.to_datetime(lifecycle["delist_date"], errors="coerce")
    if not set(failed).issubset(set(lifecycle["symbol"])):
        raise RuntimeError("Amount failure manifest includes unknown lifecycle symbols")
    state = pd.read_parquet(
        state_file,
        columns=["symbol", "trade_date", "is_st", "trade_status", "close"],
    )
    state = state[state["symbol"].astype(str).str.zfill(6).isin(failed)]
    state = normalize_states(state)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    detail: list[dict[str, object]] = []
    recovered: list[pd.DataFrame] = []

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code}: {login.error_msg}")
    try:
        for row in lifecycle[lifecycle["symbol"].isin(failed)].itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            start = max(pd.Timestamp(period_start), pd.Timestamp(row.list_date))
            end = pd.Timestamp(period_end)
            if pd.notna(row.delist_date):
                end = min(end, pd.Timestamp(row.delist_date))
            current_state = state[state["symbol"].eq(symbol)].copy()
            try:
                if end < start:
                    raise ValueError("No lifecycle overlap with recovery window")
                result = bs.query_history_k_data_plus(
                    _to_bs_code(symbol, str(row.exchange)),
                    "date,code,amount,tradestatus,isST",
                    start_date=start.date().isoformat(),
                    end_date=end.date().isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                frame = _read_query_result(result)
                candidate, audit = check_recovered_symbol(
                    frame, current_state, symbol=symbol
                )
                recovered.append(candidate)
                detail.append(audit)
            except Exception as exc:  # noqa: BLE001 - isolated source probe
                rankable_count = int((
                    current_state["trade_status"]
                    & ~current_state["is_st"]
                    & current_state["close"].gt(0)
                ).sum())
                detail.append({
                    "symbol": symbol,
                    "status": "error",
                    "unresolved_rankable_dates": rankable_count,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    finally:
        bs.logout()

    details = pd.DataFrame(detail).sort_values("symbol")
    details.to_csv(output / "recovery_details.csv", index=False)
    rows = (
        pd.concat(recovered, ignore_index=True)
        if recovered
        else pd.DataFrame(columns=["symbol", "trade_date", "amount"])
    )
    if rows.duplicated(["symbol", "trade_date"]).any():
        raise RuntimeError("Recovered amount rows contain duplicate symbol/date keys")
    rows.to_parquet(output / "recovered_amounts.parquet", index=False)
    total_unresolved = int(details["unresolved_rankable_dates"].fillna(0).sum())
    payload = {
        "status": "complete" if total_unresolved == 0 and not details["status"].eq("error").any()
                  else "incomplete",
        "purpose": "missing_historical_amount_recovery_pilot",
        "source": "BaoStock query_history_k_data_plus raw amount / adjustflag=3",
        "period_start": period_start,
        "period_end": period_end,
        "target_symbol_count": len(failed),
        "recovered_symbol_count": int(details["status"].ne("error").sum()),
        "recovered_amount_rows": len(rows),
        "unresolved_rankable_symbol_days": total_unresolved,
        "error_symbol_count": int(details["status"].eq("error").sum()),
        "strict_point_in_time_evidence": False,
        "real_money_ready": False,
        "ready_for_reconstructed_backtest": False,
        "original_archive_unmodified": True,
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.historical_amount_recovery")
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--states-file", required=True)
    parser.add_argument("--amount-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = run_recovery(
        lifecycle_file=args.lifecycle_file,
        state_file=args.states_file,
        amount_manifest_file=args.amount_manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
