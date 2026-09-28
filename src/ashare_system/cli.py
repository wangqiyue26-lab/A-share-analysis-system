from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import date, timedelta
from pathlib import Path

from . import __version__
from .data.akshare_provider import AkshareProvider
from .data.cache import ParquetBarCache


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
    end = args.end or date.today().strftime("%Y%m%d")
    start = args.start or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    provider = AkshareProvider()
    bars = provider.get_daily_bars(args.symbol, start, end, args.adjust)
    print(
        json.dumps(
            {
                "provider": provider.name,
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
