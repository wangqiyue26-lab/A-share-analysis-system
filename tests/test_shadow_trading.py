import pandas as pd
import pytest

from ashare_system.shadow_trading import capture_signal, evaluate_signals, load_signal_history


def _selected(symbols=("000001", "600000")):
    count = len(symbols)
    return pd.DataFrame(
        {
            "symbol": list(symbols),
            "name": [f"股票{i}" for i in range(count)],
            "as_of": ["2026-09-29"] * count,
            "board": ["主板"] * count,
            "is_st": [False] * count,
            "rank": list(range(1, count + 1)),
            "composite_score": [1.0 - i * 0.1 for i in range(count)],
            "screen_source": ["sina_spot_amount"] * count,
        }
    )


def _bars(symbol, prices):
    dates = pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-09"])
    return pd.DataFrame(
        {
            "symbol": [symbol] * 3,
            "trade_date": dates,
            "open": prices,
            "high": [price * 1.01 for price in prices],
            "low": [price * 0.99 for price in prices],
            "close": prices,
            "volume": [1_000_000] * 3,
            "amount": [100_000_000.0] * 3,
            "amplitude_pct": [2.0] * 3,
            "change_pct": [0.0] * 3,
            "change": [0.0] * 3,
            "turnover_pct": [1.0] * 3,
        }
    )


def test_capture_signal_is_immutable_but_idempotent(tmp_path):
    selected_file = tmp_path / "selected.csv"
    _selected().to_csv(selected_file, index=False)
    ledger = tmp_path / "ledger"
    observed = pd.Timestamp("2026-09-29T09:30:00Z")

    first = capture_signal(selected_file, ledger, observed_at=observed)
    second = capture_signal(
        selected_file,
        ledger,
        observed_at=pd.Timestamp("2026-09-29T10:00:00Z"),
    )
    assert first == second
    history = load_signal_history(ledger)
    assert len(history) == 2
    assert history["target_weight"].sum() == pytest.approx(1.0)

    changed = _selected(("000001", "300001"))
    changed.to_csv(selected_file, index=False)
    with pytest.raises(RuntimeError, match="Refusing to rewrite immutable shadow signal"):
        capture_signal(selected_file, ledger, observed_at=observed)


def test_shadow_execution_uses_next_trading_day_open_and_reports_daily_win_rate(tmp_path):
    selected_file = tmp_path / "selected.csv"
    _selected().to_csv(selected_file, index=False)
    ledger = tmp_path / "ledger"
    capture_signal(
        selected_file,
        ledger,
        observed_at=pd.Timestamp("2026-09-29T09:30:00Z"),
    )
    signals = load_signal_history(ledger)
    bars = {
        "000001": _bars("000001", [10.0, 10.0, 10.5]),
        "600000": _bars("600000", [20.0, 20.0, 21.0]),
    }

    equity, trades, metrics = evaluate_signals(signals, bars, initial_cash=100_000.0)

    assert not trades.empty
    assert set(trades["trade_date"]) == {pd.Timestamp("2026-09-30")}
    assert set(trades["signal_date"]) == {pd.Timestamp("2026-09-29")}
    assert equity.iloc[-1]["equity"] > 100_000.0
    assert metrics["total_return"] > 0
    assert metrics["daily_win_rate"] > 0
    assert metrics["real_money_ready"] is False
