import json

import pandas as pd
import pytest

from ashare_system.pit_evidence import (
    PITEvidenceStore,
    persist_staged_evidence,
    stage_pit_evidence,
)
from ashare_system.strict_pit_v2 import audit_evidence_calendar


def _master(observed_at="2026-11-03T08:30:00Z"):
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "600000"],
            "name": ["A", "B", "C"],
            "exchange": ["SZSE", "SZSE", "SSE"],
            "board": ["主板", "主板", "上证主板"],
            "list_date": ["2000-01-01", "2001-01-01", "1999-01-01"],
            "industry": ["银行", "地产", "银行"],
            "total_shares": [1e9, 1e9, 1e9],
            "float_shares": [8e8, 8e8, 8e8],
            "is_st": [False, False, False],
            "is_listed": [True, True, True],
            "observed_at": [pd.Timestamp(observed_at)] * 3,
        }
    )


def _candidates():
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002"],
            "name": ["A", "B"],
            "exchange": ["SZSE", "SZSE"],
            "board": ["主板", "主板"],
            "list_date": ["2000-01-01", "2001-01-01"],
            "industry": ["银行", "地产"],
            "total_shares": [1e9, 1e9],
            "float_shares": [8e8, 8e8],
            "is_st": [False, False],
            "is_listed": [True, True],
            "observed_at": ["2026-11-03T08:30:00Z"] * 2,
            "amount": [300e6, 200e6],
            "float_market_cap": [20e9, 10e9],
            "screen_source": ["sina_spot_amount", "sina_spot_amount"],
            "listing_age_verified": [True, True],
        }
    )


def _stage(tmp_path, monkeypatch, date="2026-11-03"):
    cache = tmp_path / "cache"
    from ashare_system.data.security_master import SecurityMasterSnapshotStore

    SecurityMasterSnapshotStore(cache).save(_master())
    candidates = tmp_path / "candidates.csv"
    _candidates().to_csv(candidates, index=False)
    manifest = tmp_path / "universe_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "prepared_as_of": date,
                "degraded_mode": False,
                "spot_provider_source": "akshare_sina_all_a_spot",
                "spot_coverage": 0.99,
                "listing_age_verified": True,
                "security_master_source": "akshare_exchange_security_master",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "stage"
    stage_pit_evidence(
        cache_root=cache,
        candidates_file=candidates,
        universe_manifest_file=manifest,
        output_dir=output,
    )
    return output


def test_pit_evidence_persists_idempotently_and_loads_exact_candidates(tmp_path, monkeypatch):
    stage = _stage(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger"
    now = pd.Timestamp("2026-11-03T16:00:00", tz="Asia/Shanghai")

    first = persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=now,
    )
    second = persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=now,
    )

    assert first.status == "persisted"
    assert second.status == "idempotent"
    assert first.master_state_created is True
    store = PITEvidenceStore(ledger)
    assert [d.date().isoformat() for d in store.available_dates()] == ["2026-11-03"]
    frame = store.load_candidates("2026-11-03")
    assert frame["symbol"].tolist() == ["000001", "000002"]


def test_pit_evidence_market_clock_blocks_intraday_or_wrong_day(tmp_path, monkeypatch):
    stage = _stage(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger"

    intraday = persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-03T14:30:00", tz="Asia/Shanghai"),
    )
    weekend_like = persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-04T16:00:00", tz="Asia/Shanghai"),
    )

    assert intraday.status == "skipped_market_clock"
    assert weekend_like.status == "skipped_market_clock"
    assert not (ledger / "days").exists()


def test_pit_candidate_day_cannot_be_rewritten(tmp_path, monkeypatch):
    stage = _stage(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger"
    now = pd.Timestamp("2026-11-03T16:00:00", tz="Asia/Shanghai")
    persist_staged_evidence(staging_dir=stage, ledger_root=ledger, now=now)

    candidate_path = stage / "candidates.csv"
    changed = pd.read_csv(candidate_path, dtype={"symbol": str})
    changed.loc[0, "amount"] = 999e6
    changed.to_csv(candidate_path, index=False)
    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    manifest["candidate_sha256"] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Refusing to rewrite immutable PIT candidate"):
        persist_staged_evidence(staging_dir=stage, ledger_root=ledger, now=now)


def test_master_state_is_deduplicated_across_days(tmp_path, monkeypatch):
    stage = _stage(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger"
    first = persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-03T16:00:00", tz="Asia/Shanghai"),
    )
    assert first.master_state_created is True

    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence_date"] = "2026-11-04"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    second = persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-04T16:00:00", tz="Asia/Shanghai"),
    )
    assert second.master_state_created is False
    assert len(list((ledger / "security_master" / "states").glob("*.csv"))) == 1


def test_load_master_as_of_refuses_future_substitution(tmp_path, monkeypatch):
    stage = _stage(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger"
    persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-03T16:00:00", tz="Asia/Shanghai"),
    )
    store = PITEvidenceStore(ledger)
    with pytest.raises(FileNotFoundError, match="future/current state substitution is forbidden"):
        store.load_master_as_of("2026-11-02")
    master = store.load_master_as_of("2026-11-03")
    assert set(master["symbol"]) == {"000001", "000002", "600000"}


def test_strict_calendar_audit_detects_missing_trading_day(tmp_path, monkeypatch):
    stage = _stage(tmp_path, monkeypatch)
    ledger = tmp_path / "ledger"
    persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-03T16:00:00", tz="Asia/Shanghai"),
    )

    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence_date"] = "2026-11-05"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    persist_staged_evidence(
        staging_dir=stage,
        ledger_root=ledger,
        now=pd.Timestamp("2026-11-05T16:00:00", tz="Asia/Shanghai"),
    )

    benchmark = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-11-03", "2026-11-04", "2026-11-05"]),
            "close": [4000.0, 4010.0, 4020.0],
        }
    )
    audit = audit_evidence_calendar(PITEvidenceStore(ledger), benchmark)
    assert audit["status"] == "gap"
    assert audit["missing_dates"] == ["2026-11-04"]
