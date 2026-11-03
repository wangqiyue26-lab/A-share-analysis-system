import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ashare_system.historical_amount_merge import merge_shards


def _write_shard(root, index, shard_count, symbol, success_rate=1.0):
    folder = root / f"shard-{index}"
    folder.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "symbol": [symbol, symbol],
            "trade_date": pd.to_datetime(["2023-01-03", "2023-01-04"]),
            "amount": [100.0 + index, 110.0 + index],
        }
    )
    frame.to_parquet(folder / "amounts.parquet", index=False)
    payload = {
        "status": "complete",
        "period_start": "2023-01-03",
        "period_end": "2026-11-03",
        "shard_count": shard_count,
        "shard_index": index,
        "symbol_count": 1,
        "success_count": 1 if success_rate >= 1 else 0,
        "success_rate": success_rate,
        "providers": {"test": 1},
        "failed_symbols": [],
    }
    (folder / "summary.json").write_text(json.dumps(payload), encoding="utf-8")


def test_merge_shards_streams_validated_archive(tmp_path):
    root = tmp_path / "shards"
    for index in range(2):
        _write_shard(root, index, 2, f"60000{index}")

    output = tmp_path / "out"
    manifest = merge_shards(
        shards_root=root,
        output_dir=output,
        expected_shards=2,
        min_success_rate=0.95,
    )

    assert manifest["status"] == "complete"
    assert manifest["symbol_count"] == 2
    assert manifest["unique_symbols_with_amount"] == 2
    assert manifest["amount_row_count"] == 4
    assert manifest["candidate_reconstruction_input_ready"] is True
    table = pq.read_table(output / "historical_amounts_2023_2026.parquet")
    assert table.num_rows == 4


def test_merge_rejects_duplicate_symbol_across_shards(tmp_path):
    root = tmp_path / "shards"
    _write_shard(root, 0, 2, "600000")
    _write_shard(root, 1, 2, "600000")
    with pytest.raises(RuntimeError, match="multiple deterministic shards"):
        merge_shards(
            shards_root=root,
            output_dir=tmp_path / "out",
            expected_shards=2,
        )


def test_merge_rejects_low_success_shard(tmp_path):
    root = tmp_path / "shards"
    _write_shard(root, 0, 2, "600000")
    _write_shard(root, 1, 2, "600001", success_rate=0.80)
    with pytest.raises(RuntimeError, match="below"):
        merge_shards(
            shards_root=root,
            output_dir=tmp_path / "out",
            expected_shards=2,
            min_success_rate=0.95,
        )
