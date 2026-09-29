import pandas as pd
import pytest

from ashare_system.backtest import (
    benchmark_comparison,
    equal_weight_rebalance_signals,
    trade_statistics,
)
from ashare_system.data.security_master import SecurityMasterSnapshotStore


def _master(observed_at: str, suffix: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["600001", "000001", "430001"],
            "name": [f"SH{suffix}", f"SZ{suffix}", f"BJ{suffix}"],
            "exchange": ["SSE", "SZSE", "BSE"],
            "board": ["上证主板", "主板", "北交所"],
            "list_date": ["2000-01-01", "1991-01-01", "2021-11-15"],
            "industry": ["A", "B", "C"],
            "total_shares": [1_000.0, 2_000.0, 3_000.0],
            "float_shares": [900.0, 1_800.0, 2_500.0],
            "is_st": [False, False, False],
            "is_listed": [True, True, True],
            "observed_at": [observed_at] * 3,
        }
    )


def test_security_master_load_as_of_never_uses_future_snapshot(tmp_path):
    store = SecurityMasterSnapshotStore(tmp_path)
    store.save(_master("2024-01-05T08:00:00Z", "old"))
    store.save(_master("2024-02-05T08:00:00Z", "new"))

    historical = store.load_as_of("2024-01-20")
    assert historical.loc[historical["exchange"] == "SSE", "name"].iloc[0] == "SHold"

    latest = store.load_as_of("2024-03-01")
    assert latest.loc[latest["exchange"] == "SSE", "name"].iloc[0] == "SHnew"

    with pytest.raises(FileNotFoundError, match="earliest retained snapshot is newer"):
        store.load_as_of("2023-12-31")


def test_equal_weight_rebalance_emits_zero_for_dropped_holding():
    first = pd.DataFrame(
        {
            "symbol": ["000001", "000002", "000003"],
            "rank": [1, 2, 3],
            "board": ["主板", "主板", "主板"],
            "is_st": [False, False, False],
        }
    )
    second = pd.DataFrame(
        {
            "symbol": ["000003", "000001", "000002"],
            "rank": [1, 2, 3],
            "board": ["主板", "主板", "主板"],
            "is_st": [False, False, False],
        }
    )
    signals = equal_weight_rebalance_signals(
        {"2024-01-02": first, "2024-02-01": second},
        top_n=2,
    )

    jan = signals[signals["trade_date"] == pd.Timestamp("2024-01-02")]
    feb = signals[signals["trade_date"] == pd.Timestamp("2024-02-01")]
    assert set(jan["symbol"]) == {"000001", "000002"}
    assert jan["target_weight"].sum() == pytest.approx(1.0)
    assert feb.loc[feb["symbol"] == "000002", "target_weight"].iloc[0] == 0.0
    assert feb.loc[feb["symbol"] == "000003", "target_weight"].iloc[0] == pytest.approx(0.5)


def test_trade_statistics_and_benchmark_comparison():
    equity = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "equity": [100_000.0, 102_000.0, 105_000.0],
        }
    )
    trades = pd.DataFrame(
        {
            "side": ["buy", "sell"],
            "notional": [50_000.0, 52_000.0],
            "commission": [15.0, 15.6],
            "stamp_duty": [0.0, 26.0],
        }
    )
    stats = trade_statistics(equity, trades)
    assert stats["trade_count"] == 2
    assert stats["gross_traded_notional"] == pytest.approx(102_000.0)
    assert stats["total_cost"] == pytest.approx(56.6)
    assert stats["turnover"] == pytest.approx(102_000.0 / equity["equity"].mean())

    benchmark = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "close": [100.0, 101.0, 102.0],
        }
    )
    aligned, comparison = benchmark_comparison(equity, benchmark)
    assert len(aligned) == 3
    assert comparison["portfolio_return"] == pytest.approx(0.05)
    assert comparison["benchmark_return"] == pytest.approx(0.02)
    assert comparison["excess_return"] == pytest.approx(1.05 / 1.02 - 1)
