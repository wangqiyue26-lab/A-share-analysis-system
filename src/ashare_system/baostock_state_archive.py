from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from .historical_amount_archive import select_shard


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


def _parse_binary(series: pd.Series, *, column: str) -> pd.Series:
    normalized = series.astype(str).str.strip()
    invalid = ~normalized.isin({"0", "1"})
    if invalid.any():
        values = sorted(normalized[invalid].unique())
        raise ValueError(f"{column} contains unexpected values: {values[:10]}")
    return normalized.eq("1")


def run_state_shard(
    *,
    lifecycle_file: str | Path,
    output_dir: str | Path,
    period_start: str,
    period_end: str,
    shard_count: int,
    shard_index: int,
) -> dict[str, object]:
    import baostock as bs

    lifecycle = pd.read_csv(lifecycle_file, dtype={"symbol": str})
    lifecycle["symbol"] = lifecycle["symbol"].astype(str).str.zfill(6)
    lifecycle["list_date"] = pd.to_datetime(lifecycle["list_date"], errors="coerce")
    lifecycle["delist_date"] = pd.to_datetime(lifecycle["delist_date"], errors="coerce")
    shard = select_shard(
        lifecycle,
        shard_count=shard_count,
        shard_index=shard_index,
        period_start=period_start,
        period_end=period_end,
    )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    shard.to_csv(output / "symbols.csv", index=False, date_format="%Y-%m-%d")

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")

    state_frames: list[pd.DataFrame] = []
    detail_rows: list[dict[str, object]] = []
    start = pd.Timestamp(period_start).normalize()
    end = pd.Timestamp(period_end).normalize()

    try:
        for row in shard.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            symbol_start = max(start, pd.Timestamp(row.list_date).normalize())
            symbol_end = end
            if pd.notna(row.delist_date):
                symbol_end = min(symbol_end, pd.Timestamp(row.delist_date).normalize())
            if symbol_end < symbol_start:
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "success": False,
                        "row_count": 0,
                        "warning": "empty_symbol_period",
                    }
                )
                continue

            code = _to_bs_code(symbol, str(row.exchange))
            try:
                result = bs.query_history_k_data_plus(
                    code,
                    "date,code,close,tradestatus,isST",
                    start_date=symbol_start.date().isoformat(),
                    end_date=symbol_end.date().isoformat(),
                    frequency="d",
                    adjustflag="3",
                )
                frame = _read_query_result(result)
                if frame.empty:
                    raise RuntimeError("BaoStock returned no daily state rows")

                frame["trade_date"] = pd.to_datetime(
                    frame["date"], errors="coerce"
                ).dt.normalize()
                frame["symbol"] = symbol
                frame["trade_status"] = _parse_binary(
                    frame["tradestatus"], column="tradestatus"
                )
                frame["is_st"] = _parse_binary(frame["isST"], column="isST")
                frame["close"] = pd.to_numeric(frame["close"], errors="coerce")

                if frame["trade_date"].isna().any():
                    raise ValueError("BaoStock state contains invalid trade dates")
                if frame["trade_date"].duplicated().any():
                    raise ValueError("BaoStock state contains duplicate trade dates")
                tradable = frame["trade_status"]
                invalid_close = tradable & (~frame["close"].gt(0))
                if invalid_close.any():
                    raise ValueError(
                        f"Tradable BaoStock rows contain invalid close: {int(invalid_close.sum())}"
                    )

                selected = frame.loc[
                    :,
                    ["symbol", "trade_date", "is_st", "trade_status", "close"],
                ].copy()
                state_frames.append(selected)
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "success": True,
                        "row_count": len(selected),
                        "first_state_date": selected["trade_date"].min().date().isoformat(),
                        "last_state_date": selected["trade_date"].max().date().isoformat(),
                        "is_st_coverage": 1.0,
                        "trade_status_coverage": 1.0,
                        "tradable_close_coverage": 1.0,
                        "st_day_count": int(selected["is_st"].sum()),
                        "suspended_day_count": int((~selected["trade_status"]).sum()),
                        "warning": None,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - shard isolates one symbol
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "success": False,
                        "row_count": 0,
                        "first_state_date": None,
                        "last_state_date": None,
                        "is_st_coverage": 0.0,
                        "trade_status_coverage": 0.0,
                        "tradable_close_coverage": 0.0,
                        "st_day_count": 0,
                        "suspended_day_count": 0,
                        "warning": f"{type(exc).__name__}: {exc}",
                    }
                )
    finally:
        bs.logout()

    detail = pd.DataFrame(detail_rows).sort_values("symbol").reset_index(drop=True)
    detail.to_csv(output / "detail.csv", index=False)

    if state_frames:
        states = pd.concat(state_frames, ignore_index=True)
        states = states.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    else:
        states = pd.DataFrame(
            columns=["symbol", "trade_date", "is_st", "trade_status", "close"]
        )
    if states.duplicated(["trade_date", "symbol"]).any():
        raise RuntimeError("State shard contains duplicate symbol/date rows")
    states.to_parquet(output / "states.parquet", index=False)

    success = detail["success"].astype(bool) if not detail.empty else pd.Series(dtype=bool)
    success_rate = float(success.mean()) if len(success) else 0.0
    payload = {
        "status": "complete",
        "purpose": "historical_daily_state_archive",
        "source": "BaoStock 0.9.4",
        "strict_point_in_time_evidence": False,
        "reconstruction_grade": "independent_historical_state_reconstruction",
        "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(),
        "shard_count": shard_count,
        "shard_index": shard_index,
        "symbol_count": len(shard),
        "success_count": int(success.sum()) if len(success) else 0,
        "success_rate": success_rate,
        "state_row_count": len(states),
        "failed_symbols": (
            detail.loc[~success, "symbol"].astype(str).tolist() if len(detail) else []
        ),
        "shard_gate_passed": bool(len(success) and success_rate >= 0.95),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def merge_state_shards(
    *,
    shards_root: str | Path,
    output_dir: str | Path,
    expected_shards: int = 32,
    min_success_rate: float = 0.95,
) -> dict[str, object]:
    root = Path(shards_root)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    summaries = sorted(root.glob("*/summary.json"))
    if len(summaries) != expected_shards:
        raise RuntimeError(
            f"Expected {expected_shards} state shard summaries, found {len(summaries)}"
        )

    indexes: set[int] = set()
    total_symbols = 0
    success_symbols = 0
    failed_symbols: list[str] = []
    frames: list[pd.DataFrame] = []
    source_files: list[dict[str, object]] = []
    period_start: str | None = None
    period_end: str | None = None

    for path in summaries:
        payload = json.loads(path.read_text(encoding="utf-8"))
        index = int(payload["shard_index"])
        if index in indexes:
            raise RuntimeError(f"Duplicate state shard index: {index}")
        indexes.add(index)
        if int(payload["shard_count"]) != expected_shards:
            raise RuntimeError(f"Unexpected shard_count in state shard {index}")
        if float(payload["success_rate"]) < min_success_rate:
            raise RuntimeError(
                f"State shard {index} success_rate={payload['success_rate']} below gate"
            )
        if period_start is None:
            period_start = str(payload["period_start"])
            period_end = str(payload["period_end"])
        elif (
            period_start != str(payload["period_start"])
            or period_end != str(payload["period_end"])
        ):
            raise RuntimeError("State shard periods are inconsistent")

        total_symbols += int(payload["symbol_count"])
        success_symbols += int(payload["success_count"])
        failed_symbols.extend(str(value) for value in payload["failed_symbols"])

        parquet = path.parent / "states.parquet"
        if not parquet.exists():
            raise RuntimeError(f"Missing state shard parquet: {parquet}")
        frame = pd.read_parquet(parquet)
        required = {"symbol", "trade_date", "is_st", "trade_status", "close"}
        missing = required - set(frame.columns)
        if missing:
            raise RuntimeError(f"{parquet} missing columns {sorted(missing)}")
        frames.append(frame)
        digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
        source_files.append(
            {
                "shard_index": index,
                "file": parquet.as_posix(),
                "rows": len(frame),
                "sha256": digest,
            }
        )

    if indexes != set(range(expected_shards)):
        raise RuntimeError("State shard index set is incomplete")

    states = pd.concat(frames, ignore_index=True)
    states["symbol"] = states["symbol"].astype(str).str.zfill(6)
    states["trade_date"] = pd.to_datetime(states["trade_date"]).dt.normalize()
    if states.duplicated(["trade_date", "symbol"]).any():
        raise RuntimeError("Merged state archive contains duplicate symbol/date rows")

    states = states.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    final_path = output / "historical_states_2023_2026.parquet"
    states.to_parquet(final_path, index=False, compression="zstd")
    final_digest = hashlib.sha256(final_path.read_bytes()).hexdigest()

    aggregate_success = success_symbols / total_symbols if total_symbols else 0.0
    if aggregate_success < min_success_rate:
        raise RuntimeError("Aggregate state archive success rate is below gate")

    payload = {
        "status": "complete",
        "purpose": "historical_daily_state_archive",
        "source": "BaoStock 0.9.4",
        "strict_point_in_time_evidence": False,
        "reconstruction_grade": "independent_historical_state_reconstruction",
        "period_start": period_start,
        "period_end": period_end,
        "shard_count": expected_shards,
        "symbol_count": total_symbols,
        "success_count": success_symbols,
        "success_rate": aggregate_success,
        "failed_symbol_count": len(set(failed_symbols)),
        "failed_symbols": sorted(set(failed_symbols)),
        "state_row_count": len(states),
        "unique_symbols": int(states["symbol"].nunique()),
        "output_file": final_path.name,
        "output_sha256": final_digest,
        "source_files": source_files,
        "candidate_reconstruction_state_ready": bool(
            aggregate_success >= min_success_rate and len(states) > 0
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.baostock_state_archive")
    sub = parser.add_subparsers(dest="command", required=True)

    shard = sub.add_parser("shard")
    shard.add_argument("--lifecycle-file", required=True)
    shard.add_argument("--output-dir", required=True)
    shard.add_argument("--period-start", default="2023-01-03")
    shard.add_argument("--period-end", default="2026-11-03")
    shard.add_argument("--shard-count", type=int, default=32)
    shard.add_argument("--shard-index", type=int, required=True)

    merge = sub.add_parser("merge")
    merge.add_argument("--shards-root", required=True)
    merge.add_argument("--output-dir", required=True)
    merge.add_argument("--expected-shards", type=int, default=32)
    merge.add_argument("--min-success-rate", type=float, default=0.95)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "shard":
        payload = run_state_shard(
            lifecycle_file=args.lifecycle_file,
            output_dir=args.output_dir,
            period_start=args.period_start,
            period_end=args.period_end,
            shard_count=args.shard_count,
            shard_index=args.shard_index,
        )
    else:
        payload = merge_state_shards(
            shards_root=args.shards_root,
            output_dir=args.output_dir,
            expected_shards=args.expected_shards,
            min_success_rate=args.min_success_rate,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
