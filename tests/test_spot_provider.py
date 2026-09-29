import pandas as pd

from ashare_system.data.spot import (
    normalize_eastmoney_spot,
    normalize_sina_spot,
    prefilter_spot_snapshot,
)


def test_eastmoney_spot_normalizes_volume_to_shares():
    raw = pd.DataFrame(
        {
            "代码": ["000001", "600000"],
            "名称": ["平安银行", "浦发银行"],
            "最新价": [10.0, 9.0],
            "涨跌幅": [1.0, -1.0],
            "成交量": [1000.0, 2000.0],
            "成交额": [100_000_000.0, 200_000_000.0],
            "换手率": [1.1, 2.2],
            "市盈率-动态": [6.0, 5.0],
            "市净率": [0.6, 0.5],
            "总市值": [1000.0, 2000.0],
            "流通市值": [900.0, 1800.0],
        }
    )
    result = normalize_eastmoney_spot(raw, pd.Timestamp("2026-09-29T08:00:00Z"))
    assert result.loc[result["symbol"] == "000001", "volume"].iloc[0] == 100_000.0
    assert result["source"].eq("akshare_eastmoney_spot").all()


def test_sina_spot_strips_exchange_prefix_and_keeps_share_volume():
    raw = pd.DataFrame(
        {
            "代码": ["sz000001", "sh600000", "bj430001"],
            "名称": ["平安银行", "浦发银行", "北交样本"],
            "最新价": [10.0, 9.0, 8.0],
            "涨跌幅": [1.0, -1.0, 0.5],
            "成交量": [12345.0, 23456.0, 34567.0],
            "成交额": [100_000_000.0, 200_000_000.0, 50_000_000.0],
        }
    )
    result = normalize_sina_spot(raw, pd.Timestamp("2026-09-29T08:00:00Z"))
    assert set(result["symbol"]) == {"000001", "600000", "430001"}
    assert result.loc[result["symbol"] == "000001", "volume"].iloc[0] == 12345.0
    assert result["total_market_cap"].isna().all()


def test_prefilter_uses_liquidity_and_excludes_st():
    raw = pd.DataFrame(
        {
            "代码": ["000001", "000002", "000003", "000004"],
            "名称": ["A", "ST B", "C", "D"],
            "最新价": [10.0, 10.0, 0.8, 10.0],
            "涨跌幅": [0.0] * 4,
            "成交量": [1000.0] * 4,
            "成交额": [500_000_000.0, 900_000_000.0, 800_000_000.0, 100_000_000.0],
        }
    )
    snapshot = normalize_eastmoney_spot(raw, pd.Timestamp("2026-09-29T08:00:00Z"))
    result = prefilter_spot_snapshot(snapshot, top_n=2, min_amount=50_000_000.0, min_price=1.0)
    assert list(result["symbol"]) == ["000001", "000004"]
