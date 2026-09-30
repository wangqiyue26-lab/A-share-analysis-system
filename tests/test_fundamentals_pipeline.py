import json

import pandas as pd
import pytest

from ashare_system.fundamentals_pipeline import build_fundamental_snapshot


def _sample_facts() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period, available, revenue, profit, ocf, assets, liabilities in (
        ("2023-06-30", "2023-08-30", 100.0, 10.0, 12.0, 80.0, 36.0),
        ("2024-06-30", "2024-08-30", 120.0, 12.0, 15.0, 100.0, 50.0),
    ):
        for metric, value in (
            ("利润表::营业收入", revenue),
            ("利润表::净利润", profit),
            ("现金流量表::经营活动产生的现金流量净额", ocf),
            ("资产负债表::资产总计", assets),
            ("资产负债表::负债合计", liabilities),
        ):
            rows.append(
                {
                    "symbol": "000001",
                    "metric": metric,
                    "value": value,
                    "period_end": period,
                    "available_at": available,
                    "source": "fake_financial_provider",
                }
            )
    return pd.DataFrame(rows)


class FakeProvider:
    name = "fake_financial_provider"

    def get_all(self, symbol: str) -> pd.DataFrame:
        frame = _sample_facts().copy()
        frame["symbol"] = str(symbol).zfill(6)
        return frame


class MustNotBeCalledProvider:
    name = "must_not_be_called"

    def get_all(self, symbol: str) -> pd.DataFrame:
        raise AssertionError(f"fresh cache should have prevented a provider call for {symbol}")


def test_build_fundamental_snapshot_writes_pit_research_artifacts_and_reuses_fresh_cache(tmp_path):
    cache_root = tmp_path / "cache"
    output = tmp_path / "output"
    first = build_fundamental_snapshot(
        ["000001"],
        as_of="2024-09-01",
        cache_root=cache_root,
        output_dir=output,
        workers=1,
        provider_factory=FakeProvider,
    )

    assert first.success_count == 1
    frame = pd.read_csv(first.fundamentals_file, dtype={"symbol": str})
    assert frame.iloc[0]["revenue_yoy"] == pytest.approx(0.20)
    assert frame.iloc[0]["fundamental_coverage"] == pytest.approx(1.0)
    assert frame.iloc[0]["fundamental_source"] == "fake_financial_provider"
    summary = json.loads((output / "fundamental_summary.json").read_text(encoding="utf-8"))
    assert summary["scoring_integration"] is False

    second_output = tmp_path / "second"
    second = build_fundamental_snapshot(
        ["000001"],
        as_of="2024-09-01",
        cache_root=cache_root,
        output_dir=second_output,
        workers=1,
        provider_factory=MustNotBeCalledProvider,
    )
    assert second.success_count == 1
    second_frame = pd.read_csv(second.fundamentals_file, dtype={"symbol": str})
    assert second_frame.iloc[0]["fundamental_source"] == "fresh_cache"
