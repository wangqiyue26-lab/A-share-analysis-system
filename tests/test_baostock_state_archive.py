import json

import pandas as pd
import pytest

from ashare_system.baostock_state_archive import merge_state_shards


def _write_state_shard(root, index, count, symbol, *, success_rate=1.0):
    folder = root / f"shard-{index}"
    folder.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "symbol": [symbol, symbol],
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "is_st": [False, True],
            "trade_status": [True, False],
            "close": [10.0, None],
        }
    )
    frame.to_parquet(folder / "states.parquet", index=False)
    payload = {
        "status": "complete",
        "period_start": "2023-01-03",
        "period_end": "2026-11-03",
        "shard_count": count,
        "shard_index": index,
        "symbol_count": 1,
        "success_count": 1 if success_rate >= 1 else 0,
        "success_rate": success_rate,
        "failed_symbols": [],
    }
    (folder / "summary.json").write_text(json.dumps(payload), encoding="utf-8")


def test_merge_state_shards_builds_candidate_input(tmp_path):
    root = tmp_path / "shards"
    _write_state_shard(root, 0, 2, "600000")
    _write_state_shard(root, 1, 2, "000001")
    output = tmp_path / "out"

    manifest = merge_state_shards(
        shards_root=root,
        output_dir=output,
        expected_shards=2,
    )

    assert manifest["status"] == "complete"
    assert manifest["symbol_count"] == 2
    assert manifest["unique_symbols"] == 2
    assert manifest["state_row_count"] == 4
    assert manifest["candidate_reconstruction_state_ready"] is True
    merged = pd.read_parquet(output / "historical_states_2023_2026.parquet")
    assert len(merged) == 4


def test_merge_state_shards_rejects_duplicate_symbol_date(tmp_path):
    root = tmp_path / "shards"
    _write_state_shard(root, 0, 2, "600000")
    _write_state_shard(root, 1, 2, "600000")
    with pytest.raises(RuntimeError, match="duplicate symbol/date"):
        merge_state_shards(
            shards_root=root,
            output_dir=tmp_path / "out",
            expected_shards=2,
        )


def test_merge_state_shards_keeps_success_gate(tmp_path):
    root = tmp_path / "shards"
    _write_state_shard(root, 0, 2, "600000")
    _write_state_shard(root, 1, 2, "000001", success_rate=0.90)
    with pytest.raises(RuntimeError, match="below gate"):
        merge_state_shards(
            shards_root=root,
            output_dir=tmp_path / "out",
            expected_shards=2,
            min_success_rate=0.95,
        )
