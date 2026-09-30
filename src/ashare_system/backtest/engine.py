from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ashare_system.data.schema import validate_bars

from .costs import execution_price, trade_cost
from .metrics import performance_summary
from .rules import TradingRuleSet, is_limit_blocked


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, float]


@dataclass
class _Position:
    shares: int = 0
    last_buy_date: pd.Timestamp | None = None
    board: str = "主板"
    is_st: bool = False


@dataclass
class _PendingTarget:
    symbol: str
    signal_date: pd.Timestamp
    target_weight: float
    board: str
    is_st: bool


class BacktestEngine:
    """Long-only daily-bar engine with conservative A-share execution constraints."""

    def __init__(self, rules: TradingRuleSet | None = None, initial_cash: float = 1_000_000.0):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.rules = rules or TradingRuleSet()
        self.initial_cash = float(initial_cash)

    def run(self, bars_by_symbol: dict[str, pd.DataFrame], signals: pd.DataFrame) -> BacktestResult:
        if not bars_by_symbol:
            raise ValueError("bars_by_symbol cannot be empty")
        required_signal_columns = {"trade_date", "symbol", "target_weight", "board", "is_st"}
        missing = sorted(required_signal_columns - set(signals.columns))
        if missing:
            raise ValueError(f"signals missing columns: {missing}")

        bars: dict[str, pd.DataFrame] = {}
        all_dates: set[pd.Timestamp] = set()
        for symbol, frame in bars_by_symbol.items():
            normalized = validate_bars(frame).copy()
            normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.normalize()
            normalized = normalized.set_index("trade_date", drop=False)
            key = str(symbol).zfill(6)
            bars[key] = normalized
            all_dates.update(pd.Timestamp(value).normalize() for value in normalized.index)

        signal_frame = signals.copy()
        signal_frame["trade_date"] = pd.to_datetime(signal_frame["trade_date"]).dt.normalize()
        signal_frame["symbol"] = signal_frame["symbol"].astype(str).str.zfill(6)
        if ((signal_frame["target_weight"] < 0) | (signal_frame["target_weight"] > 1)).any():
            raise ValueError("target_weight must be between 0 and 1")
        if signal_frame.groupby("trade_date")["target_weight"].sum().gt(1.0000001).any():
            raise ValueError("target weights cannot sum above 1 on a signal date")

        calendar = sorted(all_dates)
        cash = self.initial_cash
        positions = {symbol: _Position() for symbol in bars}
        pending: dict[str, _PendingTarget] = {}
        trade_rows: list[dict[str, object]] = []
        equity_rows: list[dict[str, object]] = []

        signals_by_date = {
            date: group.copy()
            for date, group in signal_frame.groupby("trade_date", sort=True)
        }

        for date in calendar:
            for symbol in sorted(pending):
                order = pending[symbol]
                if date <= order.signal_date or symbol not in bars or date not in bars[symbol].index:
                    continue
                row = bars[symbol].loc[date]
                if pd.isna(row.get("volume")) or float(row.get("volume", 0.0)) <= 0:
                    continue

                history = bars[symbol].loc[bars[symbol].index < date]
                if history.empty:
                    continue
                previous_close = float(history.iloc[-1]["close"])

                raw_open = float(row["open"])
                pos = positions[symbol]
                open_equity = self._portfolio_value(cash, positions, bars, date, field="open")
                target_value = open_equity * float(order.target_weight)
                side_guess = "buy" if target_value > pos.shares * raw_open else "sell"
                board = order.board if side_guess == "buy" or pos.shares <= 0 else pos.board
                is_st = order.is_st if side_guess == "buy" or pos.shares <= 0 else pos.is_st
                limit_pct = self.rules.price_limit_pct(
                    trade_date=date,
                    board=board,
                    is_st=is_st,
                )
                if is_limit_blocked(
                    side=side_guess,
                    open_price=raw_open,
                    previous_close=previous_close,
                    limit_pct=limit_pct,
                ):
                    continue

                fill = execution_price(raw_open, side_guess, self.rules.slippage_bps)
                target_shares = self.rules.round_lot(target_value / fill)
                delta = target_shares - pos.shares
                if delta == 0:
                    pending.pop(symbol, None)
                    continue

                side = "buy" if delta > 0 else "sell"
                if side == "sell" and pos.last_buy_date is not None and date <= pos.last_buy_date:
                    continue

                quantity = abs(delta)
                if side == "buy":
                    max_affordable = self.rules.round_lot(cash / fill)
                    quantity = min(quantity, max_affordable)
                    if quantity <= 0:
                        continue

                notional = quantity * fill
                costs = trade_cost(
                    notional=notional,
                    side=side,
                    trade_date=date,
                    rules=self.rules,
                )
                if side == "buy":
                    total_cash = notional + costs.total
                    while quantity >= self.rules.board_lot and total_cash > cash:
                        quantity -= self.rules.board_lot
                        notional = quantity * fill
                        costs = trade_cost(
                            notional=notional,
                            side=side,
                            trade_date=date,
                            rules=self.rules,
                        )
                        total_cash = notional + costs.total
                    if quantity <= 0:
                        continue
                    cash -= total_cash
                    pos.shares += quantity
                    pos.last_buy_date = date
                    pos.board = order.board
                    pos.is_st = order.is_st
                else:
                    quantity = min(quantity, pos.shares)
                    if quantity <= 0:
                        pending.pop(symbol, None)
                        continue
                    notional = quantity * fill
                    costs = trade_cost(
                        notional=notional,
                        side=side,
                        trade_date=date,
                        rules=self.rules,
                    )
                    cash += notional - costs.total
                    pos.shares -= quantity

                trade_rows.append(
                    {
                        "trade_date": date,
                        "symbol": symbol,
                        "side": side,
                        "shares": quantity,
                        "price": fill,
                        "notional": notional,
                        "commission": costs.commission,
                        "stamp_duty": costs.stamp_duty,
                        "signal_date": order.signal_date,
                    }
                )
                pending.pop(symbol, None)

            equity = self._portfolio_value(cash, positions, bars, date, field="close")
            equity_rows.append({"trade_date": date, "cash": cash, "equity": equity})

            if date in signals_by_date:
                group = signals_by_date[date]
                wanted = set(group["symbol"])
                for symbol, pos in positions.items():
                    if pos.shares > 0 and symbol not in wanted:
                        pending[symbol] = _PendingTarget(
                            symbol,
                            date,
                            0.0,
                            pos.board,
                            pos.is_st,
                        )
                for _, signal in group.iterrows():
                    pending[str(signal["symbol"])] = _PendingTarget(
                        symbol=str(signal["symbol"]),
                        signal_date=date,
                        target_weight=float(signal["target_weight"]),
                        board=str(signal["board"]),
                        is_st=bool(signal["is_st"]),
                    )

        equity_curve = pd.DataFrame(equity_rows)
        trades = pd.DataFrame(trade_rows)
        metrics = performance_summary(equity_curve)
        return BacktestResult(equity_curve=equity_curve, trades=trades, metrics=metrics)

    @staticmethod
    def _portfolio_value(
        cash: float,
        positions: dict[str, _Position],
        bars: dict[str, pd.DataFrame],
        date: pd.Timestamp,
        *,
        field: str,
    ) -> float:
        value = float(cash)
        for symbol, pos in positions.items():
            if pos.shares <= 0:
                continue
            frame = bars[symbol]
            history = frame.loc[frame.index <= date]
            if history.empty:
                continue
            if date in frame.index and pd.notna(frame.loc[date].get(field)):
                price = float(frame.loc[date][field])
            else:
                price = float(history.iloc[-1]["close"])
            value += pos.shares * price
        return float(value)
