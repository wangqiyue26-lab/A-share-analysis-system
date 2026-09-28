from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schema import validate_bars


class ParquetBarCache:
    """Rebuildable Parquet cache for normalized daily bars."""

    def __init__(self, root: str | Path = "data/cache"):
        self.root = Path(root)

    def path_for(self, symbol: str) -> Path:
        return self.root / "daily" / f"{str(symbol).zfill(6)}.parquet"

    def exists(self, symbol: str) -> bool:
        return self.path_for(symbol).exists()

    def save(self, symbol: str, frame: pd.DataFrame) -> Path:
        normalized = validate_bars(frame)
        expected = str(symbol).zfill(6)
        if normalized.iloc[0]["symbol"] != expected:
            raise ValueError(f"Cache symbol mismatch: expected {expected}")
        path = self.path_for(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_parquet(path, index=False)
        return path

    def load(self, symbol: str) -> pd.DataFrame:
        path = self.path_for(symbol)
        if not path.exists():
            raise FileNotFoundError(path)
        return validate_bars(pd.read_parquet(path))

    def upsert(self, symbol: str, frame: pd.DataFrame) -> Path:
        incoming = validate_bars(frame)
        if self.exists(symbol):
            current = self.load(symbol)
            combined = pd.concat([current, incoming], ignore_index=True)
            combined["trade_date"] = pd.to_datetime(combined["trade_date"])
            combined = combined.sort_values("trade_date").drop_duplicates(
                "trade_date", keep="last"
            )
        else:
            combined = incoming
        return self.save(symbol, combined)

    def latest_trade_date(self, symbol: str) -> pd.Timestamp:
        bars = self.load(symbol)
        return pd.Timestamp(bars.iloc[-1]["trade_date"]).normalize()
