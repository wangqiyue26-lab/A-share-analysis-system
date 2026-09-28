import pandas as pd
import pytest

from ashare_system.data.security_master import (
    SecurityMasterSnapshotStore,
    normalize_bj_security_master,
    normalize_sh_security_master,
    normalize_sz_security_master,
    validate_security_master,
)

OBSERVED = pd.Timestamp("2026-09-28T09:00:00Z")


def test_normalize_exchange_security_master_rows():
    sh = normalize_sh_security_master(
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
    sz = normalize_sz_security_master(
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
    bj = normalize_bj_security_master(
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
    master = validate_security_master(pd.concat([sh, sz, bj], ignore_index=True))
    assert set(master["exchange"]) == {"SSE", "SZSE", "BSE"}
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
    row = normalize_sh_security_master(
        pd.DataFrame(
            {
                "证券代码": ["600000"],
                "证券简称": ["示例"],
                "上市日期": ["2000-01-01"],
            }
        ),
        board="上证主板",
        observed_at=OBSERVED,
    )
    with pytest.raises(ValueError, match="Duplicate security symbols"):
        validate_security_master(pd.concat([row, row], ignore_index=True))


def test_security_master_snapshot_round_trip(tmp_path):
    master = normalize_sh_security_master(
        pd.DataFrame(
            {
                "证券代码": ["600000"],
                "证券简称": ["示例"],
                "上市日期": ["2000-01-01"],
            }
        ),
        board="上证主板",
        observed_at=OBSERVED,
    )
    store = SecurityMasterSnapshotStore(tmp_path)
    path = store.save(master)
    assert path.exists()
    loaded = store.load_latest()
    assert loaded.iloc[0]["symbol"] == "600000"
