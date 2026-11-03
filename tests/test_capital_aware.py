import pandas as pd
import pytest

from ashare_system.capital_aware import (
    CapitalAwarePortfolioConfig,
    capital_aware_stock_signals,
    combine_portfolio_signals,
    etf_rotation_signals,
)


def _config():
    return CapitalAwarePortfolioConfig(
        initial_cash=20_000.0,
        max_stock_positions=3,
        stock_weight_per_position=0.25,
        max_total_stock_weight=0.75,
        min_cash_weight=0.10,
        entry_rank_max=5,
        keep_rank_max=10,
        rebalance_every_trading_days=5,
        minimum_holding_trading_days=5,
        affordability_buffer_rate=0.002,
        require_positive_momentum_20_for_entry=True,
        require_close_above_ma20_for_entry=True,
        require_close_above_ma20_to_keep_after_min_hold=True,
        etf_enabled=True,
        etf_target_weight=0.15,
        etf_max_weight_if_stock_slots_unused=0.40,
    )


def _ranking(date, rows):
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "rank": rank,
                "board": board,
                "is_st": False,
                "close": close,
                "momentum_20": 0.10,
                "trend_close_ma20": 0.05,
                "as_of": date,
            }
            for symbol, rank, board, close in rows
        ]
    )


def test_capital_aware_selector_skips_unaffordable_high_price_stock():
    dates = pd.bdate_range("2026-01-05", periods=6)
    rankings = {}
    rows = [
        ("600519", 1, "上证主板", 1400.0),
        ("000001", 2, "深证主板", 12.0),
        ("600000", 3, "上证主板", 10.0),
        ("000002", 4, "深证主板", 8.0),
    ]
    for date in dates:
        rankings[date] = _ranking(date, rows)

    signals = capital_aware_stock_signals(rankings, config=_config())
    first = signals[(signals["trade_date"] == dates[0]) & (signals["target_weight"] > 0)]
    assert "600519" not in set(first["symbol"])
    assert list(first["symbol"]) == ["000001", "000002", "600000"]
    assert first["target_weight"].sum() == pytest.approx(0.75)


def test_selector_does_not_rebalance_every_day():
    dates = pd.bdate_range("2026-01-05", periods=10)
    rankings = {}
    for index, date in enumerate(dates):
        rankings[date] = _ranking(
            date,
            [
                ("000001", 1 if index < 5 else 9, "深证主板", 10.0),
                ("600000", 2, "上证主板", 10.0),
                ("000002", 3, "深证主板", 10.0),
                ("600001", 4, "上证主板", 10.0),
            ],
        )

    signals = capital_aware_stock_signals(rankings, config=_config())
    assert set(signals["trade_date"]) == {dates[0], dates[5]}
    second_active = signals[
        (signals["trade_date"] == dates[5]) & (signals["target_weight"] > 0)
    ]
    assert "000001" in set(second_active["symbol"])


def _etf_bars(symbol, closes, dates):
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(dates),
            "trade_date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000] * len(dates),
            "amount": [100_000_000.0] * len(dates),
            "amplitude_pct": [0.0] * len(dates),
            "change_pct": [0.0] * len(dates),
            "change": [0.0] * len(dates),
            "turnover_pct": [1.0] * len(dates),
        }
    )


def test_etf_sleeve_fills_unused_stock_budget_but_keeps_cash():
    dates = pd.bdate_range("2025-09-01", periods=70)
    rebalance_date = dates[-1]
    stock_signals = pd.DataFrame(
        [
            {
                "trade_date": rebalance_date,
                "symbol": "000001",
                "target_weight": 0.25,
                "board": "主板",
                "is_st": False,
                "instrument_type": "stock",
                "source_rank": 1,
            }
        ]
    )
    rising = [1.0 + index * 0.01 for index in range(len(dates))]
    flat = [1.0] * len(dates)
    etf = etf_rotation_signals(
        {
            "510300": _etf_bars("510300", rising, dates),
            "510500": _etf_bars("510500", flat, dates),
        },
        rebalance_dates=[rebalance_date],
        stock_signals=stock_signals,
        config=_config(),
    )
    assert len(etf) == 1
    assert etf.iloc[0]["symbol"] == "510300"
    assert etf.iloc[0]["target_weight"] == pytest.approx(0.40)
    combined = combine_portfolio_signals(stock_signals, etf)
    assert combined.groupby("trade_date")["target_weight"].sum().iloc[0] == pytest.approx(0.65)
