from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .historical_amount_probe import build_probe_sample
from .historical_pit_reconstruction import membership_as_of


def _to_bs_code(symbol: str, exchange: str) -> str:
    code = str(symbol).zfill(6)
    if exchange == "SSE":
        return f"sh.{code}"
    if exchange == "SZSE":
        return f"sz.{code}"
    raise ValueError(f"Unsupported exchange: {exchange}")


def _read_query_result(result) -> pd.DataFrame:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {result.error_code} {result.error_msg}")
    return pd.DataFrame(rows, columns=result.fields)


def _normalize_bool_text(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).str.strip()
    invalid = ~normalized.isin({"0", "1"})
    if invalid.any():
        values = sorted(normalized[invalid].unique())
        raise ValueError(f"Unexpected BaoStock boolean values: {values[:10]}")
    return normalized.eq("1")


def _filter_a_share_codes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result["code"] = result["code"].astype(str)
    mask = (
        result["code"].str.match(r"^sh\.6\d{5}$", na=False)
        | result["code"].str.match(r"^sz\.(?:00[0-3]|30[01])\d{3}$", na=False)
    )
    return result[mask].copy()


def run_probe(
    *,
    lifecycle_file: str | Path,
    output_dir: str | Path,
    period_start: str = "2023-01-03",
    period_end: str = "2026-11-03",
    per_cohort: int = 12,
) -> dict[str, object]:
    import baostock as bs

    lifecycle = pd.read_csv(lifecycle_file, dtype={"symbol": str})
    lifecycle["symbol"] = lifecycle["symbol"].astype(str).str.zfill(6)
    lifecycle["list_date"] = pd.to_datetime(lifecycle["list_date"], errors="coerce")
    lifecycle["delist_date"] = pd.to_datetime(lifecycle["delist_date"], errors="coerce")
    lifecycle["currently_listed"] = lifecycle["currently_listed"].astype(bool)

    sample = build_probe_sample(
        lifecycle,
        period_start=period_start,
        period_end=period_end,
        per_cohort=per_cohort,
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    sample.to_csv(output / "sample.csv", index=False, date_format="%Y-%m-%d")

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")

    detail_rows: list[dict[str, object]] = []
    membership_rows: list[dict[str, object]] = []
    try:
        for row in sample.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            start = max(pd.Timestamp(period_start), pd.Timestamp(row.list_date)).normalize()
            end = pd.Timestamp(period_end).normalize()
            if pd.notna(row.delist_date):
                end = min(end, pd.Timestamp(row.delist_date).normalize())
            code = _to_bs_code(symbol, str(row.exchange))
            try:
                result = bs.query_history_k_data_plus(
                    code,
                    "date,code,amount,tradestatus,isST",
                    start_date=start.date().isoformat(),
                    end_date=end.date().isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                frame = _read_query_result(result)
                if frame.empty:
                    raise RuntimeError("BaoStock returned no history rows")
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
                is_st = _normalize_bool_text(frame["isST"])
                trade_status = _normalize_bool_text(frame["tradestatus"])
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "exchange": row.exchange,
                        "cohort": row.cohort,
                        "success": True,
                        "row_count": len(frame),
                        "first_bar": frame["date"].min().date().isoformat(),
                        "last_bar": frame["date"].max().date().isoformat(),
                        "amount_coverage": float(frame["amount"].notna().mean()),
                        "is_st_coverage": float(frame["isST"].astype(str).ne("").mean()),
                        "trade_status_coverage": float(
                            frame["tradestatus"].astype(str).ne("").mean()
                        ),
                        "st_day_count": int(is_st.sum()),
                        "tradable_day_count": int(trade_status.sum()),
                        "st_transition_count": int(is_st.ne(is_st.shift()).sum() - 1),
                        "warning": None,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - diagnostic probe isolates symbols
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "exchange": row.exchange,
                        "cohort": row.cohort,
                        "success": False,
                        "row_count": 0,
                        "first_bar": None,
                        "last_bar": None,
                        "amount_coverage": 0.0,
                        "is_st_coverage": 0.0,
                        "trade_status_coverage": 0.0,
                        "st_day_count": 0,
                        "tradable_day_count": 0,
                        "st_transition_count": 0,
                        "warning": f"{type(exc).__name__}: {exc}",
                    }
                )

        check_dates = [
            pd.Timestamp("2023-01-03"),
            pd.Timestamp("2024-01-02"),
            pd.Timestamp("2025-01-02"),
            pd.Timestamp("2026-11-03"),
        ]
        for date in check_dates:
            result = bs.query_all_stock(day=date.date().isoformat())
            all_stock = _read_query_result(result)
            all_stock = _filter_a_share_codes(all_stock)
            reconstructed = membership_as_of(lifecycle, date)
            expected_count = len(reconstructed)
            observed_count = len(all_stock)
            membership_rows.append(
                {
                    "trade_date": date,
                    "baostock_a_share_count": observed_count,
                    "official_reconstructed_count": expected_count,
                    "count_difference": observed_count - expected_count,
                    "relative_difference": (
                        (observed_count - expected_count) / expected_count
                        if expected_count
                        else None
                    ),
                }
            )
    finally:
        bs.logout()

    detail = pd.DataFrame(detail_rows).sort_values(["cohort", "symbol"]).reset_index(drop=True)
    membership = pd.DataFrame(membership_rows).sort_values("trade_date").reset_index(drop=True)
    detail.to_csv(output / "detail.csv", index=False)
    membership.to_csv(output / "membership_crosscheck.csv", index=False, date_format="%Y-%m-%d")

    success = detail["success"].astype(bool)
    current = detail[detail["cohort"].str.contains("current", regex=False)]
    delisted = detail[detail["cohort"].str.contains("delisted", regex=False)]
    successful = detail[success]
    max_membership_gap = (
        float(membership["relative_difference"].abs().max()) if not membership.empty else None
    )
    payload = {
        "status": "complete",
        "purpose": "baostock_historical_state_crosscheck",
        "source": "BaoStock 0.9.4 historical daily API",
        "strict_point_in_time_evidence": False,
        "period_start": pd.Timestamp(period_start).date().isoformat(),
        "period_end": pd.Timestamp(period_end).date().isoformat(),
        "sample_count": len(detail),
        "success_count": int(success.sum()),
        "success_rate": float(success.mean()) if len(detail) else 0.0,
        "current_success_rate": (
            float(current["success"].astype(bool).mean()) if len(current) else 0.0
        ),
        "delisted_success_rate": (
            float(delisted["success"].astype(bool).mean()) if len(delisted) else 0.0
        ),
        "mean_is_st_coverage_successes": (
            float(successful["is_st_coverage"].mean()) if len(successful) else 0.0
        ),
        "mean_amount_coverage_successes": (
            float(successful["amount_coverage"].mean()) if len(successful) else 0.0
        ),
        "mean_trade_status_coverage_successes": (
            float(successful["trade_status_coverage"].mean()) if len(successful) else 0.0
        ),
        "membership_crosscheck_dates": len(membership),
        "max_abs_membership_relative_difference": max_membership_gap,
        "reconstruction_recommendation": (
            "promising_independent_state_source"
            if (
                len(detail)
                and float(success.mean()) >= 0.95
                and float(successful["is_st_coverage"].mean()) >= 0.99
                and max_membership_gap is not None
                and max_membership_gap <= 0.05
            )
            else "needs_more_validation"
        ),
        "note": (
            "BaoStock is queried after the fact and therefore is not strict contemporaneous "
            "PIT evidence. It is used as an independent historical state source to cross-check "
            "official lifecycle reconstruction and CNINFO event evidence."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.baostock_state_probe")
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--output-dir", default="reports/historical-pit/baostock-state-probe")
    parser.add_argument("--period-start", default="2023-01-03")
    parser.add_argument("--period-end", default="2026-11-03")
    parser.add_argument("--per-cohort", type=int, default=12)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_probe(
        lifecycle_file=args.lifecycle_file,
        output_dir=args.output_dir,
        period_start=args.period_start,
        period_end=args.period_end,
        per_cohort=args.per_cohort,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
