import pandas as pd
import pytest

from ashare_system.data.spot import (
    SPOT_COLUMNS,
    fetch_live_spot,
    live_spot_coverage,
    normalize_sina_spot,
)


def _master() -> pd.DataFrame:
    observed = pd.Timestamp("2026-09-30T02:00:00Z")
    symbols = ["000001", "000002", "300001", "600001", "600002"]
    return pd.DataFrame(
        {
            "symbol": symbols,
            "name": ["A", "B", "C", "D", "E"],
            "exchange": ["SZSE", "SZSE", "SZSE", "SSE", "SSE"],
            "board": ["主板", "主板", "创业板", "主板", "主板"],
            "list_date": ["2000-01-01"] * 5,
            "industry": ["测试"] * 5,
            "total_shares": [1e9] * 5,
            "float_shares": [8e8] * 5,
            "is_st": [False] * 5,
            "is_listed": [True] * 5,
            "observed_at": [observed] * 5,
        }
    )


def _spot(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbols,
            "name": symbols,
            "last": [10.0] * len(symbols),
            "amount": [100_000_000.0] * len(symbols),
            "turnover_pct": [pd.NA] * len(symbols),
            "market_cap": [pd.NA] * len(symbols),
            "float_market_cap": [pd.NA] * len(symbols),
        },
        columns=SPOT_COLUMNS,
    )


class _FailProvider:
    name = "fail"
    screen_source = "fail_spot_amount"

    def get_current(self):
        raise ConnectionError("blocked")


class _PartialProvider:
    name = "partial"
    screen_source = "partial_spot_amount"

    def get_current(self):
        return _spot(["000001", "000002"])


class _GoodProvider:
    name = "good"
    screen_source = "sina_spot_amount"

    def get_current(self):
        return _spot(["000001", "000002", "300001", "600001", "600002"])


def test_normalize_sina_spot_accepts_prefixed_codes():
    raw = pd.DataFrame(
        {
            "代码": ["sz000001", "sh600001"],
            "名称": ["A", "B"],
            "最新价": [10.0, 20.0],
            "成交额": [100e6, 200e6],
        }
    )
    frame = normalize_sina_spot(raw)
    assert list(frame["symbol"]) == ["000001", "600001"]
    assert frame.loc[1, "amount"] == 200e6


def test_live_spot_coverage_uses_current_listed_exchange_symbols():
    coverage = live_spot_coverage(_master(), _spot(["000001", "000002", "300001", "600001"]))
    assert coverage == pytest.approx(0.8)


def test_live_spot_fails_over_between_first_class_sources():
    result = fetch_live_spot(
        _master(),
        providers=(_FailProvider(), _GoodProvider()),
        min_coverage=0.8,
    )
    assert result.provider_name == "good"
    assert result.screen_source == "sina_spot_amount"
    assert result.coverage == 1.0
    assert len(result.warnings) == 1
    assert "ConnectionError" in result.warnings[0]


def test_live_spot_rejects_partial_payload_before_trying_next_source():
    result = fetch_live_spot(
        _master(),
        providers=(_PartialProvider(), _GoodProvider()),
        min_coverage=0.8,
    )
    assert result.provider_name == "good"
    assert "coverage 40.0%" in result.warnings[0]


def test_live_spot_fails_closed_when_all_sources_fail_quality_gate():
    with pytest.raises(RuntimeError, match="All live all-market spot providers failed"):
        fetch_live_spot(
            _master(),
            providers=(_FailProvider(), _PartialProvider()),
            min_coverage=0.8,
        )
