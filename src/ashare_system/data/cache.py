from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schema import validate_bars


class ParquetBarCache:
    """Small rebuildable Parquet cache for normalized daily bars."""

    def __init__(self, root: str | Path = "data/cache"):
        self.root = Path(root)

    def path_for(self, symbol: str) -> Path:
        return self.root / "daily" / f"{str(symbol).zfill(6)}.parquet"

    def save(self, symbol: str, frame: pd.DataFrame) -> Path:
        normalized = validate_bars(frame)
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
        """Merge new normalized bars into a symbol cache, keeping the latest duplicate date."""
        incoming = validate_bars(frame)
        try:
            current = self.load(symbol)
        except FileNotFoundError:
            return self.save(symbol, incoming)
        combined = pd.concat([current, incoming], ignore_index=True)
        combined["trade_date"] = pd.to_datetime(combined["trade_date"])
        combined = combined.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        return self.save(symbol, combined)
