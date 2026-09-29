import pytest

from ashare_system.backtest import TradingRuleSet, execution_price, trade_cost


def test_stamp_duty_historical_cutover():
    rules = TradingRuleSet()
    assert rules.stamp_duty_rate("2023-08-25", "sell") == pytest.approx(0.001)
    assert rules.stamp_duty_rate("2023-08-28", "sell") == pytest.approx(0.0005)
    assert rules.stamp_duty_rate("2026-09-01", "buy") == 0.0


def test_price_limit_history_by_board_and_st_status():
    rules = TradingRuleSet()
    assert rules.price_limit_pct(trade_date="2020-08-21", board="创业板") == pytest.approx(0.10)
    assert rules.price_limit_pct(trade_date="2020-08-24", board="创业板") == pytest.approx(0.20)
    assert rules.price_limit_pct(trade_date="2025-01-01", board="科创板") == pytest.approx(0.20)
    assert rules.price_limit_pct(trade_date="2025-01-01", board="北交所") == pytest.approx(0.30)
    assert rules.price_limit_pct(trade_date="2026-07-03", board="主板", is_st=True) == pytest.approx(0.05)
    assert rules.price_limit_pct(trade_date="2026-07-06", board="主板", is_st=True) == pytest.approx(0.10)


def test_trade_cost_has_minimum_commission_and_sell_stamp_duty():
    rules = TradingRuleSet(commission_rate=0.0003, minimum_commission_cny=5.0)
    buy = trade_cost(notional=1_000.0, side="buy", trade_date="2026-09-01", rules=rules)
    sell = trade_cost(notional=10_000.0, side="sell", trade_date="2026-09-01", rules=rules)
    assert buy.commission == pytest.approx(5.0)
    assert buy.stamp_duty == 0.0
    assert sell.commission == pytest.approx(5.0)
    assert sell.stamp_duty == pytest.approx(5.0)


def test_execution_price_applies_slippage_directionally():
    assert execution_price(10.0, "buy", 5) == pytest.approx(10.005)
    assert execution_price(10.0, "sell", 5) == pytest.approx(9.995)
