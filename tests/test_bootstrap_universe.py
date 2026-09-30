import pandas as pd

from ashare_system.bootstrap_universe import build_bootstrap_candidates, normalize_sina_spot
from ashare_system.data.spot import SPOT_COLUMNS


def test_bootstrap_candidates_filters_st_exchange_and_liquidity():
    spot = pd.DataFrame(
        [
            ["600001", "沪股A", 10.0, 220_000_000.0, 1.2, 20_000_000_000.0, 15_000_000_000.0],
            ["000001", "深股A", 9.0, 180_000_000.0, 1.1, 18_000_000_000.0, 12_000_000_000.0],
            ["300001", "*ST测试", 8.0, 500_000_000.0, 4.0, 5_000_000_000.0, 4_000_000_000.0],
            ["830001", "北股A", 7.0, 400_000_000.0, 2.0, 4_000_000_000.0, 3_000_000_000.0],
            ["600002", "低成交", 6.0, 10_000_000.0, 0.3, 3_000_000_000.0, 2_000_000_000.0],
        ],
        columns=SPOT_COLUMNS,
    )

    result = build_bootstrap_candidates(
        spot,
        limit=10,
        min_amount=50_000_000.0,
        allowed_exchanges=("SSE", "SZSE"),
        screen_source="bootstrap_eastmoney_spot_amount",
    )

    assert result["symbol"].tolist() == ["600001", "000001"]
    assert result["exchange"].tolist() == ["SSE", "SZSE"]
    assert not result["is_st"].any()
    assert not result["listing_age_verified"].any()
    assert set(result["screen_source"]) == {"bootstrap_eastmoney_spot_amount"}


def test_bootstrap_candidates_classifies_growth_and_star_boards():
    spot = pd.DataFrame(
        [
            ["688001", "科创A", 10.0, 200_000_000.0, 1.0, 9_000_000_000.0, 8_000_000_000.0],
            ["300001", "创业A", 10.0, 190_000_000.0, 1.0, 8_000_000_000.0, 7_000_000_000.0],
        ],
        columns=SPOT_COLUMNS,
    )

    result = build_bootstrap_candidates(spot, limit=5, min_amount=0)
    boards = dict(zip(result["symbol"], result["board"], strict=True))
    assert boards["688001"] == "科创板"
    assert boards["300001"] == "创业板"


def test_normalize_sina_spot_extracts_prefixed_symbols():
    raw = pd.DataFrame(
        {
            "代码": ["sh600001", "sz000001"],
            "名称": ["沪股A", "深股A"],
            "最新价": [10.0, 9.0],
            "成交额": [200_000_000.0, 180_000_000.0],
        }
    )

    result = normalize_sina_spot(raw)

    assert result["symbol"].tolist() == ["600001", "000001"]
    assert result["amount"].tolist() == [200_000_000.0, 180_000_000.0]
    assert result["float_market_cap"].isna().all()
