import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from ashare_system.archive_security_master import archive_latest_security_master
from ashare_system.data.security_master import SecurityMasterSnapshotStore


def _complete_master(observed_at: str) -> pd.DataFrame:
    rows = []
    for symbol, name, exchange, board in [
        ("600000", "浦发银行", "SSE", "上证主板"),
        ("000001", "平安银行", "SZSE", "深证主板"),
        ("430047", "诺思兰德", "BSE", "北交所"),
    ]:
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "board": board,
                "list_date": "2000-01-01",
                "industry": "测试行业",
                "total_shares": 1_000_000,
                "float_shares": 800_000,
                "is_st": False,
                "is_listed": True,
                "observed_at": observed_at,
            }
        )
    return pd.DataFrame(rows)


def test_archive_latest_security_master_writes_checksum_manifest(tmp_path):
    cache_root = tmp_path / "cache"
    store = SecurityMasterSnapshotStore(cache_root)
    store.save(_complete_master("2026-09-29T07:00:00Z"))
    store.save(_complete_master("2026-09-30T07:00:00Z"))

    output_dir = tmp_path / "archive"
    result = archive_latest_security_master(cache_root=cache_root, output_dir=output_dir)

    snapshot = Path(result.snapshot_file)
    manifest_path = Path(result.manifest_file)
    assert snapshot.name == "security_master_20260930T070000Z.parquet"
    assert snapshot.exists()
    assert manifest_path.exists()
    assert result.row_count == 3
    assert result.exchanges == ("BSE", "SSE", "SZSE")

    checksum = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert result.sha256 == checksum
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["observed_at"] == "2026-09-30T07:00:00+00:00"
    assert manifest["sha256"] == checksum
    assert manifest["strict_point_in_time_eligible"] is True
    assert manifest["row_count"] == 3


def test_archive_requires_existing_snapshot(tmp_path):
    with pytest.raises(FileNotFoundError, match="No security-master snapshots"):
        archive_latest_security_master(
            cache_root=tmp_path / "cache",
            output_dir=tmp_path / "archive",
        )
