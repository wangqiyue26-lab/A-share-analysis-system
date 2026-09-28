from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ashare_system.data.cache import ParquetBarCache
from ashare_system.data.router import DataRouter
from ashare_system.factors.engine import FactorEngine

RouterFactory = Callable[[], DataRouter]


@dataclass(frozen=True)
class DailyPipelineResult:
    target_date: date
    ranking: pd.DataFrame
    factor_exclusions: pd.DataFrame
    updates: pd.DataFrame


class BatchBarUpdater:
    """Incrementally update per-symbol bar caches with bounded concurrency."""

    def __init__(
        self,
        router_factory: RouterFactory,
        cache: ParquetBarCache,
        *,
        max_workers: int = 6,
        bootstrap_calendar_days: int = 180,
        adjust: str = "qfq",
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if bootstrap_calendar_days < 90:
            raise ValueError("bootstrap_calendar_days must be at least 90")
        self.router_factory = router_factory
        self.cache = cache
        self.max_workers = max_workers
        self.bootstrap_calendar_days = bootstrap_calendar_days
        self.adjust = adjust

    def _update_one(self, symbol: str, target_date: date) -> dict[str, object]:
        code = str(symbol).zfill(6)
        target = pd.Timestamp(target_date).normalize()
        if self.cache.exists(code):
            latest = self.cache.latest_trade_date(code)
            if latest >= target:
                return {
                    "symbol": code,
                    "status": "cached",
                    "provider": None,
                    "rows_fetched": 0,
                    "latest_trade_date": latest.date().isoformat(),
                    "error": None,
                }
            start_date = (latest + pd.Timedelta(days=1)).date()
        else:
            start_date = target_date - timedelta(days=self.bootstrap_calendar_days)

        router = self.router_factory()
        try:
            bars = router.get_daily_bars(
                code,
                start_date.strftime("%Y%m%d"),
                target_date.strftime("%Y%m%d"),
                self.adjust,
            )
            self.cache.upsert(code, bars)
            latest = self.cache.latest_trade_date(code)
            status = "updated" if latest >= target else "stale"
            return {
                "symbol": code,
                "status": status,
                "provider": router.last_provider_name,
                "rows_fetched": len(bars),
                "latest_trade_date": latest.date().isoformat(),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - one symbol must not abort an entire batch
            latest_text = None
            if self.cache.exists(code):
                latest_text = self.cache.latest_trade_date(code).date().isoformat()
            return {
                "symbol": code,
                "status": "failed",
                "provider": router.last_provider_name,
                "rows_fetched": 0,
                "latest_trade_date": latest_text,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def update(self, symbols: Iterable[str], target_date: date) -> pd.DataFrame:
        codes = sorted({str(symbol).zfill(6) for symbol in symbols})
        if not codes:
            raise ValueError("At least one symbol is required")

        rows: list[dict[str, object]] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._update_one, symbol, target_date): symbol for symbol in codes
            }
            for future in as_completed(futures):
                rows.append(future.result())
        return pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)


class DailyResearchPipeline:
    """Update bars and rank only symbols whose cache is current for the target trading day."""

    def __init__(self, updater: BatchBarUpdater, factor_engine: FactorEngine) -> None:
        self.updater = updater
        self.factor_engine = factor_engine

    def run(self, symbols: Iterable[str], target_date: date) -> DailyPipelineResult:
        updates = self.updater.update(symbols, target_date)
        usable = set(updates.loc[updates["status"].isin(["cached", "updated"]), "symbol"])

        frames: dict[str, pd.DataFrame] = {}
        target = pd.Timestamp(target_date).normalize()
        for symbol in sorted(usable):
            bars = self.updater.cache.load(symbol)
            bars = bars[pd.to_datetime(bars["trade_date"]).dt.normalize() <= target].copy()
            if bars.empty:
                continue
            if pd.Timestamp(bars.iloc[-1]["trade_date"]).normalize() != target:
                continue
            frames[symbol] = bars

        factor_result = self.factor_engine.run(frames)
        return DailyPipelineResult(
            target_date=target_date,
            ranking=factor_result.ranking,
            factor_exclusions=factor_result.exclusions,
            updates=updates,
        )


def write_pipeline_result(result: DailyPipelineResult, output_dir: str | Path) -> dict[str, object]:
    """Persist transparent machine-readable research outputs for later Pages rendering."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    result.ranking.to_csv(directory / "ranking.csv", index=False, encoding="utf-8-sig")
    result.factor_exclusions.to_csv(
        directory / "factor_exclusions.csv", index=False, encoding="utf-8-sig"
    )
    result.updates.to_csv(directory / "updates.csv", index=False, encoding="utf-8-sig")

    counts = Counter(result.updates["status"].astype(str).tolist())
    summary: dict[str, object] = {
        "target_date": result.target_date.isoformat(),
        "requested_symbols": int(len(result.updates)),
        "ranked_symbols": int(len(result.ranking)),
        "factor_exclusions": int(len(result.factor_exclusions)),
        "update_status": dict(sorted(counts.items())),
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
