from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_shards(
    *,
    shards_root: str | Path,
    output_dir: str | Path,
    expected_shards: int = 32,
    min_success_rate: float = 0.95,
) -> dict[str, object]:
    root = Path(shards_root)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    summary_paths = sorted(root.glob("*/summary.json"))
    if len(summary_paths) != expected_shards:
        raise RuntimeError(
            f"Expected {expected_shards} shard summaries, found {len(summary_paths)}"
        )

    summaries: list[dict[str, object]] = []
    shard_indexes: set[int] = set()
    total_symbols = 0
    success_symbols = 0
    failed_symbols: list[str] = []
    providers: dict[str, int] = {}
    period_start: str | None = None
    period_end: str | None = None

    for path in summary_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        index = int(payload["shard_index"])
        if index in shard_indexes:
            raise RuntimeError(f"Duplicate shard index: {index}")
        shard_indexes.add(index)
        if int(payload["shard_count"]) != expected_shards:
            raise RuntimeError(
                f"Shard {index} was created with shard_count={payload['shard_count']}"
            )
        success_rate = float(payload["success_rate"])
        if success_rate < min_success_rate:
            raise RuntimeError(
                f"Shard {index} success_rate={success_rate:.4f} below {min_success_rate:.4f}"
            )
        if period_start is None:
            period_start = str(payload["period_start"])
            period_end = str(payload["period_end"])
        elif (
            period_start != str(payload["period_start"])
            or period_end != str(payload["period_end"])
        ):
            raise RuntimeError("Shard periods are inconsistent")

        total_symbols += int(payload["symbol_count"])
        success_symbols += int(payload["success_count"])
        failed_symbols.extend(str(value) for value in payload["failed_symbols"])
        for provider, count in dict(payload["providers"]).items():
            providers[str(provider)] = providers.get(str(provider), 0) + int(count)
        summaries.append(payload)

    expected_indexes = set(range(expected_shards))
    if shard_indexes != expected_indexes:
        missing = sorted(expected_indexes - shard_indexes)
        extra = sorted(shard_indexes - expected_indexes)
        raise RuntimeError(f"Shard index set mismatch; missing={missing}, extra={extra}")

    parquet_paths = sorted(root.glob("*/amounts.parquet"))
    if len(parquet_paths) != expected_shards:
        raise RuntimeError(
            f"Expected {expected_shards} shard parquet files, found {len(parquet_paths)}"
        )

    final_path = output / "historical_amounts_2023_2026.parquet"
    writer: pq.ParquetWriter | None = None
    target_schema: pa.Schema | None = None
    total_rows = 0
    seen_symbols: set[str] = set()
    duplicate_symbol_shards: list[str] = []
    shard_files: list[dict[str, object]] = []

    try:
        for path in parquet_paths:
            table = pq.read_table(path)
            required = {"symbol", "trade_date", "amount"}
            missing_columns = required - set(table.column_names)
            if missing_columns:
                raise RuntimeError(f"{path} missing columns {sorted(missing_columns)}")

            symbol_values = {
                str(value).zfill(6)
                for value in table.column("symbol").to_pylist()
                if value is not None
            }
            overlap = seen_symbols & symbol_values
            if overlap:
                duplicate_symbol_shards.extend(sorted(overlap))
            seen_symbols.update(symbol_values)

            if table.num_rows:
                amount = table.column("amount")
                if amount.null_count:
                    raise RuntimeError(f"{path} contains null amount values")

            if writer is None:
                target_schema = table.schema
                writer = pq.ParquetWriter(
                    final_path,
                    target_schema,
                    compression="zstd",
                )
            elif target_schema is not None and table.schema != target_schema:
                table = table.cast(target_schema)

            writer.write_table(table)
            total_rows += table.num_rows
            shard_files.append(
                {
                    "file": path.as_posix(),
                    "rows": table.num_rows,
                    "sha256": _sha256_file(path),
                }
            )
    finally:
        if writer is not None:
            writer.close()

    if duplicate_symbol_shards:
        raise RuntimeError(
            "Symbols appeared in multiple deterministic shards: "
            f"{sorted(set(duplicate_symbol_shards))[:20]}"
        )
    if not final_path.exists():
        raise RuntimeError("Merged historical amount parquet was not created")

    aggregate_success_rate = success_symbols / total_symbols if total_symbols else 0.0
    if aggregate_success_rate < min_success_rate:
        raise RuntimeError(
            f"Aggregate success rate {aggregate_success_rate:.4f} below gate"
        )

    manifest = {
        "status": "complete",
        "purpose": "historical_amount_cross_section_archive",
        "strict_point_in_time_evidence": False,
        "reconstruction_grade": "historical_market_data_reconstructed",
        "period_start": period_start,
        "period_end": period_end,
        "shard_count": expected_shards,
        "symbol_count": total_symbols,
        "success_count": success_symbols,
        "failed_symbol_count": len(failed_symbols),
        "success_rate": aggregate_success_rate,
        "failed_symbols": sorted(set(failed_symbols)),
        "unique_symbols_with_amount": len(seen_symbols),
        "amount_row_count": total_rows,
        "providers": providers,
        "output_file": final_path.name,
        "output_sha256": _sha256_file(final_path),
        "shard_files": shard_files,
        "candidate_reconstruction_input_ready": bool(
            aggregate_success_rate >= min_success_rate and total_rows > 0
        ),
        "note": (
            "This archive removes current-survivor-only amount history from candidate "
            "reconstruction by including lifecycle-relevant current and delisted A shares. "
            "Historical ST state is still a separate evidence requirement."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.historical_amount_merge")
    parser.add_argument("--shards-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-shards", type=int, default=32)
    parser.add_argument("--min-success-rate", type=float, default=0.95)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = merge_shards(
        shards_root=args.shards_root,
        output_dir=args.output_dir,
        expected_shards=args.expected_shards,
        min_success_rate=args.min_success_rate,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
