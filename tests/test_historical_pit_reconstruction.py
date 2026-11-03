import pandas as pd

from ashare_system.historical_pit_reconstruction import (
    HistoricalSourceBundle,
    build_lifecycle,
    membership_as_of,
    normalize_szse_name_changes,
)


def _bundle():
    current = pd.DataFrame(
        [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "exchange": "SSE",
                "board": "上证主板",
                "list_date": pd.Timestamp("1999-11-10"),
                "currently_listed": True,
            },
            {
                "symbol": "000001",
                "name": "平安银行",
                "exchange": "SZSE",
                "board": "主板",
                "list_date": pd.Timestamp("1991-04-03"),
                "currently_listed": True,
            },
        ]
    )
    sse_delisted = pd.DataFrame(
        {
            "公司代码": ["600001", "900901"],
            "公司简称": ["退市样本", "退市B样本"],
            "上市日期": ["2000-01-01", "2000-01-01"],
            "暂停上市日期": ["2024-06-20", "2024-06-20"],
        }
    )
    szse_delisted = pd.DataFrame(
        {
            "证券代码": ["000002", "200002"],
            "证券简称": ["退市深样本", "退市深B样本"],
            "上市日期": ["2001-01-01", "2001-01-01"],
            "终止上市日期": ["2025-05-10", "2025-05-10"],
        }
    )
    return HistoricalSourceBundle(
        observed_at="2026-11-03T00:00:00+00:00",
        current=current,
        sse_delisted=sse_delisted,
        szse_delisted=szse_delisted,
        szse_name_changes=pd.DataFrame(),
        warnings=(),
    )


def test_lifecycle_combines_current_and_official_delisted_membership():
    lifecycle = build_lifecycle(_bundle())
    assert set(lifecycle["symbol"]) == {"600000", "600001", "000001", "000002"}
    assert not lifecycle["symbol"].str.startswith(("900", "200")).any()
    ended = lifecycle.set_index("symbol")
    assert not bool(ended.loc["600001", "currently_listed"])
    assert ended.loc["600001", "board"] == "上证主板"
    assert ended.loc["000002", "board"] == "深证主板"


def test_membership_as_of_uses_delist_date_as_exclusive_boundary():
    lifecycle = build_lifecycle(_bundle())
    before = membership_as_of(lifecycle, "2024-06-19")
    on_date = membership_as_of(lifecycle, "2024-06-20")
    assert "600001" in set(before["symbol"])
    assert "600001" not in set(on_date["symbol"])


def test_szse_name_changes_extract_st_transitions_without_inventing_dates():
    raw = pd.DataFrame(
        {
            "证券代码": ["000001", "000001", "000003"],
            "变更日期": ["2024-05-06", "2025-05-07", "2024-06-01"],
            "变更前简称": ["样本银行", "*ST样本", "普通公司"],
            "变更后简称": ["*ST样本", "样本银行", "新简称"],
        }
    )
    normalized, warnings = normalize_szse_name_changes(raw)
    assert warnings == []
    transitions = normalized[normalized["st_transition"]]
    assert len(transitions) == 2
    assert not bool(transitions.iloc[0]["is_st_before"])
    assert bool(transitions.iloc[0]["is_st_after"])
    assert bool(transitions.iloc[1]["is_st_before"])
    assert not bool(transitions.iloc[1]["is_st_after"])


def test_szse_name_change_parser_fails_closed_when_old_new_names_missing():
    raw = pd.DataFrame(
        {
            "证券代码": ["000001"],
            "变更日期": ["2024-05-06"],
            "其他字段": ["x"],
        }
    )
    normalized, warnings = normalize_szse_name_changes(raw)
    assert normalized.empty
    assert warnings
