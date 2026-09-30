import json

import pandas as pd

from ashare_system.site import build_dashboard


def test_build_dashboard_with_selection_and_backtest(tmp_path):
    selection = tmp_path / "selection"
    backtest = tmp_path / "backtest"
    site = tmp_path / "site"
    selection.mkdir()
    backtest.mkdir()

    pd.DataFrame(
        {
            "rank": [1.0, 2.0],
            "symbol": ["000001", "600000"],
            "name": ["平安银行", "浦发银行"],
            "as_of": ["2026-09-28", "2026-09-28"],
            "composite_score": [1.2, 0.8],
            "momentum_20": [0.10, 0.05],
            "momentum_60": [0.22, 0.11],
            "trend_ma20_ma60": [0.03, 0.01],
            "volatility_20": [0.02, 0.03],
            "max_drawdown_60": [-0.08, -0.12],
            "factor_coverage": [1.0, 1.0],
        }
    ).to_csv(selection / "selected.csv", index=False)
    (selection / "summary.json").write_text(
        json.dumps(
            {
                "selected_count": 2,
                "ranked_count": 5,
                "history_success_count": 6,
                "excluded_count": 1,
                "screen_source": "bootstrap_sina_spot_amount",
                "security_master_source": "degraded_current_universe",
            }
        ),
        encoding="utf-8",
    )

    pd.DataFrame(
        {
            "trade_date": ["2026-09-25", "2026-09-28"],
            "portfolio_index": [1.0, 1.05],
            "benchmark_index": [1.0, 1.02],
            "excess_index": [1.0, 1.05 / 1.02],
        }
    ).to_csv(backtest / "benchmark.csv", index=False)
    (backtest / "summary.json").write_text(
        json.dumps(
            {
                "portfolio_return": 0.05,
                "benchmark_return": 0.02,
                "excess_return": 1.05 / 1.02 - 1,
                "max_drawdown": -0.03,
                "turnover": 1.2,
                "total_cost": 88.5,
            }
        ),
        encoding="utf-8",
    )

    index = build_dashboard(site, selection_dir=selection, backtest_dir=backtest)
    text = index.read_text(encoding="utf-8")

    assert "A股量化研究系统" in text
    assert "000001" in text
    assert "平安银行" in text
    assert "沪深300" in text
    assert "备用源" in text
    assert "bootstrap_sina_spot_amount" in text
    assert "不构成个股推荐" in text
    assert (site / ".nojekyll").exists()
    metadata = json.loads((site / "site-meta.json").read_text(encoding="utf-8"))
    assert metadata["selection_rows"] == 2
    assert metadata["has_backtest"] is True
    assert metadata["degraded_data"] is True


def test_build_dashboard_can_render_empty_state(tmp_path):
    index = build_dashboard(tmp_path / "site")
    text = index.read_text(encoding="utf-8")
    assert "暂无选股结果" in text
    assert "暂无基准对比曲线" in text
