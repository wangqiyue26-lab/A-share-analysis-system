import pandas as pd
import pytest

from ashare_system.data.spot import normalize_eastmoney_spot, select_liquid_candidates


def _master() -> pd.DataFrame:
    observed = pd.Timestamp("2026-09-29T08:00:00Z")
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "300001", "600001", "920001"],
            "name": ["平安银行", "万科A", "测试新股", "ST测试", "北交测试"],
            "exchange": ["SZSE", "SZSE", "SZSE", "SSE", "BSE"],
            "board": ["主板", "主板", "创业板", "上证主板", "北交所"],
            "list_date": ["1991-04-03", "1991-01-29", "2026-08-01", "2000-01-01", "2020-01-01"],
            "industry": ["银行", "地产", "测试", "测试", "测试"],
            "total_shares": [20e9, 12e9, 1e9, 2e9, 3e9],
            "float_shares": [19e9, 11e9, 0.2e9, 1.5e9, 2.5e9],
            "is_st": [False, False, False, True, False],
            "is_listed": [True, True, True, True, True],
            "observed_at": [observed] * 5,
        }
    )


def _raw_spot() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "代码": ["000001", "000002", "300001", "600001", "920001"],
            "名称": ["平安银行", "万科A", "测试新股", "ST测试", "北交测试"],
            "最新价": [10.0, 8.0, 20.0, 3.0, 12.0],
            "成交额": [100e6, 300e6, 500e6, 800e6, 2_000e6],
            "换手率": [1.0, 2.0, 5.0, 6.0, 10.0],
            "总市值": [200e9, 100e9, 20e9, 10e9, 30e9],
            "流通市值": [190e9, 90e9, 5e9, 8e9, 25e9],
        }
    )


def test_normalize_eastmoney_spot():
    frame = normalize_eastmoney_spot(_raw_spot())
    assert list(frame["symbol"]) == ["000001", "000002", "300001", "600001", "920001"]
    assert frame.loc[1, "amount"] == 300e6


def test_liquidity_screen_filters_st_new_listing_and_disallowed_exchange():
    spot = normalize_eastmoney_spot(_raw_spot())
    result = select_liquid_candidates(
        _master(),
        spot,
        as_of="2026-09-29",
        limit=2,
        min_amount=50e6,
        min_listing_days=120,
        allowed_exchanges=("SSE", "SZSE"),
    )
    assert list(result["symbol"]) == ["000002", "000001"]
    assert set(result["screen_source"]) == {"eastmoney_spot_amount"}
    assert set(result["exchange"]) <= {"SSE", "SZSE"}


def test_screen_fails_closed_when_spot_is_unavailable():
    with pytest.raises(RuntimeError, match="spot snapshot is unavailable"):
        select_liquid_candidates(
            _master(),
            None,
            as_of="2026-09-29",
            limit=2,
            min_listing_days=120,
            allowed_exchanges=("SSE", "SZSE"),
        )


def test_screen_rejects_unknown_exchange_filter():
    with pytest.raises(ValueError, match="Unsupported allowed exchanges"):
        select_liquid_candidates(
            _master(),
            None,
            as_of="2026-09-29",
            allowed_exchanges=("NYSE",),
        )
