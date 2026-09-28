from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import __version__
from .data.akshare_provider import AkshareEastmoneyProvider
from .data.akshare_sina_provider import AkshareSinaProvider
from .data.cache import ParquetBarCache
from .data.calendar import AkshareSinaTradeCalendarProvider, latest_trade_date_on_or_before
from .data.router import DataRouter
from .data.security_master import AkshareSecurityMasterProvider, SecurityMasterSnapshotStore
from .factors.engine import FactorEngine
from .factors.scoring import load_factor_config
from .pipeline.daily import BatchBarUpdater, DailyResearchPipeline, write_pipeline_result

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _market_router() -> DataRouter:
    return DataRouter([AkshareEastmoneyProvider(), AkshareSinaProvider()])


def command_health(args: argparse.Namespace) -> int:
    payload = {
        "status": "ok",
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


def command_smoke_data(args: argparse.Namespace) -> int:
    today = datetime.now(CHINA_TZ).date()
    end = args.end or today.strftime("%Y%m%d")
    start = args.start or (today - timedelta(days=30)).strftime("%Y%m%d")
    router = _market_router()
    bars = router.get_daily_bars(args.symbol, start, end, args.adjust)
    print(
        json.dumps(
            {
                "provider": router.last_provider_name,
                "symbol": str(args.symbol).zfill(6),
                "rows": len(bars),
                "first_trade_date": bars.iloc[0]["trade_date"].date().isoformat(),
                "last_trade_date": bars.iloc[-1]["trade_date"].date().isoformat(),
                "last_close": float(bars.iloc[-1]["close"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.cache_dir:
        path = ParquetBarCache(args.cache_dir).save(args.symbol, bars)
        print(f"cache={path}")
    return 0


def command_calendar_smoke(args: argparse.Namespace) -> int:
    provider = AkshareSinaTradeCalendarProvider()
    calendar = provider.get_calendar()
    target = pd.Timestamp(args.day).date() if args.day else datetime.now(CHINA_TZ).date()
    latest = latest_trade_date_on_or_before(calendar, target)
    print(
        json.dumps(
            {
                "provider": provider.name,
                "calendar_rows": len(calendar),
                "requested_day": target.isoformat(),
                "latest_trade_date": latest.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_security_master_smoke(args: argparse.Namespace) -> int:
    provider = AkshareSecurityMasterProvider()
    result = provider.fetch_current()
    master = result.master
    counts = master.groupby("exchange")["symbol"].count().to_dict()
    payload = {
        "provider": provider.name,
        "complete": result.complete,
        "rows": len(master),
        "exchange_counts": {str(key): int(value) for key, value in counts.items()},
        "fetched_exchanges": result.fetched_exchanges,
        "failures": result.failures,
        "st_count": int(master["is_st"].sum()),
        "observed_at": master["observed_at"].max().isoformat(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.cache_dir:
        if result.complete:
            path = SecurityMasterSnapshotStore(args.cache_dir).save(master)
            print(f"cache={path}")
        else:
            print("cache_skipped=incomplete_security_master")

    if args.require_complete and not result.complete:
        return 2
    return 0


def command_pipeline_smoke(args: argparse.Namespace) -> int:
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise ValueError("--symbols must contain at least one stock code")

    target = pd.Timestamp(args.target).date()
    cache = ParquetBarCache(args.cache_dir)
    updater = BatchBarUpdater(
        _market_router,
        cache,
        max_workers=args.max_workers,
        bootstrap_calendar_days=args.bootstrap_days,
        adjust=args.adjust,
    )
    engine = FactorEngine(
        load_factor_config(args.factor_config),
        min_average_amount_20=args.min_average_amount,
    )
    pipeline = DailyResearchPipeline(updater, engine)
    result = pipeline.run(symbols, target)
    summary = write_pipeline_result(result, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.ranking.empty:
        print("ranking_empty=true")
        return 3
    print(result.ranking[["rank", "symbol", "composite_score"]].head(20).to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ashare-system")
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Print runtime health information")
    health.add_argument("--output")
    health.set_defaults(func=command_health)

    smoke = subparsers.add_parser("smoke-data", help="Fetch and validate one real A-share data sample")
    smoke.add_argument("--symbol", default="000001")
    smoke.add_argument("--start")
    smoke.add_argument("--end")
    smoke.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])
    smoke.add_argument("--cache-dir")
    smoke.set_defaults(func=command_smoke_data)

    calendar = subparsers.add_parser("calendar-smoke", help="Fetch and validate the A-share trade calendar")
    calendar.add_argument("--day")
    calendar.set_defaults(func=command_calendar_smoke)

    master = subparsers.add_parser(
        "security-master-smoke",
        help="Fetch current SSE/SZSE/BSE lists and report exchange-level completeness",
    )
    master.add_argument("--cache-dir")
    master.add_argument("--require-complete", action="store_true")
    master.set_defaults(func=command_security_master_smoke)

    pipeline = subparsers.add_parser(
        "pipeline-smoke",
        help="Run an end-to-end multi-stock update and factor-ranking smoke test",
    )
    pipeline.add_argument("--symbols", default="000001,600519,000858")
    pipeline.add_argument("--target", default="2024-06-28")
    pipeline.add_argument("--cache-dir", default="data/cache-smoke")
    pipeline.add_argument("--output-dir", default="reports/pipeline-smoke")
    pipeline.add_argument("--factor-config", default="config/factors.yml")
    pipeline.add_argument("--max-workers", type=int, default=3)
    pipeline.add_argument("--bootstrap-days", type=int, default=180)
    pipeline.add_argument("--min-average-amount", type=float, default=0.0)
    pipeline.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])
    pipeline.set_defaults(func=command_pipeline_smoke)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
