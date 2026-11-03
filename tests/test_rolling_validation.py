import pandas as pd
import pytest

from ashare_system.rolling_validation import evaluate_rolling_blocks, pass_development_gate


def test_rolling_validation_builds_complete_twenty_day_blocks():
    dates = pd.bdate_range("2026-01-02", periods=41)
    equity = pd.DataFrame(
        {
            "trade_date": dates,
            "equity": [20_000.0 * (1.001**index) for index in range(len(dates))],
        }
    )
    benchmark = pd.DataFrame(
        {
            "trade_date": dates,
            "close": [4_000.0 * (1.0002**index) for index in range(len(dates))],
        }
    )
    trades = pd.DataFrame(
        {
            "trade_date": [dates[5], dates[25]],
            "notional": [5_000.0, 5_000.0],
            "commission": [5.0, 5.0],
            "stamp_duty": [0.0, 0.0],
            "transfer_fee": [0.05, 0.05],
            "slippage_cost": [2.5, 2.5],
        }
    )

    blocks, summary = evaluate_rolling_blocks(
        equity,
        trades=trades,
        benchmark=benchmark,
        block_days=20,
    )

    assert len(blocks) == 2
    assert summary.block_count == 2
    assert summary.median_return > 0
    assert summary.median_excess_return > 0
    assert summary.benchmark_outperformance_rate == pytest.approx(1.0)
    assert blocks["trade_count"].sum() == 2


def test_development_gate_requires_enough_blocks():
    dates = pd.bdate_range("2026-01-02", periods=41)
    equity = pd.DataFrame(
        {"trade_date": dates, "equity": [20_000.0 + index * 10 for index in range(len(dates))]}
    )
    blocks, summary = evaluate_rolling_blocks(equity, block_days=20)
    assert len(blocks) == 2
    assert pass_development_gate(summary) is False
