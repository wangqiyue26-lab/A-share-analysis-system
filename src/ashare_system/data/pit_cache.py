from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .point_in_time import PIT_COLUMNS, validate_point_in_time_metrics

_PIT_KEY = ["symbol", "metric", "period_end", "available_at", "source"]


class ParquetPointInTimeCache:
    """Per-symbol cache that preserves point-in-time financial revisions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _data_path(self, symbol: str) -> Path:
        return self.root / f"{str(symbol).zfill(6)}.parquet"

    def _meta_path(self, symbol: str) -> Path:
        return self.root / f"{str(symbol).zfill(6)}.json"

    def load(self, symbol: str) -> pd.DataFrame:
        path = self._data_path(symbol)
        if not path.exists():
            raise FileNotFoundError(f"No PIT cache for {str(symbol).zfill(6)}")
        return validate_point_in_time_metrics(pd.read_parquet(path))

    def metadata(self, symbol: str) -> dict[str, object]:
        path = self._meta_path(symbol)
        if not path.exists():
            raise FileNotFoundError(f"No PIT cache metadata for {str(symbol).zfill(6)}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("PIT cache metadata must be a JSON object")
        return payload

    def last_refreshed_at(self, symbol: str) -> pd.Timestamp:
        payload = self.metadata(symbol)
        refreshed = pd.Timestamp(payload["refreshed_at"])
        return refreshed.tz_localize("UTC") if refreshed.tzinfo is None else refreshed.tz_convert("UTC")

    def is_fresh(
        self,
        symbol: str,
        *,
        max_age_days: int = 7,
        now: pd.Timestamp | None = None,
    ) -> bool:
        if max_age_days < 0:
            raise ValueError("max_age_days cannot be negative")
        current = now or pd.Timestamp.now(tz="UTC")
        current = current.tz_localize("UTC") if current.tzinfo is None else current.tz_convert("UTC")
        try:
            refreshed = self.last_refreshed_at(symbol)
            self.load(symbol)
        except (FileNotFoundError, KeyError, ValueError, TypeError):
            return False
        return refreshed >= current - pd.Timedelta(days=max_age_days)

    def upsert(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        refreshed_at: pd.Timestamp | None = None,
    ) -> Path:
        code = str(symbol).zfill(6)
        incoming = validate_point_in_time_metrics(frame)
        if incoming.empty:
            raise ValueError("Cannot cache an empty PIT frame")
        if set(incoming["symbol"]) != {code}:
            raise ValueError(f"PIT cache frame must contain only symbol {code}")

        try:
            existing = self.load(code)
        except FileNotFoundError:
            existing = pd.DataFrame(columns=PIT_COLUMNS)

        combined = pd.concat([existing, incoming], ignore_index=True)
        if not combined.empty:
            conflicts = combined.groupby(_PIT_KEY, dropna=False)["value"].nunique(dropna=False)
            if (conflicts > 1).any():
                raise ValueError("Conflicting PIT values share the same availability identity")
            combined = combined.drop_duplicates(_PIT_KEY, keep="last")
        combined = validate_point_in_time_metrics(combined)

        self.root.mkdir(parents=True, exist_ok=True)
        path = self._data_path(code)
        temp = path.with_suffix(".parquet.tmp")
        combined.to_parquet(temp, index=False)
        temp.replace(path)

        stamp = refreshed_at or pd.Timestamp.now(tz="UTC")
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        metadata = {
            "symbol": code,
            "refreshed_at": stamp.isoformat(),
            "rows": len(combined),
            "metric_count": int(combined["metric"].nunique()),
        }
        meta_path = self._meta_path(code)
        meta_temp = meta_path.with_suffix(".json.tmp")
        meta_temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        meta_temp.replace(meta_path)
        return path
