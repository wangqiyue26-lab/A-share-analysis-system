import pandas as pd

from ashare_system.backtest import BacktestEngine, TradingRuleSet


def _bars(opens, closes, volumes=None):
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    if volumes is None:
        volumes = [1_000_000] * 4
    frame = pd.DataFrame(
        {
            "symbol": ["000001"] * 4,
            "trade_date": dates,
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes, strict=True)],
            "low": [min(o, c) for o, c in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": volumes,
            "amount": [10_000_000.0] * 4,
            "amplitude_pct": [0.0] * 4,
            "change_pct": [0.0] * 4,
            "change": [0.0] * 4,
            "turnover_pct": [1.0] * 4,
        }
    )
    return frame


def _signal(date, weight):
    return {
        "trade_date": pd.Timestamp(date),
        "symbol": "000001",
        "target_weight": weight,
        "board": "主板",
        "is_st": False,
    }


def test_signal_executes_on_next_trading_day_and_sell_is_t_plus_one_safe():
    bars = _bars([10.0, 10.1, 10.2, 10.3], [10.0, 10.1, 10.2, 10.3])
    signals = pd.DataFrame([_signal("2024-01-02", 0.9), _signal("2024-01-03", 0.0)])
    result = BacktestEngine(initial_cash=100_000).run({"000001": bars}, signals)

    assert list(result.trades["side"]) == ["buy", "sell"]
    assert result.trades.iloc[0]["signal_date"] == pd.Timestamp("2024-01-02")
    assert result.trades.iloc[0]["trade_date"] == pd.Timestamp("2024-01-03")
    assert result.trades.iloc[1]["trade_date"] == pd.Timestamp("2024-01-04")


def test_limit_up_buy_is_retried_on_next_tradeable_day():
    bars = _bars([10.0, 11.0, 10.5, 10.6], [10.0, 11.0, 10.5, 10.6])
    signals = pd.DataFrame([_signal("2024-01-02", 0.8)])
    rules = TradingRuleSet(slippage_bps=0)
    result = BacktestEngine(rules=rules, initial_cash=100_000).run({"000001": bars}, signals)

    assert len(result.trades) == 1
    assert result.trades.iloc[0]["trade_date"] == pd.Timestamp("2024-01-04")


def test_suspension_with_zero_volume_defers_order():
    bars = _bars(
        [10.0, 10.0, 10.1, 10.2],
        [10.0, 10.0, 10.1, 10.2],
        volumes=[1_000_000, 0, 1_000_000, 1_000_000],
    )
    signals = pd.DataFrame([_signal("2024-01-02", 0.5)])
    result = BacktestEngine(initial_cash=100_000).run({"000001": bars}, signals)

    assert len(result.trades) == 1
    assert result.trades.iloc[0]["trade_date"] == pd.Timestamp("2024-01-04")
    assert result.metrics["max_drawdown"] <= 0
