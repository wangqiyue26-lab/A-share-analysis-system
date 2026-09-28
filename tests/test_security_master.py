import pandas as pd
import pytest

from ashare_system.data.security_master import (
    AkshareSecurityMasterProvider,
    REQUIRED_EXCHANGES,
    SecurityMasterSnapshotStore,
    normalize_bj_security_master,
    normalize_sh_security_master,
    normalize_sz_security_master,
    validate_security_master,
)

OBSERVED = pd.Timestamp("2026-09-28T09:00:00Z")


def make_sh() -> pd.DataFrame:
    return normalize_sh_security_master(
        pd.DataFrame(
            {
                "证券代码": ["600000"],
                "证券简称": ["浦发银行"],
                "上市日期": ["1999-11-10"],
            }
        ),
        board="上证主板",
        observed_at=OBSERVED,
    )


def make_sz() -> pd.DataFrame:
    return normalize_sz_security_master(
        pd.DataFrame(
            {
                "板块": ["主板"],
                "A股代码": ["000001"],
                "A股简称": ["平安银行"],
                "A股上市日期": ["1991-04-03"],
                "A股总股本": [1000],
                "A股流通股本": [900],
                "所属行业": ["金融"],
            }
        ),
        observed_at=OBSERVED,
    )


def make_bj() -> pd.DataFrame:
    return normalize_bj_security_master(
        pd.DataFrame(
            {
                "证券代码": ["430047"],
                "证券简称": ["诺思兰德"],
                "总股本": [100],
                "流通股本": [80],
                "上市日期": ["2020-11-24"],
                "所属行业": ["医药"],
            }
        ),
        observed_at=OBSERVED,
    )


def test_normalize_exchange_security_master_rows():
    master = validate_security_master(pd.concat([make_sh(), make_sz(), make_bj()], ignore_index=True))
    assert set(master["exchange"]) == REQUIRED_EXCHANGES
    assert master["symbol"].tolist() == ["000001", "430047", "600000"]
    assert master["is_listed"].all()


def test_security_master_marks_current_st_name():
    master = normalize_sh_security_master(
        pd.DataFrame(
            {
                "证券代码": ["600001"],
                "证券简称": ["*ST示例"],
                "上市日期": ["2000-01-01"],
            }
        ),
        board="上证主板",
        observed_at=OBSERVED,
    )
    assert bool(master.iloc[0]["is_st"]) is True


def test_security_master_rejects_duplicate_symbols():
    row = make_sh()
    with pytest.raises(ValueError, match="Duplicate security symbols"):
        validate_security_master(pd.concat([row, row], ignore_index=True))


def test_security_master_snapshot_round_trip(tmp_path):
    master = validate_security_master(pd.concat([make_sh(), make_sz(), make_bj()], ignore_index=True))
    store = SecurityMasterSnapshotStore(tmp_path)
    path = store.save(master)
    assert path.exists()
    loaded = store.load_latest()
    assert set(loaded["exchange"]) == REQUIRED_EXCHANGES


def test_snapshot_store_rejects_partial_master(tmp_path):
    store = SecurityMasterSnapshotStore(tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        store.save(make_sh())


def test_provider_reports_partial_exchange_failure(monkeypatch):
    provider = AkshareSecurityMasterProvider()
    monkeypatch.setattr(provider, "_fetch_sse", lambda observed_at: make_sh())
    monkeypatch.setattr(provider, "_fetch_bse", lambda observed_at: make_bj())

    def broken_szse(observed_at):
        raise ConnectionError("synthetic SZSE outage")

    monkeypatch.setattr(provider, "_fetch_szse", broken_szse)
    result = provider.fetch_current()
    assert result.complete is False
    assert result.fetched_exchanges == ("BSE", "SSE")
    assert len(result.failures) == 1
    assert result.failures[0].startswith("SZSE:")


def test_provider_rejects_partial_master_by_default(monkeypatch):
    provider = AkshareSecurityMasterProvider()
    monkeypatch.setattr(provider, "_fetch_sse", lambda observed_at: make_sh())
    monkeypatch.setattr(provider, "_fetch_bse", lambda observed_at: make_bj())
    monkeypatch.setattr(
        provider,
        "_fetch_szse",
        lambda observed_at: (_ for _ in ()).throw(ConnectionError("synthetic outage")),
    )
    with pytest.raises(RuntimeError, match="Incomplete security master"):
        provider.get_current()
