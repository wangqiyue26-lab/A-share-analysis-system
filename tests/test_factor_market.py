import pandas as pd
import pytest

from ashare_system.factors.market import FactorDataError, compute_market_factors


def make_bars(symbol: str = "000001", rows: int = 80, daily_step: float = 0.01) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    closes = pd.Series([10.0 + daily_step * i for i in range(rows)], dtype=float)
    return pd.DataFrame(
        {
            "symbol": [symbol] * rows,
            "trade_date": dates,
            "open": closes * 0.995,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": [2_000_000] * rows,
            "amount": [30_000_000] * rows,
            "amplitude_pct": [2.0] * rows,
            "change_pct": closes.pct_change().fillna(0.0) * 100,
            "change": closes.diff().fillna(0.0),
            "turnover_pct": [1.2] * rows,
        }
    )


def test_compute_market_factors_has_expected_signs():
    snapshot = compute_market_factors(make_bars())
    assert snapshot["symbol"] == "000001"
    assert snapshot["momentum_20"] > 0
    assert snapshot["momentum_60"] > snapshot["momentum_20"]
    assert snapshot["trend_ma20_ma60"] > 0
    assert snapshot["volatility_20"] >= 0
    assert snapshot["max_drawdown_60"] == pytest.approx(0.0)
    assert snapshot["log_amount_20"] > 0


def test_compute_market_factors_rejects_short_history():
    with pytest.raises(FactorDataError, match="at least 61"):
        compute_market_factors(make_bars(rows=60))
