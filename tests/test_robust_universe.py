import json

import pandas as pd

import ashare_system.robust_universe as robust
from ashare_system.data.spot import SPOT_COLUMNS, LiveSpotResult


def _master() -> pd.DataFrame:
    observed = pd.Timestamp("2026-09-30T02:00:00Z")
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "600001"],
            "name": ["A", "B", "C"],
            "exchange": ["SZSE", "SZSE", "SSE"],
            "board": ["主板", "主板", "主板"],
            "list_date": ["2000-01-01"] * 3,
            "industry": ["测试"] * 3,
            "total_shares": [1e9] * 3,
            "float_shares": [8e8] * 3,
            "is_st": [False] * 3,
            "is_listed": [True] * 3,
            "observed_at": [observed] * 3,
        }
    )


def _spot() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002", "600001"],
            "name": ["A", "B", "C"],
            "last": [10.0, 20.0, 30.0],
            "amount": [300e6, 200e6, 100e6],
            "turnover_pct": [pd.NA] * 3,
            "market_cap": [pd.NA] * 3,
            "float_market_cap": [pd.NA] * 3,
        },
        columns=SPOT_COLUMNS,
    )


def test_robust_universe_treats_valid_sina_as_live_primary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        robust,
        "_complete_security_master",
        lambda cache: (_master(), "akshare_exchange_security_master", None),
    )
    monkeypatch.setattr(
        robust,
        "fetch_live_spot",
        lambda *args, **kwargs: LiveSpotResult(
            frame=_spot(),
            provider_name="akshare_sina_all_a_spot",
            screen_source="sina_spot_amount",
            coverage=1.0,
            warnings=(),
        ),
    )

    manifest = robust.prepare_robust_universe(
        output_root=tmp_path / "out",
        cache_root=tmp_path / "cache",
        candidate_limit=2,
        min_amount=50e6,
    )

    assert manifest["screen_source"] == "sina_spot_amount"
    assert manifest["degraded_mode"] is False
    assert manifest["spot_coverage"] == 1.0
    frame = pd.read_csv(tmp_path / "out" / "candidates.csv", dtype={"symbol": str})
    assert list(frame["symbol"]) == ["000001", "000002"]
    assert frame["listing_age_verified"].all()
    cache_manifest = json.loads(
        (tmp_path / "cache" / "candidate_universe" / "manifest.json").read_text(encoding="utf-8")
    )
    assert cache_manifest["source"] == "sina_spot_amount"


def test_robust_universe_uses_recent_verified_cache_after_live_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        robust,
        "_complete_security_master",
        lambda cache: (_master(), "akshare_exchange_security_master", None),
    )

    def fail_live(*args, **kwargs):
        raise RuntimeError("both live sources unavailable")

    cached = pd.DataFrame(
        {
            "symbol": ["000001", "000002"],
            "name": ["A", "B"],
            "exchange": ["SZSE", "SZSE"],
            "board": ["主板", "主板"],
            "is_st": [False, False],
            "screen_source": ["candidate_cache_fallback", "candidate_cache_fallback"],
            "listing_age_verified": [True, True],
            "amount": [300e6, 200e6],
            "float_market_cap": [pd.NA, pd.NA],
        }
    )
    monkeypatch.setattr(robust, "fetch_live_spot", fail_live)
    monkeypatch.setattr(
        robust,
        "_load_recent_candidate_cache",
        lambda *args, **kwargs: (cached.copy(), {"saved_at": "2026-09-30T01:00:00+00:00"}),
    )

    manifest = robust.prepare_robust_universe(
        output_root=tmp_path / "out",
        cache_root=tmp_path / "cache",
        candidate_limit=2,
    )

    assert manifest["screen_source"] == "candidate_cache_fallback"
    assert manifest["degraded_mode"] is True
    assert manifest["listing_age_verified"] is True
    assert "both live sources unavailable" in manifest["spot_warning"]
