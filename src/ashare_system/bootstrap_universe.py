from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.spot import SPOT_COLUMNS, AkshareEastmoneySpotProvider

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


def normalize_sina_spot(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize AKShare's Sina all-A-share snapshot for one-shot cold-start use."""
    if raw is None or raw.empty:
        raise RuntimeError("Sina all-A-share snapshot is empty")
    required = {"代码", "名称", "最新价", "成交额"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Sina spot payload missing columns: {missing}")

    symbols = raw["代码"].astype(str).str.extract(r"(\d{6})$", expand=False)
    frame = pd.DataFrame(
        {
            "symbol": symbols,
            "name": raw["名称"].astype(str),
            "last": pd.to_numeric(raw["最新价"], errors="coerce"),
            "amount": pd.to_numeric(raw["成交额"], errors="coerce"),
            "turnover_pct": pd.NA,
            "market_cap": pd.NA,
            "float_market_cap": pd.NA,
        }
    )
    frame = frame[frame["symbol"].notna() & frame["last"].gt(0)].copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame = frame.drop_duplicates("symbol", keep="last")
    if frame.empty:
        raise RuntimeError("Sina all-A-share snapshot contains no valid prices")
    return frame.loc[:, SPOT_COLUMNS].reset_index(drop=True)


def _sina_spot_once() -> pd.DataFrame:
    """Fetch Sina's paginated all-A snapshot once; intentionally no retry loop."""
    import akshare as ak

    return normalize_sina_spot(ak.stock_zh_a_spot())


def build_bootstrap_candidates(
    spot: pd.DataFrame,
    *,
    limit: int = 30,
    min_amount: float = 50_000_000.0,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    screen_source: str = "bootstrap_spot_amount",
) -> pd.DataFrame:
    """Build a bounded current candidate set when the canonical master cannot cold-start.

    This degraded path is intentionally *not* a security-master snapshot. It uses an
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
        raise RuntimeError("spot bootstrap returned no liquid candidates")

    frame["board"] = [
        _board_for_symbol(symbol, exchange)
        for symbol, exchange in zip(frame["symbol"], frame["exchange"], strict=True)
    ]
    frame["screen_source"] = screen_source
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


def _candidate_cache_paths(cache_root: Path) -> tuple[Path, Path]:
    directory = cache_root / "candidate_universe"
    return directory / "latest.csv", directory / "manifest.json"


def _save_candidate_cache(
    cache_root: Path,
    candidates: pd.DataFrame,
    *,
    source: str,
) -> None:
    candidates_path, manifest_path = _candidate_cache_paths(cache_root)
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(candidates_path, index=False)
    payload = {
        "saved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source": source,
        "candidate_count": len(candidates),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_recent_candidate_cache(
    cache_root: Path,
    *,
    max_age_days: int = 7,
) -> tuple[pd.DataFrame, dict[str, object]]:
    candidates_path, manifest_path = _candidate_cache_paths(cache_root)
    if not candidates_path.exists() or not manifest_path.exists():
        raise FileNotFoundError("No cached candidate universe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    saved_at = pd.Timestamp(manifest["saved_at"])
    if saved_at.tzinfo is None:
        saved_at = saved_at.tz_localize("UTC")
    else:
        saved_at = saved_at.tz_convert("UTC")
    if saved_at < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=max_age_days):
        raise RuntimeError(f"Candidate cache is older than {max_age_days} days")

    frame = pd.read_csv(candidates_path, dtype={"symbol": str})
    required = {"symbol", "name", "exchange", "board", "is_st", "screen_source"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Candidate cache missing columns: {missing}")
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["screen_source"] = "candidate_cache_fallback"
    if "listing_age_verified" not in frame.columns:
        frame["listing_age_verified"] = False
    return frame, manifest


def write_bootstrap_universe(
    *,
    output_root: str | Path = "reports/universe",
    cache_root: str | Path = "data/cache",
    candidate_limit: int = 30,
    min_amount: float = 50_000_000.0,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    reason_file: str | Path | None = None,
) -> dict[str, object]:
    output = Path(output_root)
    cache = Path(cache_root)
    output.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    primary_reason = _reason_tail(reason_file)
    if primary_reason:
        warnings.append(f"canonical_master: {primary_reason}")

    candidates: pd.DataFrame | None = None
    screen_source: str | None = None
    provider_source: str | None = None

    try:
        spot = AkshareEastmoneySpotProvider().get_current()
        screen_source = "bootstrap_eastmoney_spot_amount"
        provider_source = "eastmoney_all_a_spot"
        candidates = build_bootstrap_candidates(
            spot,
            limit=candidate_limit,
            min_amount=min_amount,
            allowed_exchanges=allowed_exchanges,
            screen_source=screen_source,
        )
    except Exception as exc:  # noqa: BLE001 - next provider is an intentional fallback
        warnings.append(f"eastmoney_spot: {type(exc).__name__}: {exc}")

    if candidates is None:
        try:
            spot = _sina_spot_once()
            screen_source = "bootstrap_sina_spot_amount"
            provider_source = "sina_all_a_spot"
            candidates = build_bootstrap_candidates(
                spot,
                limit=candidate_limit,
                min_amount=min_amount,
                allowed_exchanges=allowed_exchanges,
                screen_source=screen_source,
            )
        except Exception as exc:  # noqa: BLE001 - recent candidate cache is final fallback
            warnings.append(f"sina_spot: {type(exc).__name__}: {exc}")

    cache_manifest: dict[str, object] | None = None
    if candidates is None:
        try:
            candidates, cache_manifest = _load_recent_candidate_cache(cache)
            candidates = candidates.head(candidate_limit).reset_index(drop=True)
            screen_source = "candidate_cache_fallback"
            provider_source = "recent_candidate_cache"
        except Exception as exc:  # noqa: BLE001 - fail closed after all bounded fallbacks
            warnings.append(f"candidate_cache: {type(exc).__name__}: {exc}")
            raise RuntimeError("All cold-start universe sources failed: " + " | ".join(warnings)) from exc
    else:
        _save_candidate_cache(cache, candidates, source=provider_source or screen_source or "unknown")

    candidates_file = output / "candidates.csv"
    candidates.to_csv(candidates_file, index=False)

    now = pd.Timestamp.now(tz=CHINA_TZ)
    manifest = {
        "prepared_as_of": now.date().isoformat(),
        "candidate_count": len(candidates),
        "screen_source": screen_source,
        "allowed_exchanges": list(allowed_exchanges),
        "security_master_source": "spot_bootstrap_no_canonical_snapshot",
        "security_master_warning": primary_reason,
        "security_master_observed_at": None,
        "spot_provider_source": provider_source,
        "fallback_warnings": warnings,
        "listing_age_verified": False,
        "degraded_mode": True,
        "candidate_cache_origin": cache_manifest,
        "candidates_file": str(candidates_file),
    }
    manifest_path = output / "universe_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cold-start fallback for the daily A-share universe")
    parser.add_argument("--output-root", default="reports/universe")
    parser.add_argument("--cache-root", default="data/cache")
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
        cache_root=args.cache_root,
        candidate_limit=args.candidate_limit,
        min_amount=args.min_amount,
        allowed_exchanges=exchanges,
        reason_file=args.reason_file,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
