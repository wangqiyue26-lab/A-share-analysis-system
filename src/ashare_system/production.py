from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.akshare_provider import AkshareEastmoneyProvider
from .data.akshare_sina_provider import AkshareSinaProvider
from .data.cache import ParquetBarCache
from .data.router import DataRouter
from .data.security_master import AkshareSecurityMasterProvider, SecurityMasterSnapshotStore
from .data.spot import AkshareEastmoneySpotProvider, select_liquid_candidates
from .factors import FactorEngine, FactorRunResult, load_factor_config
from .reporting import write_selection_outputs
from .site import build_dashboard

CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DailyProductionResult:
    market_as_of: str
    candidate_count: int
    history_success_count: int
    selected_count: int
    screen_source: str
    selection_dir: str
    site_dir: str
    manifest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "market_as_of": self.market_as_of,
            "candidate_count": self.candidate_count,
            "history_success_count": self.history_success_count,
            "selected_count": self.selected_count,
            "screen_source": self.screen_source,
            "selection_dir": self.selection_dir,
            "site_dir": self.site_dir,
            "manifest": self.manifest,
        }


def _router() -> DataRouter:
    return DataRouter([AkshareEastmoneyProvider(), AkshareSinaProvider()])


def _complete_security_master(cache_root: Path) -> tuple[pd.DataFrame, str, str | None]:
    provider = AkshareSecurityMasterProvider()
    store = SecurityMasterSnapshotStore(cache_root)
    warning: str | None = None
    try:
        result = provider.fetch_current()
        if result.complete:
            store.save(result.master)
            return result.master, provider.name, None
        warning = " | ".join(result.failures) or "incomplete current security master"
    except Exception as exc:  # noqa: BLE001 - cache fallback is intentional at provider boundary
        warning = f"{type(exc).__name__}: {exc}"

    try:
        cached = store.load_latest()
    except FileNotFoundError as exc:
        raise RuntimeError(f"No complete security master available; current fetch failed: {warning}") from exc

    observed = cached["observed_at"].max()
    if observed < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30):
        raise RuntimeError(
            "Latest complete security-master cache is older than 30 days; "
            f"current fetch failed: {warning}"
        )
    return cached, "security_master_cache", warning


def _fetch_one_history(
    symbol: str,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_root: Path,
    adjust: str,
) -> tuple[str, pd.DataFrame | None, str | None, str | None]:
    cache = ParquetBarCache(cache_root / f"bars_{adjust or 'raw'}")
    cached: pd.DataFrame | None = None
    try:
        cached = cache.load(symbol)
    except FileNotFoundError:
        pass

    fetch_start = start
    if cached is not None and not cached.empty:
        cached_min = pd.Timestamp(cached["trade_date"].min()).normalize()
        cached_max = pd.Timestamp(cached["trade_date"].max()).normalize()
        if cached_min <= start:
            fetch_start = max(start, cached_max + pd.Timedelta(days=1))

    provider_name = "cache"
    warning: str | None = None
    if cached is None or cached.empty or fetch_start <= end:
        router = _router()
        try:
            incoming = router.get_daily_bars(
                symbol,
                fetch_start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                adjust,
            )
            provider_name = router.last_provider_name or "unknown"
            cache.upsert(symbol, incoming)
            cached = cache.load(symbol)
        except Exception as exc:  # noqa: BLE001 - a recent cache may safely bridge holidays/outages
            if cached is None or cached.empty:
                return symbol, None, None, f"{type(exc).__name__}: {exc}"
            cached_max = pd.Timestamp(cached["trade_date"].max()).normalize()
            if cached_max < end - pd.Timedelta(days=14):
                return symbol, None, None, f"stale_cache_after_{type(exc).__name__}: {exc}"
            warning = f"refresh_failed_using_recent_cache: {type(exc).__name__}: {exc}"
            provider_name = "recent_cache"

    if cached is None or cached.empty:
        return symbol, None, None, "empty_history"
    dates = pd.to_datetime(cached["trade_date"])
    frame = cached[(dates >= start) & (dates <= end)].copy()
    if frame.empty:
        return symbol, None, None, "no_history_in_requested_window"
    return symbol, frame, provider_name, warning


def run_daily_production(
    *,
    output_root: str | Path = "reports/production",
    site_dir: str | Path = "site",
    cache_root: str | Path = "data/cache",
    factor_config: str | Path = "config/factors.yml",
    candidate_limit: int = 30,
    top_n: int = 10,
    history_days: int = 420,
    min_amount: float = 50_000_000.0,
    min_listing_days: int = 120,
    workers: int = 4,
    adjust: str = "qfq",
) -> DailyProductionResult:
    if candidate_limit < top_n:
        raise ValueError("candidate_limit must be >= top_n")
    if workers <= 0 or workers > 8:
        raise ValueError("workers must be between 1 and 8")

    output_root = Path(output_root)
    selection_dir = output_root / "selection"
    cache_root = Path(cache_root)
    site_dir = Path(site_dir)
    today = pd.Timestamp.now(tz=CHINA_TZ).tz_localize(None).normalize()
    start = today - pd.Timedelta(days=history_days)

    master, master_source, master_warning = _complete_security_master(cache_root)

    spot = None
    spot_warning: str | None = None
    try:
        spot = AkshareEastmoneySpotProvider().get_current()
    except Exception as exc:  # noqa: BLE001 - deterministic security-master fallback below
        spot_warning = f"{type(exc).__name__}: {exc}"

    candidates = select_liquid_candidates(
        master,
        spot,
        as_of=today,
        limit=candidate_limit,
        min_amount=min_amount,
        min_listing_days=min_listing_days,
    )
    if len(candidates) < top_n:
        raise RuntimeError(
            f"Candidate screen returned only {len(candidates)} names, below requested top_n={top_n}"
        )
    screen_source = str(candidates["screen_source"].iloc[0])
    output_root.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(output_root / "candidates.csv", index=False)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    providers: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    refresh_warnings: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as pool:
        futures = {
            pool.submit(
                _fetch_one_history,
                str(row.symbol).zfill(6),
                start=start,
                end=today,
                cache_root=cache_root,
                adjust=adjust,
            ): str(row.symbol).zfill(6)
            for row in candidates.itertuples(index=False)
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                returned_symbol, frame, provider, warning = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate one symbol from the production run
                failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if frame is None:
                failures.append({"symbol": returned_symbol, "error": warning or "history_fetch_failed"})
                continue
            bars_by_symbol[returned_symbol] = frame
            providers[returned_symbol] = provider or "unknown"
            if warning:
                refresh_warnings.append({"symbol": returned_symbol, "warning": warning})

    if len(bars_by_symbol) < top_n:
        raise RuntimeError(
            f"Only {len(bars_by_symbol)} candidate histories were usable, below top_n={top_n}"
        )

    latest_dates = {
        symbol: pd.Timestamp(frame["trade_date"].max()).normalize()
        for symbol, frame in bars_by_symbol.items()
    }
    market_as_of = max(latest_dates.values())
    for symbol, latest in list(latest_dates.items()):
        if latest != market_as_of:
            failures.append(
                {
                    "symbol": symbol,
                    "error": f"stale_latest_bar:{latest.date().isoformat()} != {market_as_of.date().isoformat()}",
                }
            )
            bars_by_symbol.pop(symbol, None)
            providers.pop(symbol, None)

    if len(bars_by_symbol) < top_n:
        raise RuntimeError(
            "Too few same-as-of histories after stale/suspension filtering: "
            f"{len(bars_by_symbol)} < {top_n}"
        )

    engine = FactorEngine(load_factor_config(factor_config))
    factor_result = engine.run(bars_by_symbol)
    metadata_columns = ["symbol", "name", "exchange", "board", "is_st", "screen_source"]
    metadata = candidates.loc[:, [column for column in metadata_columns if column in candidates.columns]]
    ranking = factor_result.ranking.merge(metadata, on="symbol", how="left")
    enriched = FactorRunResult(ranking=ranking, exclusions=factor_result.exclusions)
    paths = write_selection_outputs(
        enriched,
        selection_dir,
        top_n=top_n,
        fetch_failures=failures,
    )

    summary = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
    provider_counts = dict(Counter(providers.values()))
    summary.update(
        {
            "market_as_of": market_as_of.date().isoformat(),
            "candidate_count": len(candidates),
            "history_success_count": len(bars_by_symbol),
            "screen_source": screen_source,
            "security_master_source": master_source,
            "security_master_warning": master_warning,
            "spot_warning": spot_warning,
            "history_provider_counts": provider_counts,
            "refresh_warning_count": len(refresh_warnings),
            "refresh_warnings": refresh_warnings,
        }
    )
    paths["summary_json"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    index = build_dashboard(
        site_dir,
        selection_dir=selection_dir,
        title="A股量化研究系统",
        mode_label="每日云端研究",
    )
    manifest_path = output_root / "manifest.json"
    manifest = {
        "market_as_of": market_as_of.date().isoformat(),
        "candidate_count": len(candidates),
        "history_success_count": len(bars_by_symbol),
        "selected_count": int(summary.get("selected_count", 0)),
        "screen_source": screen_source,
        "security_master_source": master_source,
        "security_master_observed_at": master["observed_at"].max().isoformat(),
        "spot_warning": spot_warning,
        "refresh_warning_count": len(refresh_warnings),
        "history_provider_counts": provider_counts,
        "selection_summary": str(paths["summary_json"]),
        "site_index": str(index),
        "strict_backtest_in_daily_page": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return DailyProductionResult(
        market_as_of=manifest["market_as_of"],
        candidate_count=len(candidates),
        history_success_count=len(bars_by_symbol),
        selected_count=int(summary.get("selected_count", 0)),
        screen_source=screen_source,
        selection_dir=str(selection_dir),
        site_dir=str(site_dir),
        manifest=str(manifest_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ashare-daily")
    parser.add_argument("--output-root", default="reports/production")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--factor-config", default="config/factors.yml")
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--history-days", type=int, default=420)
    parser.add_argument("--min-amount", type=float, default=50_000_000.0)
    parser.add_argument("--min-listing-days", type=int, default=120)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_daily_production(
        output_root=args.output_root,
        site_dir=args.site_dir,
        cache_root=args.cache_root,
        factor_config=args.factor_config,
        candidate_limit=args.candidate_limit,
        top_n=args.top,
        history_days=args.history_days,
        min_amount=args.min_amount,
        min_listing_days=args.min_listing_days,
        workers=args.workers,
        adjust=args.adjust,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
