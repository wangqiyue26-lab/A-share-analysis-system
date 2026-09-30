import json

import pandas as pd

import ashare_system.enrich_daily as enrich_module
from ashare_system.enrich_daily import enrich_daily_outputs


def _base_outputs(tmp_path):
    selection = tmp_path / "selection"
    site = tmp_path / "site"
    selection.mkdir()
    site.mkdir()
    pd.DataFrame(
        {
            "rank": [1, 2],
            "symbol": ["000001", "600000"],
            "name": ["平安银行", "浦发银行"],
            "as_of": ["2026-09-30", "2026-09-30"],
            "composite_score": [1.2, 0.8],
        }
    ).to_csv(selection / "selected.csv", index=False)
    (selection / "summary.json").write_text(
        json.dumps({"selected_count": 2}, ensure_ascii=False),
        encoding="utf-8",
    )
    (site / "index.html").write_text(
        """<html><body><main>
  <section class="panel">
    <h2>最新量化排名</h2>
    <div class="subtitle">当前综合分由动量、趋势、波动、回撤和成交额等市场因子组成；后续阶段再接入经过 Point-in-Time 校验的基本面因子。</div>
  </section>
  <section class="panel">
    <h2>回测与基准</h2>
  </section>
</main></body></html>""",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"selected_count": 2}), encoding="utf-8")
    return selection, site, manifest


def test_daily_enrichment_writes_page_and_updates_metadata(tmp_path, monkeypatch):
    selection, site, manifest = _base_outputs(tmp_path)

    def fake_snapshot(symbols, *, as_of, cache_root, output_dir, workers, refresh_days, stale_days):
        assert list(symbols) == ["000001", "600000"]
        assert pd.Timestamp(as_of) == pd.Timestamp("2026-09-30")
        pd.DataFrame(
            {
                "symbol": ["000001", "600000"],
                "fundamental_as_of": ["2026-09-30", "2026-09-30"],
                "fundamental_period_end": ["2026-06-30", "2026-06-30"],
                "revenue_yoy": [0.12, 0.08],
                "net_profit_yoy": [0.10, 0.04],
                "net_margin": [0.21, 0.18],
                "ocf_to_net_profit": [1.15, 0.92],
                "debt_to_assets": [0.70, 0.73],
                "fundamental_coverage": [1.0, 1.0],
                "fundamental_source": ["test", "test"],
                "fundamental_warning": [None, None],
            }
        ).to_csv(output_dir / "fundamentals.csv", index=False)
        (output_dir / "fundamental_summary.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-09-30T00:00:00+00:00",
                    "requested_count": 2,
                    "success_count": 2,
                    "failure_count": 0,
                    "average_coverage": 1.0,
                    "scoring_integration": False,
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(enrich_module, "build_fundamental_snapshot", fake_snapshot)
    result = enrich_daily_outputs(
        selection_dir=selection,
        site_dir=site,
        manifest_path=manifest,
        cache_root=tmp_path / "cache",
    )

    assert result.status == "success"
    assert result.success_count == 2
    page = (site / "fundamentals.html").read_text(encoding="utf-8")
    assert "PIT 基本面补充" in page
    assert "平安银行" in page
    assert "12.00%" in page
    assert "当前页面用于补充解释，不参与市场因子综合分" in page

    index = (site / "index.html").read_text(encoding="utf-8")
    assert "fundamentals.html" in index
    assert "暂不参与综合分" in index
    assert index.count("PIT-FUNDAMENTALS-START") == 1

    selection_summary = json.loads((selection / "summary.json").read_text(encoding="utf-8"))
    assert selection_summary["fundamental_success_count"] == 2
    assert selection_summary["fundamental_scoring_integration"] is False
    production_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    assert production_manifest["fundamental_success_count"] == 2
    assert production_manifest["fundamental_scoring_integration"] is False


def test_daily_enrichment_degrades_without_breaking_market_site(tmp_path, monkeypatch):
    selection, site, manifest = _base_outputs(tmp_path)

    def failing_snapshot(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(enrich_module, "build_fundamental_snapshot", failing_snapshot)
    result = enrich_daily_outputs(
        selection_dir=selection,
        site_dir=site,
        manifest_path=manifest,
        cache_root=tmp_path / "cache",
    )

    assert result.status == "degraded"
    assert result.success_count == 0
    summary = json.loads((selection / "fundamental_summary.json").read_text(encoding="utf-8"))
    assert "provider unavailable" in summary["pipeline_error"]
    assert summary["scoring_integration"] is False
    assert "fundamentals.html" in (site / "index.html").read_text(encoding="utf-8")
    page = (site / "fundamentals.html").read_text(encoding="utf-8")
    assert "市场因子排名未受影响" in page
