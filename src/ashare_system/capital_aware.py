from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import load_yaml


@dataclass(frozen=True)
class CapitalAwarePortfolioConfig:
    initial_cash: float
    max_stock_positions: int
    stock_weight_per_position: float
    max_total_stock_weight: float
    min_cash_weight: float
    entry_rank_max: int
    keep_rank_max: int
    rebalance_every_trading_days: int
    minimum_holding_trading_days: int
    affordability_buffer_rate: float
    etf_enabled: bool
    etf_target_weight: float
    etf_max_weight_if_stock_slots_unused: float

    @property
    def minimum_stock_ticket_cny(self) -> float:
        return self.initial_cash * self.stock_weight_per_position


def load_capital_aware_config(
    path: str | Path = "config/portfolio_v2.yml",
) -> CapitalAwarePortfolioConfig:
    payload = load_yaml(path)
    account = payload["account"]
    portfolio = payload["portfolio"]
    etf = payload["etf_sleeve"]
    config = CapitalAwarePortfolioConfig(
        initial_cash=float(account["initial_cash"]),
        max_stock_positions=int(portfolio["max_stock_positions"]),
        stock_weight_per_position=float(portfolio["stock_weight_per_position"]),
        max_total_stock_weight=float(portfolio["max_total_stock_weight"]),
        min_cash_weight=float(portfolio["min_cash_weight"]),
        entry_rank_max=int(portfolio["entry_rank_max"]),
        keep_rank_max=int(portfolio["keep_rank_max"]),
        rebalance_every_trading_days=int(portfolio["rebalance_every_trading_days"]),
        minimum_holding_trading_days=int(portfolio["minimum_holding_trading_days"]),
        affordability_buffer_rate=float(portfolio.get("affordability_buffer_rate", 0.0)),
        etf_enabled=bool(etf.get("enabled", False)),
        etf_target_weight=float(etf.get("target_weight", 0.0)),
        etf_max_weight_if_stock_slots_unused=float(
            etf.get("max_weight_if_stock_slots_unused", 0.0)
        ),
    )
    if config.initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if config.max_stock_positions <= 0:
        raise ValueError("max_stock_positions must be positive")
    if not 0 < config.stock_weight_per_position <= 1:
        raise ValueError("stock_weight_per_position must be in (0, 1]")
    if config.stock_weight_per_position * config.max_stock_positions > (
        config.max_total_stock_weight + 1e-12
    ):
        raise ValueError("configured stock slots exceed max_total_stock_weight")
    if not 0 <= config.min_cash_weight < 1:
        raise ValueError("min_cash_weight must be in [0, 1)")
    if config.keep_rank_max < config.entry_rank_max:
        raise ValueError("keep_rank_max must be >= entry_rank_max")
    if config.rebalance_every_trading_days <= 0:
        raise ValueError("rebalance_every_trading_days must be positive")
    return config


def _validated_ranking(frame: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    required = {"symbol", "rank", "board", "is_st", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"ranking for {date.date()} missing columns: {missing}")
    result = frame.copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["rank"] = pd.to_numeric(result["rank"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result[result["rank"].notna() & result["close"].gt(0)].copy()
    return result.sort_values(["rank", "symbol"]).reset_index(drop=True)


def capital_aware_stock_signals(
    rankings_by_date: dict[pd.Timestamp | str, pd.DataFrame],
    *,
    config: CapitalAwarePortfolioConfig,
) -> pd.DataFrame:
    """Turn daily rankings into a low-turnover, affordability-aware stock sleeve.

    The selector only changes targets every configured rebalance interval.
    Existing holdings survive weak rank changes while inside the minimum
    holding window, and afterwards remain eligible inside the wider keep-rank
    buffer. New positions must fit one board lot inside the configured target
    ticket for the CNY 20k account.
    """
    if not rankings_by_date:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "target_weight",
                "board",
                "is_st",
                "instrument_type",
                "source_rank",
            ]
        )

    dated = sorted(
        ((pd.Timestamp(raw).normalize(), frame) for raw, frame in rankings_by_date.items()),
        key=lambda item: item[0],
    )
    calendar_index = {date: index for index, (date, _) in enumerate(dated)}
    held: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []

    for date, raw_frame in dated:
        index = calendar_index[date]
        if index % config.rebalance_every_trading_days != 0:
            continue
        frame = _validated_ranking(raw_frame, date)
        by_symbol = {row.symbol: row for row in frame.itertuples(index=False)}
        selected: list[str] = []

        for symbol, meta in sorted(held.items()):
            row = by_symbol.get(symbol)
            age = index - int(meta["entry_index"])
            rank = int(row.rank) if row is not None else None
            if age < config.minimum_holding_trading_days or (
                rank is not None and rank <= config.keep_rank_max
            ):
                selected.append(symbol)

        selected = selected[: config.max_stock_positions]
        target_ticket = config.initial_cash * config.stock_weight_per_position
        for row in frame.itertuples(index=False):
            if len(selected) >= config.max_stock_positions:
                break
            symbol = str(row.symbol)
            if symbol in selected or int(row.rank) > config.entry_rank_max:
                continue
            one_lot_cost = (
                float(row.close)
                * 100.0
                * (1.0 + config.affordability_buffer_rate)
            )
            if one_lot_cost > target_ticket:
                continue
            selected.append(symbol)

        previous = set(held)
        selected_set = set(selected)
        for symbol in sorted(previous - selected_set):
            meta = held[symbol]
            rows.append(
                {
                    "trade_date": date,
                    "symbol": symbol,
                    "target_weight": 0.0,
                    "board": str(meta["board"]),
                    "is_st": bool(meta["is_st"]),
                    "instrument_type": "stock",
                    "source_rank": pd.NA,
                }
            )

        next_held: dict[str, dict[str, object]] = {}
        for symbol in selected:
            row = by_symbol.get(symbol)
            old = held.get(symbol)
            if old is not None:
                board = str(old["board"])
                is_st = bool(old["is_st"])
                entry_index = int(old["entry_index"])
            elif row is not None:
                board = str(row.board)
                is_st = bool(row.is_st)
                entry_index = index
            else:
                continue
            rank = int(row.rank) if row is not None else pd.NA
            rows.append(
                {
                    "trade_date": date,
                    "symbol": symbol,
                    "target_weight": config.stock_weight_per_position,
                    "board": board,
                    "is_st": is_st,
                    "instrument_type": "stock",
                    "source_rank": rank,
                }
            )
            next_held[symbol] = {
                "board": board,
                "is_st": is_st,
                "entry_index": entry_index,
            }
        held = next_held

    if not rows:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "target_weight",
                "board",
                "is_st",
                "instrument_type",
                "source_rank",
            ]
        )
    result = pd.DataFrame(rows)
    active = result[result["target_weight"] > 0].groupby("trade_date")["target_weight"].sum()
    if active.gt(config.max_total_stock_weight + 1e-12).any():
        raise ValueError("capital-aware stock sleeve exceeds configured maximum weight")
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _etf_snapshot(frame: pd.DataFrame, as_of: pd.Timestamp) -> tuple[float, float] | None:
    history = frame[pd.to_datetime(frame["trade_date"]) <= as_of].copy()
    history = history.sort_values("trade_date")
    if len(history) < 61:
        return None
    close = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(close) < 61:
        return None
    momentum_20 = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    ma60 = float(close.tail(60).mean())
    latest = float(close.iloc[-1])
    if momentum_20 <= 0 or latest <= ma60:
        return None
    return momentum_20, latest


def etf_rotation_signals(
    etf_bars_by_symbol: dict[str, pd.DataFrame],
    *,
    rebalance_dates: list[pd.Timestamp],
    stock_signals: pd.DataFrame,
    config: CapitalAwarePortfolioConfig,
) -> pd.DataFrame:
    """Build a simple trend-qualified ETF sleeve without mixing ETF and stock scores."""
    columns = [
        "trade_date",
        "symbol",
        "target_weight",
        "board",
        "is_st",
        "instrument_type",
        "source_rank",
    ]
    if not config.etf_enabled or not etf_bars_by_symbol or not rebalance_dates:
        return pd.DataFrame(columns=columns)

    active_stock = (
        stock_signals[stock_signals["target_weight"] > 0]
        .groupby("trade_date")["target_weight"]
        .sum()
        .to_dict()
    )
    previous_etf: str | None = None
    rows: list[dict[str, object]] = []

    for raw_date in sorted(pd.Timestamp(value).normalize() for value in rebalance_dates):
        candidates: list[tuple[str, float]] = []
        for symbol, frame in etf_bars_by_symbol.items():
            snapshot = _etf_snapshot(frame, raw_date)
            if snapshot is None:
                continue
            momentum_20, _ = snapshot
            candidates.append((str(symbol).zfill(6), momentum_20))
        candidates.sort(key=lambda item: (-item[1], item[0]))
        chosen = candidates[0][0] if candidates else None

        stock_weight = float(active_stock.get(raw_date, 0.0))
        unused_stock = max(0.0, config.max_total_stock_weight - stock_weight)
        desired = min(
            config.etf_max_weight_if_stock_slots_unused,
            config.etf_target_weight + unused_stock,
            max(0.0, 1.0 - config.min_cash_weight - stock_weight),
        )
        if chosen is None:
            desired = 0.0

        if previous_etf is not None and previous_etf != chosen:
            rows.append(
                {
                    "trade_date": raw_date,
                    "symbol": previous_etf,
                    "target_weight": 0.0,
                    "board": "ETF",
                    "is_st": False,
                    "instrument_type": "etf",
                    "source_rank": pd.NA,
                }
            )
        if chosen is not None and desired > 0:
            rows.append(
                {
                    "trade_date": raw_date,
                    "symbol": chosen,
                    "target_weight": desired,
                    "board": "ETF",
                    "is_st": False,
                    "instrument_type": "etf",
                    "source_rank": 1,
                }
            )
            previous_etf = chosen
        else:
            previous_etf = None

    return pd.DataFrame(rows, columns=columns).sort_values(
        ["trade_date", "symbol"]
    ).reset_index(drop=True)


def combine_portfolio_signals(stock_signals: pd.DataFrame, etf_signals: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (stock_signals, etf_signals) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(
            columns=["trade_date", "symbol", "target_weight", "board", "is_st", "instrument_type"]
        )
    combined = pd.concat(frames, ignore_index=True)
    totals = combined.groupby("trade_date")["target_weight"].sum()
    if totals.gt(1.0000001).any():
        raise ValueError("combined portfolio targets exceed 100%")
    return combined.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
