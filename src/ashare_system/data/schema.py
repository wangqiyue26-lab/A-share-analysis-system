from __future__ import annotations

import pandas as pd

BAR_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "amplitude_pct",
    "change_pct",
    "change",
    "turnover_pct",
)

NUMERIC_BAR_COLUMNS = tuple(column for column in BAR_COLUMNS if column not in {"symbol", "trade_date"})


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize the canonical daily-bar dataframe."""
    missing = [column for column in BAR_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing canonical bar columns: {missing}")

    result = frame.loc[:, BAR_COLUMNS].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise")
    for column in NUMERIC_BAR_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    if result["trade_date"].duplicated().any():
        raise ValueError("Duplicate trade_date values detected for a single symbol")
    if result["close"].isna().any():
        raise ValueError("close contains non-numeric or missing values")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")

    return result.sort_values("trade_date").reset_index(drop=True)
