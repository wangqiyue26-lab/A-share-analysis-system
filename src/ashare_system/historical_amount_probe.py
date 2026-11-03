from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .production import _fetch_one_history


def _stable_sample(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    if frame.empty or n <= 0:
        return frame.iloc[0:0].copy()
    result = frame.copy()
    result["_sample_key"] = result["symbol"].astype(str).map(
        lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    )
    return result.sort_values("_sample_key").head(n).drop(columns="_sample_key")


def build_probe_sample(
    lifecycle: pd.DataFrame,
    *,
    period_start: str,
    period_end: str,
    per_cohort: int = 12,
) -> pd.DataFrame:
    """Build deterministic current/delisted samples for SSE and SZSE."""
    frame = lifecycle.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    frame["currently_listed"] = frame["currently_listed"].astype(bool)

    start = pd.Timestamp(period_start).normalize()
    end = pd.Timestamp(period_end).normalize()
    rows: list[pd.DataFrame] = []
    for exchange in ("SSE", "SZSE"):
        exchange_frame = frame[frame["exchange"] == exchange].copy()

        current = exchange_frame[exchange_frame["currently_listed"]].copy()
        current = current[current["list_date"] <= end]
        current = _stable_sample(current, per_cohort)
        current["cohort"] = f"{exchange.lower()}_current"
        rows.append(current)

        delisted = exchange_frame[~exchange_frame["currently_listed"]].copy()
        delisted = delisted[
            delisted["delist_date"].notna()
            & delisted["delist_date"].ge(start)
            & delisted["list_date"].le(end)
        ]
        delisted = _stable_sample(delisted, per_cohort)
        delisted["cohort"] = f"{exchange.lower()}_delisted_in_period"
        rows.append(delisted)

    result = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if result.empty:
        raise RuntimeError("Historical amount probe could not build any sample")
    return result.sort_values(["cohort", "symbol"]).reset_index(drop=True)


def run_probe(
    *,
    lifecycle_file: str | Path,
    output_dir: str | Path,
    cache_root: str | Path = "data/cache",
    period_start: str = "2023-01-03",
    period_end: str = "2026-11-03",
    per_cohort: int = 12,
    workers: int = 4,
) -> dict[str, object]:
    lifecycle = pd.read_csv(lifecycle_file, dtype={"symbol": str})
    sample = build_probe_sample(
        lifecycle,
        period_start=period_start,
        period_end=period_end,
        per_cohort=per_cohort,
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    sample.to_csv(output / "sample.csv", index=False, date_format="%Y-%m-%d")

    start = pd.Timestamp(period_start).normalize()
    end = pd.Timestamp(period_end).normalize()
    results: list[dict[str, object]] = []

    def submit_one(row: pd.Series) -> tuple[str, pd.DataFrame | None, str | None, str | None]:
        symbol_start = max(start, pd.Timestamp(row["list_date"]).normalize())
        symbol_end = end
        if pd.notna(row["delist_date"]):
            symbol_end = min(symbol_end, pd.Timestamp(row["delist_date"]).normalize())
        if symbol_end < symbol_start:
            return str(row["symbol"]), None, None, "empty_symbol_period"
        return _fetch_one_history(
            str(row["symbol"]).zfill(6),
            start=symbol_start,
            end=symbol_end,
            cache_root=Path(cache_root),
            adjust="",
        )

    by_symbol = {
        str(row.symbol).zfill(6): row
        for row in sample.itertuples(index=False)
    }
    with ThreadPoolExecutor(max_workers=min(workers, len(sample))) as pool:
        futures = {
            pool.submit(submit_one, pd.Series(row._asdict())): str(row.symbol).zfill(6)
            for row in sample.itertuples(index=False)
        }
        for future in as_completed(futures):
            symbol = futures[future]
            meta = by_symbol[symbol]
            try:
                returned, bars, provider, warning = future.result()
            except Exception as exc:  # noqa: BLE001 - diagnostic probe isolates one symbol
                results.append(
                    {
                        "symbol": symbol,
                        "cohort": meta.cohort,
                        "exchange": meta.exchange,
                        "success": False,
                        "provider": None,
                        "row_count": 0,
                        "amount_coverage": 0.0,
                        "first_bar": None,
                        "last_bar": None,
                        "warning": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            if bars is None or bars.empty:
                results.append(
                    {
                        "symbol": returned,
                        "cohort": meta.cohort,
                        "exchange": meta.exchange,
                        "success": False,
                        "provider": provider,
                        "row_count": 0,
                        "amount_coverage": 0.0,
                        "first_bar": None,
                        "last_bar": None,
                        "warning": warning,
                    }
                )
                continue

            amount = pd.to_numeric(bars["amount"], errors="coerce")
            results.append(
                {
                    "symbol": returned,
                    "cohort": meta.cohort,
                    "exchange": meta.exchange,
                    "success": True,
                    "provider": provider,
                    "row_count": len(bars),
                    "amount_coverage": float(amount.notna().mean()),
                    "first_bar": pd.Timestamp(bars["trade_date"].min()).date().isoformat(),
                    "last_bar": pd.Timestamp(bars["trade_date"].max()).date().isoformat(),
                    "warning": warning,
                }
            )

    detail = pd.DataFrame(results).sort_values(["cohort", "symbol"]).reset_index(drop=True)
    detail.to_csv(output / "detail.csv", index=False)

    cohorts: dict[str, dict[str, object]] = {}
    for cohort, frame in detail.groupby("cohort"):
        success = frame["success"].astype(bool)
        cohorts[str(cohort)] = {
            "sample_count": len(frame),
            "success_count": int(success.sum()),
            "success_rate": float(success.mean()),
            "mean_amount_coverage_successes": (
                float(frame.loc[success, "amount_coverage"].mean()) if success.any() else 0.0
            ),
            "providers": {
                str(k): int(v)
                for k, v in frame.loc[success, "provider"].fillna("unknown").value_counts().items()
            },
        }

    delisted_rows = detail[detail["cohort"].str.contains("delisted", regex=False)]
    current_rows = detail[detail["cohort"].str.contains("current", regex=False)]
    delisted_success = (
        float(delisted_rows["success"].astype(bool).mean()) if len(delisted_rows) else 0.0
    )
    current_success = (
        float(current_rows["success"].astype(bool).mean()) if len(current_rows) else 0.0
    )

    payload = {
        "status": "complete",
        "purpose": "coverage_probe_only",
        "strict_point_in_time_evidence": False,
        "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(),
        "per_cohort": per_cohort,
        "sample_count": len(detail),
        "current_success_rate": current_success,
        "delisted_success_rate": delisted_success,
        "cohorts": cohorts,
        "full_archive_recommendation": (
            "proceed_to_sharded_archive"
            if delisted_success >= 0.80 and current_success >= 0.90
            else "do_not_scale_yet"
        ),
        "note": (
            "This probe measures whether free per-symbol history sources can provide raw daily "
            "bars with amount for both surviving and delisted A shares. It does not reconstruct "
            "a historical candidate universe and is not strict PIT evidence."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.historical_amount_probe")
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--output-dir", default="reports/historical-pit/amount-probe")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--period-start", default="2023-01-03")
    parser.add_argument("--period-end", default="2026-11-03")
    parser.add_argument("--per-cohort", type=int, default=12)
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_probe(
        lifecycle_file=args.lifecycle_file,
        output_dir=args.output_dir,
        cache_root=args.cache_root,
        period_start=args.period_start,
        period_end=args.period_end,
        per_cohort=args.per_cohort,
        workers=args.workers,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
