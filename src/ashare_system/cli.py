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
from .data.router import DataRouter
from .data.security_master import AkshareSecurityMasterProvider, SecurityMasterSnapshotStore

CHINA_TZ = ZoneInfo("Asia/Shanghai")


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
    router = DataRouter([AkshareEastmoneyProvider(), AkshareSinaProvider()])
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
