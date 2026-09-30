from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .data.financials import AkshareSinaFinancialProvider
from .data.pit_cache import ParquetPointInTimeCache
from .factors.fundamental import FUNDAMENTAL_FACTOR_COLUMNS, compute_fundamental_factors


@dataclass(frozen=True)
class FundamentalSnapshotResult:
    requested_count: int
    success_count: int
    failure_count: int
    average_coverage: float | None
    fundamentals_file: str
    summary_file: str

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_count": self.requested_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "average_coverage": self.average_coverage,
            "fundamentals_file": self.fundamentals_file,
            "summary_file": self.summary_file,
        }


def _normalize_cutoff(as_of: str | pd.Timestamp) -> pd.Timestamp:
    cutoff = pd.Timestamp(as_of)
    return cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")


def _load_or_refresh(
    symbol: str,
    *,
    cache: ParquetPointInTimeCache,
    refresh_days: int,
    stale_days: int,
    now: pd.Timestamp,
    provider_factory: Callable[[], AkshareSinaFinancialProvider],
) -> tuple[pd.DataFrame | None, str | None, str | None]:
    if cache.is_fresh(symbol, max_age_days=refresh_days, now=now):
        return cache.load(symbol), "fresh_cache", None

    provider = provider_factory()
    try:
        incoming = provider.get_all(symbol)
        cache.upsert(symbol, incoming, refreshed_at=now)
        return cache.load(symbol), provider.name, None
    except Exception as exc:  # noqa: BLE001 - recent cache is an intentional provider-boundary fallback
        try:
            refreshed = cache.last_refreshed_at(symbol)
            cached = cache.load(symbol)
        except (FileNotFoundError, KeyError, ValueError, TypeError):
            return None, None, f"{type(exc).__name__}: {exc}"
        if refreshed < now - pd.Timedelta(days=stale_days):
            return None, None, f"stale_financial_cache_after_{type(exc).__name__}: {exc}"
        return cached, "recent_cache", f"refresh_failed_using_recent_cache: {type(exc).__name__}: {exc}"


def build_fundamental_snapshot(
    symbols: Iterable[str],
    *,
    as_of: str | pd.Timestamp,
    cache_root: str | Path = "data/cache/financials",
    output_dir: str | Path = "reports/production/selection",
    workers: int = 2,
    refresh_days: int = 7,
    stale_days: int = 30,
    provider_factory: Callable[[], AkshareSinaFinancialProvider] = AkshareSinaFinancialProvider,
) -> FundamentalSnapshotResult:
    """Build a best-effort PIT fundamental snapshot without changing the market score."""
    if workers <= 0 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    if refresh_days < 0 or stale_days < refresh_days:
        raise ValueError("Require 0 <= refresh_days <= stale_days")

    requested = sorted({str(symbol).zfill(6) for symbol in symbols})
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fundamentals_file = output / "fundamentals.csv"
    summary_file = output / "fundamental_summary.json"
    cutoff = _normalize_cutoff(as_of)
    now = pd.Timestamp.now(tz="UTC")
    cache = ParquetPointInTimeCache(cache_root)

    rows: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    sources: dict[str, str] = {}

    def fetch(symbol: str) -> tuple[str, pd.DataFrame | None, str | None, str | None]:
        facts, source, warning = _load_or_refresh(
            symbol,
            cache=cache,
            refresh_days=refresh_days,
            stale_days=stale_days,
            now=now,
            provider_factory=provider_factory,
        )
        return symbol, facts, source, warning

    if requested:
        with ThreadPoolExecutor(max_workers=min(workers, len(requested))) as pool:
            futures = {pool.submit(fetch, symbol): symbol for symbol in requested}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    returned_symbol, facts, source, warning = future.result()
                except Exception as exc:  # noqa: BLE001 - isolate one symbol from supplemental research
                    failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if facts is None:
                    failures.append({"symbol": returned_symbol, "error": warning or "financial_fetch_failed"})
                    continue
                factor_row = compute_fundamental_factors(facts, cutoff)
                if factor_row.empty:
                    failures.append({"symbol": returned_symbol, "error": "no_visible_fundamental_factors"})
                    continue
                factor_row = factor_row.copy()
                factor_row["fundamental_source"] = source or "unknown"
                factor_row["fundamental_warning"] = warning
                rows.append(factor_row)
                sources[returned_symbol] = source or "unknown"
                if warning:
                    warnings.append({"symbol": returned_symbol, "warning": warning})

    columns = [
        "symbol",
        "fundamental_as_of",
        "fundamental_period_end",
        *FUNDAMENTAL_FACTOR_COLUMNS,
        "fundamental_coverage",
        "fundamental_source",
        "fundamental_warning",
    ]
    if rows:
        fundamentals = pd.concat(rows, ignore_index=True).loc[:, columns].sort_values("symbol")
    else:
        fundamentals = pd.DataFrame(columns=columns)
    fundamentals.to_csv(fundamentals_file, index=False)

    average_coverage = None
    if not fundamentals.empty:
        average_coverage = float(pd.to_numeric(fundamentals["fundamental_coverage"]).mean())
    summary = {
        "as_of": cutoff.isoformat(),
        "requested_count": len(requested),
        "success_count": len(fundamentals),
        "failure_count": len(failures),
        "average_coverage": average_coverage,
        "source_counts": dict(Counter(sources.values())),
        "warning_count": len(warnings),
        "warnings": warnings,
        "failures": failures,
        "scoring_integration": False,
        "note": "PIT fundamentals are supplemental and do not alter the market-factor composite score.",
    }
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return FundamentalSnapshotResult(
        requested_count=len(requested),
        success_count=len(fundamentals),
        failure_count=len(failures),
        average_coverage=average_coverage,
        fundamentals_file=str(fundamentals_file),
        summary_file=str(summary_file),
    )
