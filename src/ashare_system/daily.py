from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.akshare_provider import AkshareEastmoneyProvider
from .data.akshare_sina_provider import AkshareSinaProvider
from .data.cache import ParquetBarCache
from .data.router import DataRouter
from .data.schema import validate_bars
from .data.spot import AkshareSpotProvider, prefilter_spot_snapshot
from .factors import FactorEngine, FactorRunResult, load_factor_config
from .reporting import write_selection_outputs

CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DailyPipelineConfig:
    prefilter_count: int = 60
    top_n: int = 20
    min_spot_amount: float = 50_000_000.0
    min_price: float = 1.0
    history_calendar_days: int = 280
    min_history: int = 80
    min_average_amount_20: float = 20_000_000.0
    max_workers: int = 4
    max_stale_days: int = 7
    adjust: str = "hfq"


@dataclass(frozen=True)
class HistoryFetchResult:
    symbol: str
    bars: pd.DataFrame
    provider: str
    lag_days: int
    refresh_error: str | None = None


@dataclass(frozen=True)
class DailySelectionResult:
    factor_result: FactorRunResult
    paths: dict[str, Path]
    metadata: dict[str, object]


def _providers(prefer_sina: bool) -> list[object]:
    if prefer_sina:
        return [AkshareSinaProvider(), AkshareEastmoneyProvider()]
    return [AkshareEastmoneyProvider(), AkshareSinaProvider()]


def _merge_bars(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return validate_bars(fresh)
    combined = pd.concat([existing, fresh], ignore_index=True)
    combined = combined.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return validate_bars(combined)


def _load_history(
    *,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_root: str | Path,
    adjust: str,
    prefer_sina: bool,
    max_stale_days: int,
) -> HistoryFetchResult:
    adjustment_key = adjust or "raw"
    cache = ParquetBarCache(Path(cache_root) / adjustment_key)
    symbol = str(symbol).zfill(6)
    existing = pd.DataFrame()
    try:
        existing = cache.load(symbol)
    except FileNotFoundError:
        pass

    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if not existing.empty:
        dates = pd.to_datetime(existing["trade_date"], errors="coerce")
        existing = existing[(dates >= start) & (dates <= end)].copy()

    if not existing.empty:
        last_cached = pd.Timestamp(existing["trade_date"].max()).normalize()
        if last_cached >= end:
            return HistoryFetchResult(symbol, validate_bars(existing), "cache", 0)
        request_start = last_cached + pd.Timedelta(days=1)
    else:
        last_cached = None
        request_start = start

    router = DataRouter(_providers(prefer_sina))
    try:
        fresh = router.get_daily_bars(
            symbol,
            request_start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            adjust,
        )
        merged = _merge_bars(existing, fresh)
        cache.save(symbol, merged)
        lag_days = max((end - pd.Timestamp(merged["trade_date"].max()).normalize()).days, 0)
        return HistoryFetchResult(
            symbol=symbol,
            bars=merged,
            provider=router.last_provider_name or "unknown",
            lag_days=lag_days,
        )
    except Exception as exc:
        if existing.empty or last_cached is None:
            raise
        lag_days = max((end - last_cached).days, 0)
        if lag_days > max_stale_days:
            raise RuntimeError(
                f"Cached history for {symbol} is {lag_days} calendar days behind "
                f"the requested date and refresh failed: {exc}"
            ) from exc
        return HistoryFetchResult(
            symbol=symbol,
            bars=validate_bars(existing),
            provider="cache_refresh_fallback",
            lag_days=lag_days,
            refresh_error=f"{type(exc).__name__}: {exc}",
        )


def _enrich_factor_result(
    factor_result: FactorRunResult,
    candidates: pd.DataFrame,
) -> FactorRunResult:
    wanted = [
        "symbol",
        "name",
        "price",
        "amount",
        "turnover_pct",
        "total_market_cap",
        "float_market_cap",
    ]
    available = [column for column in wanted if column in candidates.columns]
    metadata = candidates.loc[:, available].copy()
    metadata["symbol"] = metadata["symbol"].astype(str).str.zfill(6)
    ranking = factor_result.ranking.merge(metadata, on="symbol", how="left")
    exclusion_meta = [column for column in ("symbol", "name") if column in metadata.columns]
    exclusions = factor_result.exclusions.merge(metadata.loc[:, exclusion_meta], on="symbol", how="left")
    return FactorRunResult(ranking=ranking, exclusions=exclusions)


def run_daily_selection(
    *,
    output_dir: str | Path = "reports/output/daily",
    cache_root: str | Path = "data/cache",
    factor_config: str | Path = "config/factors.yml",
    config: DailyPipelineConfig | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> DailySelectionResult:
    cfg = config or DailyPipelineConfig()
    if cfg.prefilter_count < cfg.top_n:
        raise ValueError("prefilter_count must be >= top_n")
    if cfg.max_workers <= 0:
        raise ValueError("max_workers must be positive")

    if as_of is not None:
        end = pd.Timestamp(as_of).normalize()
    else:
        end = pd.Timestamp.now(tz=CHINA_TZ).tz_localize(None).normalize()
    start = end - pd.Timedelta(days=cfg.history_calendar_days)

    spot = AkshareSpotProvider().get_snapshot()
    candidates = prefilter_spot_snapshot(
        spot,
        top_n=cfg.prefilter_count,
        min_amount=cfg.min_spot_amount,
        min_price=cfg.min_price,
        exclude_st=True,
    )
    if len(candidates) < cfg.top_n:
        raise RuntimeError(
            f"Only {len(candidates)} liquid candidates available, fewer than requested top_n={cfg.top_n}"
        )

    prefer_sina = spot.attrs.get("provider") == "sina"
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    failures: list[dict[str, str]] = []
    provider_counts: dict[str, int] = {}
    lagging_symbols: list[dict[str, object]] = []
    refresh_failure_count = 0

    with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
        futures = {
            executor.submit(
                _load_history,
                symbol=str(row.symbol),
                start=start,
                end=end,
                cache_root=cache_root,
                adjust=cfg.adjust,
                prefer_sina=prefer_sina,
                max_stale_days=cfg.max_stale_days,
            ): str(row.symbol).zfill(6)
            for row in candidates.itertuples(index=False)
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                item = future.result()
                bars_by_symbol[symbol] = item.bars
                provider_counts[item.provider] = provider_counts.get(item.provider, 0) + 1
                if item.refresh_error:
                    refresh_failure_count += 1
                if item.lag_days > 0 or item.refresh_error:
                    lagging_symbols.append(
                        {
                            "symbol": symbol,
                            "lag_days": item.lag_days,
                            "refresh_error": item.refresh_error,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - one symbol must not abort the market run
                failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})

    if len(bars_by_symbol) < cfg.top_n:
        raise RuntimeError(
            f"Only {len(bars_by_symbol)} candidate histories fetched; need at least {cfg.top_n}"
        )

    engine = FactorEngine(
        load_factor_config(factor_config),
        min_history=cfg.min_history,
        min_average_amount_20=cfg.min_average_amount_20,
    )
    factor_result = _enrich_factor_result(engine.run(bars_by_symbol), candidates)
    paths = write_selection_outputs(
        factor_result,
        output_dir,
        top_n=cfg.top_n,
        fetch_failures=failures,
    )

    output = Path(output_dir)
    candidates_path = output / "candidate_universe.csv"
    candidates.to_csv(candidates_path, index=False)
    paths["candidate_universe_csv"] = candidates_path

    ranked_count = int(factor_result.ranking["rank"].notna().sum())
    data_dates = pd.to_datetime(factor_result.ranking.get("as_of"), errors="coerce").dropna()
    metadata: dict[str, object] = {
        "pipeline": "full_market_liquidity_prefilter",
        "as_of_requested": end.date().isoformat(),
        "data_as_of_min": data_dates.min().date().isoformat() if not data_dates.empty else None,
        "data_as_of_max": data_dates.max().date().isoformat() if not data_dates.empty else None,
        "market_snapshot_provider": spot.attrs.get("provider"),
        "market_snapshot_rows": len(spot),
        "prefilter_count": len(candidates),
        "history_fetched_count": len(bars_by_symbol),
        "history_failure_count": len(failures),
        "history_provider_counts": provider_counts,
        "lagging_symbol_count": len(lagging_symbols),
        "refresh_failure_count": refresh_failure_count,
        "lagging_symbols": lagging_symbols,
        "ranked_count": ranked_count,
        "selected_count": min(ranked_count, cfg.top_n),
        "adjustment": cfg.adjust,
        "history_start": start.date().isoformat(),
    }
    summary_path = paths["summary_json"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update(metadata)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return DailySelectionResult(factor_result=factor_result, paths=paths, metadata=metadata)
