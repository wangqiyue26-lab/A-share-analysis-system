import pandas as pd

from ashare_system.factors.engine import FactorEngine
from ashare_system.factors.scoring import load_factor_config


def make_bars(symbol: str, *, slope: float, amount: float, rows: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    closes = pd.Series([10.0 + slope * i for i in range(rows)], dtype=float)
    returns = closes.pct_change().fillna(0.0)
    return pd.DataFrame(
        {
            "symbol": [symbol] * rows,
            "trade_date": dates,
            "open": closes * 0.997,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": [2_000_000] * rows,
            "amount": [amount] * rows,
            "amplitude_pct": [2.0] * rows,
            "change_pct": returns * 100,
            "change": closes.diff().fillna(0.0),
            "turnover_pct": [1.0] * rows,
        }
    )


def test_factor_engine_ranks_and_reports_exclusions():
    engine = FactorEngine(
        load_factor_config("config/factors.yml"),
        min_average_amount_20=20_000_000,
    )
    result = engine.run(
        {
            "000001": make_bars("000001", slope=0.04, amount=80_000_000),
            "000002": make_bars("000002", slope=0.01, amount=40_000_000),
            "000003": make_bars("000003", slope=0.02, amount=5_000_000),
        }
    )
    assert set(result.ranking["symbol"]) == {"000001", "000002"}
    assert result.ranking.iloc[0]["rank"] == 1
    assert result.ranking["factor_coverage"].eq(1.0).all()
    assert result.exclusions.iloc[0]["symbol"] == "000003"
    assert result.exclusions.iloc[0]["reason"] == "below_min_average_amount_20"
