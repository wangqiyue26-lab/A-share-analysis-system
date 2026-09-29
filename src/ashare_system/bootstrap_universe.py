from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.spot import AkshareEastmoneySpotProvider, SPOT_COLUMNS

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _exchange_for_symbol(symbol: str) -> str | None:
    code = str(symbol).zfill(6)
    if code.startswith("6"):
        return "SSE"
    if code.startswith(("0", "3")):
        return "SZSE"
    if code.startswith(("4", "8", "9")):
        return "BSE"
    return None


def _board_for_symbol(symbol: str, exchange: str) -> str:
    code = str(symbol).zfill(6)
    if exchange == "SSE":
        return "科创板" if code.startswith(("688", "689")) else "上证主板"
    if exchange == "SZSE":
        return "创业板" if code.startswith(("300", "301")) else "深证主板"
    return "北交所"


def build_bootstrap_candidates(
    spot: pd.DataFrame,
    *,
    limit: int = 30,
    min_amount: float = 50_000_000.0,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
) -> pd.DataFrame:
    """Build a bounded current candidate set when the canonical master cannot cold-start.

    This degraded path is intentionally *not* a security-master snapshot. It uses the
    all-A-share spot table only for today's candidate handoff. Listing age remains
    unverified here and is guarded later by the history/factor minimum-row checks.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if min_amount < 0:
        raise ValueError("min_amount cannot be negative")

    required = set(SPOT_COLUMNS)
    missing = sorted(required - set(spot.columns))
    if missing:
        raise ValueError(f"spot payload missing columns: {missing}")

    allowed = {str(item).upper() for item in allowed_exchanges}
    if not allowed or not allowed.issubset({"SSE", "SZSE", "BSE"}):
        raise ValueError(f"unsupported allowed exchanges: {sorted(allowed)}")

    frame = spot.loc[:, SPOT_COLUMNS].copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["exchange"] = frame["symbol"].map(_exchange_for_symbol)
    frame = frame[frame["exchange"].isin(allowed)].copy()
    frame["name"] = frame["name"].astype(str)
    frame["is_st"] = frame["name"].str.upper().str.contains("ST", regex=False)
    frame = frame[~frame["is_st"]].copy()
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["float_market_cap"] = pd.to_numeric(frame["float_market_cap"], errors="coerce")
    frame = frame[frame["amount"].ge(min_amount)].copy()
    if frame.empty:
        raise RuntimeError("spot bootstrap returned no liquid SSE/SZSE candidates")

    frame["board"] = [
        _board_for_symbol(symbol, exchange)
        for symbol, exchange in zip(frame["symbol"], frame["exchange"], strict=True)
    ]
    frame["screen_source"] = "bootstrap_eastmoney_spot_amount"
    frame["listing_age_verified"] = False
    frame = frame.sort_values(
        ["amount", "float_market_cap", "symbol"],
        ascending=[False, False, True],
        na_position="last",
    ).head(limit)
    columns = [
        "symbol",
        "name",
        "exchange",
        "board",
        "is_st",
        "screen_source",
        "listing_age_verified",
        "amount",
        "float_market_cap",
    ]
    return frame.loc[:, columns].reset_index(drop=True)


def _reason_tail(path: str | Path | None) -> str | None:
    if not path:
        return None
    source = Path(path)
    if not source.exists():
        return f"reason_file_missing:{source}"
    text = source.read_text(encoding="utf-8", errors="replace").strip()
    return text[-2000:] if text else None


def write_bootstrap_universe(
    *,
    output_root: str | Path = "reports/universe",
    candidate_limit: int = 30,
    min_amount: float = 50_000_000.0,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    reason_file: str | Path | None = None,
) -> dict[str, object]:
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    spot = AkshareEastmoneySpotProvider().get_current()
    candidates = build_bootstrap_candidates(
        spot,
        limit=candidate_limit,
        min_amount=min_amount,
        allowed_exchanges=allowed_exchanges,
    )
    candidates_file = output / "candidates.csv"
    candidates.to_csv(candidates_file, index=False)

    now = pd.Timestamp.now(tz=CHINA_TZ)
    manifest = {
        "prepared_as_of": now.date().isoformat(),
        "candidate_count": len(candidates),
        "screen_source": "bootstrap_eastmoney_spot_amount",
        "allowed_exchanges": list(allowed_exchanges),
        "security_master_source": "spot_bootstrap_no_canonical_snapshot",
        "security_master_warning": _reason_tail(reason_file),
        "security_master_observed_at": None,
        "spot_warning": None,
        "listing_age_verified": False,
        "degraded_mode": True,
        "candidates_file": str(candidates_file),
    }
    manifest_path = output / "universe_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cold-start fallback for the daily A-share universe")
    parser.add_argument("--output-root", default="reports/universe")
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--min-amount", type=float, default=50_000_000.0)
    parser.add_argument("--allowed-exchanges", default="SSE,SZSE")
    parser.add_argument("--reason-file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    exchanges = tuple(item.strip().upper() for item in args.allowed_exchanges.split(",") if item.strip())
    manifest = write_bootstrap_universe(
        output_root=args.output_root,
        candidate_limit=args.candidate_limit,
        min_amount=args.min_amount,
        allowed_exchanges=exchanges,
        reason_file=args.reason_file,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
