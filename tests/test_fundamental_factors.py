import math

import pandas as pd
import pytest

from ashare_system.factors.fundamental import compute_fundamental_factors


def _row(metric: str, value: float, period_end: str, available_at: str, source: str = "demo") -> dict[str, object]:
    return {
        "symbol": "000001",
        "metric": metric,
        "value": value,
        "period_end": period_end,
        "available_at": available_at,
        "source": source,
    }


def sample_fundamentals() -> pd.DataFrame:
    rows = [
        _row("利润表::营业收入", 100.0, "2023-06-30", "2023-08-30"),
        _row("利润表::净利润", 10.0, "2023-06-30", "2023-08-30"),
        _row("现金流量表::经营活动产生的现金流量净额", 12.0, "2023-06-30", "2023-08-30"),
        _row("资产负债表::资产总计", 80.0, "2023-06-30", "2023-08-30"),
        _row("资产负债表::负债合计", 36.0, "2023-06-30", "2023-08-30"),
        _row("利润表::营业收入", 120.0, "2024-06-30", "2024-08-30"),
        _row("利润表::净利润", 12.0, "2024-06-30", "2024-08-30"),
        _row("现金流量表::经营活动产生的现金流量净额", 15.0, "2024-06-30", "2024-08-30"),
        _row("资产负债表::资产总计", 100.0, "2024-06-30", "2024-08-30"),
        _row("资产负债表::负债合计", 50.0, "2024-06-30", "2024-08-30"),
        _row("利润表::营业收入", 126.0, "2024-06-30", "2024-09-15", "restatement"),
        _row("利润表::净利润", 14.0, "2024-06-30", "2024-09-15", "restatement"),
    ]
    return pd.DataFrame(rows)


def test_fundamental_factors_use_same_period_yoy_and_accounting_ratios():
    factors = compute_fundamental_factors(sample_fundamentals(), "2024-09-01")
    row = factors.iloc[0]

    assert row["fundamental_period_end"] == pd.Timestamp("2024-06-30", tz="UTC")
    assert row["revenue_yoy"] == pytest.approx(0.20)
    assert row["net_profit_yoy"] == pytest.approx(0.20)
    assert row["net_margin"] == pytest.approx(0.10)
    assert row["ocf_to_net_profit"] == pytest.approx(1.25)
    assert row["debt_to_assets"] == pytest.approx(0.50)
    assert row["fundamental_coverage"] == pytest.approx(1.0)


def test_fundamental_factors_do_not_see_future_restatement():
    before = compute_fundamental_factors(sample_fundamentals(), "2024-09-01").iloc[0]
    after = compute_fundamental_factors(sample_fundamentals(), "2024-09-20").iloc[0]

    assert before["revenue_yoy"] == pytest.approx(0.20)
    assert after["revenue_yoy"] == pytest.approx(0.26)
    assert before["net_profit_yoy"] == pytest.approx(0.20)
    assert after["net_profit_yoy"] == pytest.approx(0.40)


def test_fundamental_growth_is_missing_before_current_report_is_available():
    factors = compute_fundamental_factors(sample_fundamentals(), "2024-08-15")
    row = factors.iloc[0]

    assert row["fundamental_period_end"] == pd.Timestamp("2023-06-30", tz="UTC")
    assert math.isnan(row["revenue_yoy"])
    assert math.isnan(row["net_profit_yoy"])
    assert row["net_margin"] == pytest.approx(0.10)
    assert row["fundamental_coverage"] == pytest.approx(3 / 5)


def test_profit_growth_is_missing_for_non_positive_prior_profit():
    facts = sample_fundamentals()
    mask = (facts["metric"] == "利润表::净利润") & (facts["period_end"] == "2023-06-30")
    facts.loc[mask, "value"] = -2.0

    row = compute_fundamental_factors(facts, "2024-09-01").iloc[0]
    assert math.isnan(row["net_profit_yoy"])
