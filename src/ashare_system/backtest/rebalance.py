from __future__ import annotations

import pandas as pd

REBALANCE_COLUMNS = ("trade_date", "symbol", "target_weight", "board", "is_st")


def equal_weight_rebalance_signals(
    rankings_by_date: dict[pd.Timestamp | str, pd.DataFrame],
    *,
    top_n: int = 20,
    max_weight: float | None = None,
) -> pd.DataFrame:
    """Convert dated ranking tables into complete target-weight rebalance signals.

    Ranking tables must include `symbol`, `rank`, `board`, and `is_st`. Dropped
    holdings receive an explicit zero target so the execution engine can close
    them instead of silently carrying stale positions.
    """
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if max_weight is not None and not 0 < max_weight <= 1:
        raise ValueError("max_weight must be in (0, 1]")

    rows: list[dict[str, object]] = []
    previous_meta: dict[str, tuple[str, bool]] = {}

    for raw_date, ranking in sorted(rankings_by_date.items(), key=lambda item: pd.Timestamp(item[0])):
        date = pd.Timestamp(raw_date).normalize()
        required = {"symbol", "rank", "board", "is_st"}
        missing = sorted(required - set(ranking.columns))
        if missing:
            raise ValueError(f"ranking for {date.date()} missing columns: {missing}")

        frame = ranking.copy()
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
        frame["rank"] = pd.to_numeric(frame["rank"], errors="coerce")
        frame = frame[frame["rank"].notna()].sort_values(["rank", "symbol"]).head(top_n)
        if frame.empty:
            selected_meta: dict[str, tuple[str, bool]] = {}
        else:
            selected_meta = {
                row.symbol: (str(row.board), bool(row.is_st))
                for row in frame.itertuples(index=False)
            }

        for symbol in sorted(set(previous_meta) - set(selected_meta)):
            board, is_st = previous_meta[symbol]
            rows.append(
                {
                    "trade_date": date,
                    "symbol": symbol,
                    "target_weight": 0.0,
                    "board": board,
                    "is_st": is_st,
                }
            )

        if selected_meta:
            weight = 1.0 / len(selected_meta)
            if max_weight is not None:
                weight = min(weight, max_weight)
            for symbol in frame["symbol"]:
                board, is_st = selected_meta[symbol]
                rows.append(
                    {
                        "trade_date": date,
                        "symbol": symbol,
                        "target_weight": weight,
                        "board": board,
                        "is_st": is_st,
                    }
                )

        previous_meta = selected_meta

    if not rows:
        return pd.DataFrame(columns=REBALANCE_COLUMNS)

    result = pd.DataFrame(rows, columns=REBALANCE_COLUMNS)
    active_weight = result.groupby("trade_date")["target_weight"].sum()
    if active_weight.gt(1.0000001).any():
        raise ValueError("generated target weights exceed 100%")
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
