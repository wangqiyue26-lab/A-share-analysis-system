from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .data.akshare_provider import AkshareEastmoneyProvider
from .data.akshare_sina_provider import AkshareSinaProvider
from .data.cache import ParquetBarCache
from .data.financials import AkshareSinaFinancialProvider
from .data.router import DataRouter
from .data.security_master import AkshareSecurityMasterProvider, SecurityMasterSnapshotStore
from .factors import FactorEngine, load_factor_config
from .reporting import write_selection_outputs

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _data_router() -> DataRouter:
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
    router = _data_router()
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


def command_smoke_fundamentals(args: argparse.Namespace) -> int:
    provider = AkshareSinaFinancialProvider()
    facts = provider.get_statement(args.symbol, args.statement)
    periods = facts["period_end"].drop_duplicates().sort_values()
    availability = facts["available_at"].drop_duplicates().sort_values()
    payload = {
        "provider": provider.name,
        "symbol": str(args.symbol).zfill(6),
        "statement": args.statement,
        "fact_count": len(facts),
        "report_period_count": len(periods),
        "first_report_period": periods.iloc[0].date().isoformat(),
        "last_report_period": periods.iloc[-1].date().isoformat(),
        "first_available_at": availability.iloc[0].date().isoformat(),
        "last_available_at": availability.iloc[-1].date().isoformat(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        facts.to_parquet(output, index=False)
        print(f"output={output}")
    return 0


def command_select_sample(args: argparse.Namespace) -> int:
    today = datetime.now(CHINA_TZ).date()
    end = args.end or today.strftime("%Y%m%d")
    start = args.start or (today - timedelta(days=240)).strftime("%Y%m%d")
    symbols = [item.strip().zfill(6) for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise ValueError("--symbols must contain at least one A-share code")

    bars_by_symbol = {}
    failures: list[dict[str, str]] = []
    providers: dict[str, str | None] = {}
    for symbol in symbols:
        router = _data_router()
        try:
            bars_by_symbol[symbol] = router.get_daily_bars(symbol, start, end, args.adjust)
            providers[symbol] = router.last_provider_name
        except (RuntimeError, ValueError, ConnectionError) as exc:
            failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})

    if not bars_by_symbol:
        raise RuntimeError("No market data could be fetched for the requested sample")

    config = load_factor_config(args.factor_config)
    engine = FactorEngine(
        config,
        min_history=args.min_history,
        min_average_amount_20=args.min_average_amount,
    )
    result = engine.run(bars_by_symbol)
    paths = write_selection_outputs(
        result,
        args.output_dir,
        top_n=args.top,
        fetch_failures=failures,
    )
    payload = {
        "symbols_requested": symbols,
        "symbols_fetched": sorted(bars_by_symbol),
        "providers": providers,
        "ranked_count": int(result.ranking["rank"].notna().sum()),
        "selected_csv": str(paths["selected_csv"]),
        "summary_json": str(paths["summary_json"]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
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

    master = subparsers.add_parser(
        "security-master-smoke",
        help="Fetch current SSE/SZSE/BSE lists and report exchange-level completeness",
    )
    master.add_argument("--cache-dir")
    master.add_argument("--require-complete", action="store_true")
    master.set_defaults(func=command_security_master_smoke)

    fundamentals = subparsers.add_parser(
        "smoke-fundamentals",
        help="Fetch one financial statement with point-in-time availability timestamps",
    )
    fundamentals.add_argument("--symbol", default="000001")
    fundamentals.add_argument(
        "--statement",
        default="利润表",
        choices=["资产负债表", "利润表", "现金流量表"],
    )
    fundamentals.add_argument("--output")
    fundamentals.set_defaults(func=command_smoke_fundamentals)

    select = subparsers.add_parser(
        "select-sample",
        help="Fetch a small real A-share universe and generate an explainable ranking",
    )
    select.add_argument("--symbols", default="000001,600000,000333,600519,601318")
    select.add_argument("--start")
    select.add_argument("--end")
    select.add_argument("--adjust", default="", choices=["", "qfq", "hfq"])
    select.add_argument("--factor-config", default="config/factors.yml")
    select.add_argument("--min-history", type=int, default=80)
    select.add_argument("--min-average-amount", type=float, default=20_000_000.0)
    select.add_argument("--top", type=int, default=20)
    select.add_argument("--output-dir", default="reports/output/latest")
    select.set_defaults(func=command_select_sample)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
