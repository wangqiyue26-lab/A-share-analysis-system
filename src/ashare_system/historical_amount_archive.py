from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .production import _fetch_one_history


def shard_for_symbol(symbol: str, shard_count: int) -> int:
    if shard_count <= 0:
        raise ValueError("shard_count must be positive")
    digest = hashlib.sha256(str(symbol).zfill(6).encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % shard_count


def select_shard(
    lifecycle: pd.DataFrame,
    *,
    shard_count: int,
    shard_index: int,
    period_start: str,
    period_end: str,
) -> pd.DataFrame:
    if not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must be in [0, shard_count)")
    frame = lifecycle.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    start = pd.Timestamp(period_start).normalize()
    end = pd.Timestamp(period_end).normalize()
    relevant = frame[
        frame["list_date"].notna()
        & frame["list_date"].le(end)
        & (frame["delist_date"].isna() | frame["delist_date"].ge(start))
    ].copy()
    relevant["shard"] = relevant["symbol"].map(
        lambda value: shard_for_symbol(value, shard_count)
    )
    return (
        relevant[relevant["shard"] == shard_index]
        .sort_values("symbol")
        .reset_index(drop=True)
    )


def run_shard_archive(
    *,
    lifecycle_file: str | Path,
    output_dir: str | Path,
    cache_root: str | Path,
    period_start: str,
    period_end: str,
    shard_count: int,
    shard_index: int,
    workers: int = 2,
) -> dict[str, object]:
    lifecycle = pd.read_csv(lifecycle_file, dtype={"symbol": str})
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

    start = pd.Timestamp(period_start).normalize()
    end = pd.Timestamp(period_end).normalize()
    amount_frames: list[pd.DataFrame] = []
    detail_rows: list[dict[str, object]] = []

    def fetch(meta: object) -> tuple[str, pd.DataFrame | None, str | None, str | None]:
        symbol = str(meta.symbol).zfill(6)
        symbol_start = max(start, pd.Timestamp(meta.list_date).normalize())
        symbol_end = end
        if pd.notna(meta.delist_date):
            symbol_end = min(symbol_end, pd.Timestamp(meta.delist_date).normalize())
        if symbol_end < symbol_start:
            return symbol, None, None, "empty_symbol_period"
        return _fetch_one_history(
            symbol,
            start=symbol_start,
            end=symbol_end,
            cache_root=Path(cache_root),
            adjust="",
        )

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(shard)))) as pool:
        futures = {
            pool.submit(fetch, row): str(row.symbol).zfill(6)
            for row in shard.itertuples(index=False)
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                returned, bars, provider, warning = future.result()
            except Exception as exc:  # noqa: BLE001 - archive isolates one symbol
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "success": False,
                        "provider": None,
                        "row_count": 0,
                        "amount_coverage": 0.0,
                        "warning": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            if bars is None or bars.empty:
                detail_rows.append(
                    {
                        "symbol": returned,
                        "success": False,
                        "provider": provider,
                        "row_count": 0,
                        "amount_coverage": 0.0,
                        "warning": warning,
                    }
                )
                continue

            selected = bars.loc[:, ["symbol", "trade_date", "amount"]].copy()
            selected["symbol"] = selected["symbol"].astype(str).str.zfill(6)
            selected["trade_date"] = pd.to_datetime(selected["trade_date"]).dt.normalize()
            selected["amount"] = pd.to_numeric(selected["amount"], errors="coerce")
            amount_frames.append(selected)
            coverage = float(selected["amount"].notna().mean())
            detail_rows.append(
                {
                    "symbol": returned,
                    "success": coverage == 1.0,
                    "provider": provider,
                    "row_count": len(selected),
                    "amount_coverage": coverage,
                    "first_bar": selected["trade_date"].min().date().isoformat(),
                    "last_bar": selected["trade_date"].max().date().isoformat(),
                    "warning": warning,
                }
            )

    detail = pd.DataFrame(detail_rows).sort_values("symbol").reset_index(drop=True)
    detail.to_csv(output / "detail.csv", index=False)
    if amount_frames:
        amounts = pd.concat(amount_frames, ignore_index=True)
        amounts = amounts.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    else:
        amounts = pd.DataFrame(columns=["symbol", "trade_date", "amount"])
    amounts.to_parquet(output / "amounts.parquet", index=False)

    success = detail["success"].astype(bool) if not detail.empty else pd.Series(dtype=bool)
    providers = (
        {
            str(key): int(value)
            for key, value in detail.loc[success, "provider"]
            .fillna("unknown")
            .value_counts()
            .items()
        }
        if not detail.empty
        else {}
    )
    payload = {
        "status": "complete",
        "purpose": "historical_amount_archive_pilot",
        "strict_point_in_time_evidence": False,
        "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(),
        "shard_count": shard_count,
        "shard_index": shard_index,
        "symbol_count": len(shard),
        "success_count": int(success.sum()) if len(success) else 0,
        "success_rate": float(success.mean()) if len(success) else 0.0,
        "amount_row_count": len(amounts),
        "providers": providers,
        "failed_symbols": (
            detail.loc[~success, "symbol"].astype(str).tolist() if len(detail) else []
        ),
        "pilot_pass": bool(len(success) and float(success.mean()) >= 0.95),
    }
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.historical_amount_archive")
    parser.add_argument("--lifecycle-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--period-start", default="2023-01-03")
    parser.add_argument("--period-end", required=True)
    parser.add_argument("--shard-count", type=int, default=32)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--workers", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_shard_archive(
        lifecycle_file=args.lifecycle_file,
        output_dir=args.output_dir,
        cache_root=args.cache_root,
        period_start=args.period_start,
        period_end=args.period_end,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        workers=args.workers,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
