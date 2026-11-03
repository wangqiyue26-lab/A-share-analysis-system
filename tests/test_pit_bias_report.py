import pytest

from ashare_system.pit_bias_report import compare_bias


def _strict():
    return {
        "status": "complete",
        "strategy_version": "capital-aware-v2",
        "strict_point_in_time_universe": True,
        "start": "2026-11-03",
        "end": "2026-12-01",
        "total_return": 0.05,
        "max_drawdown": -0.04,
        "rolling": {
            "median_return": 0.02,
            "median_excess_return": 0.01,
            "benchmark_outperformance_rate": 0.60,
            "return_excluding_best_block": 0.03,
        },
    }


def _current():
    return {
        "start": "2026-11-03",
        "end": "2026-12-01",
        "total_return": 0.08,
        "max_drawdown": -0.03,
        "rolling": {
            "median_return": 0.03,
            "median_excess_return": 0.02,
            "benchmark_outperformance_rate": 0.70,
            "return_excluding_best_block": 0.06,
        },
    }


def test_bias_report_compares_same_period_without_claiming_causality():
    report = compare_bias(_strict(), _current())
    assert report["strict_total_return"] == pytest.approx(0.05)
    assert report["current_universe_total_return"] == pytest.approx(0.08)
    assert report["current_minus_strict_return"] == pytest.approx(0.03)
    assert report["real_money_ready"] is False
    assert "not a guaranteed causal estimate" in report["interpretation"]


def test_bias_report_rejects_mismatched_periods():
    current = _current()
    current["end"] = "2026-12-02"
    with pytest.raises(ValueError, match="end dates differ"):
        compare_bias(_strict(), current)
