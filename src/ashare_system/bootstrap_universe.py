from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.akshare_provider import AkshareEastmoneyProvider
from .data.akshare_sina_provider import AkshareSinaProvider
from .data.router import DataRouter
from .data.security_master import SecurityMasterSnapshotStore, validate_security_master
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
    if limit <= 0:
        raise ValueError("limit must be positive")
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
        ["amount", "float_market_cap", "symbol"], ascending=[False, False, True], na_position="last"
    ).head(limit)
    return frame.loc[
        :, ["symbol", "name", "exchange", "board", "is_st", "screen_source", "listing_age_verified", "amount", "float_market_cap"]
    ].reset_index(drop=True)


def _history_liquidity_candidates(
    cache_root: Path,
    *,
    limit: int,
    min_amount: float,
    allowed_exchanges: tuple[str, ...],
    prefilter_limit: int = 60,
    workers: int = 4,
) -> pd.DataFrame:
    """Cold-start fallback: broad size prefilter, final rank by real 20-day traded amount."""
    master = validate_security_master(SecurityMasterSnapshotStore(cache_root).load_latest())
    today = pd.Timestamp.now(tz=CHINA_TZ).tz_localize(None).normalize()
    allowed = {item.upper() for item in allowed_exchanges}
    eligible = master[
        master["exchange"].isin(allowed)
        & master["is_listed"]
        & ~master["is_st"]
        & master["list_date"].notna()
        & master["list_date"].le(today - pd.Timedelta(days=120))
    ].copy()
    eligible["size_proxy"] = pd.to_numeric(eligible["float_shares"], errors="coerce").fillna(
        pd.to_numeric(eligible["total_shares"], errors="coerce")
    )
    eligible = eligible[eligible["size_proxy"].gt(0)].sort_values(
        ["size_proxy", "symbol"], ascending=[False, True]
    ).head(prefilter_limit)
    if len(eligible) < limit:
        raise RuntimeError("Not enough security-master rows for history-liquidity prefilter")

    start = (today - pd.Timedelta(days=65)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    def fetch_amount(symbol: str) -> tuple[str, float, str]:
        router = DataRouter([AkshareSinaProvider(), AkshareEastmoneyProvider()])
        bars = router.get_daily_bars(symbol, start, end, "")
        amount = pd.to_numeric(bars["amount"], errors="coerce").dropna().tail(20)
        if len(amount) < 10:
            raise RuntimeError(f"insufficient recent amount rows for {symbol}")
        return symbol, float(amount.mean()), str(router.last_provider_name)

    measurements: list[tuple[str, float, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_amount, str(symbol).zfill(6)): str(symbol).zfill(6) for symbol in eligible["symbol"]}
        for future in as_completed(futures):
            try:
                measurements.append(future.result())
            except Exception:
                continue

    amounts = pd.DataFrame(measurements, columns=["symbol", "amount", "history_provider"])
    amounts = amounts[amounts["amount"].ge(min_amount)].copy()
    if len(amounts) < limit:
        raise RuntimeError(f"History-liquidity fallback produced only {len(amounts)} usable symbols; need {limit}")
    result = eligible.merge(amounts, on="symbol", how="inner")
    result["screen_source"] = "history_liquidity_fallback"
    result["listing_age_verified"] = True
    result["float_market_cap"] = pd.NA
    result = result.sort_values(["amount", "symbol"], ascending=[False, True]).head(limit)
    return result.loc[
        :, ["symbol", "name", "exchange", "board", "is_st", "screen_source", "listing_age_verified", "amount", "float_market_cap", "history_provider"]
    ].reset_index(drop=True)


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


def _save_candidate_cache(cache_root: Path, candidates: pd.DataFrame, *, source: str) -> None:
    candidates_path, manifest_path = _candidate_cache_paths(cache_root)
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(candidates_path, index=False)
    manifest_path.write_text(
        json.dumps(
            {"saved_at": pd.Timestamp.now(tz="UTC").isoformat(), "source": source, "candidate_count": len(candidates)},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _load_recent_candidate_cache(cache_root: Path, *, max_age_days: int = 7) -> tuple[pd.DataFrame, dict[str, object]]:
    candidates_path, manifest_path = _candidate_cache_paths(cache_root)
    if not candidates_path.exists() or not manifest_path.exists():
        raise FileNotFoundError("No cached candidate universe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    saved_at = pd.Timestamp(manifest["saved_at"])
    saved_at = saved_at.tz_localize("UTC") if saved_at.tzinfo is None else saved_at.tz_convert("UTC")
    if saved_at < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=max_age_days):
        raise RuntimeError(f"Candidate cache is older than {max_age_days} days")
    frame = pd.read_csv(candidates_path, dtype={"symbol": str})
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["screen_source"] = "candidate_cache_fallback"
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
        warnings.append(f"primary_screen: {primary_reason}")

    candidates: pd.DataFrame | None = None
    screen_source: str | None = None
    provider_source: str | None = None
    cache_manifest: dict[str, object] | None = None

    for source_name, getter in (
        ("eastmoney_all_a_spot", AkshareEastmoneySpotProvider().get_current),
        ("sina_all_a_spot", _sina_spot_once),
    ):
        if candidates is not None:
            break
        try:
            spot = getter()
            screen_source = "bootstrap_eastmoney_spot_amount" if source_name.startswith("eastmoney") else "bootstrap_sina_spot_amount"
            candidates = build_bootstrap_candidates(
                spot,
                limit=candidate_limit,
                min_amount=min_amount,
                allowed_exchanges=allowed_exchanges,
                screen_source=screen_source,
            )
            provider_source = source_name
        except Exception as exc:  # noqa: BLE001 - provider failover is intentional
            warnings.append(f"{source_name}: {type(exc).__name__}: {exc}")

    if candidates is None:
        try:
            candidates, cache_manifest = _load_recent_candidate_cache(cache)
            candidates = candidates.head(candidate_limit).reset_index(drop=True)
            screen_source = "candidate_cache_fallback"
            provider_source = "recent_candidate_cache"
        except Exception as exc:
            warnings.append(f"candidate_cache: {type(exc).__name__}: {exc}")

    if candidates is None:
        try:
            candidates = _history_liquidity_candidates(
                cache,
                limit=candidate_limit,
                min_amount=min_amount,
                allowed_exchanges=allowed_exchanges,
            )
            screen_source = "history_liquidity_fallback"
            provider_source = "security_master_plus_recent_daily_bars"
        except Exception as exc:
            warnings.append(f"history_liquidity: {type(exc).__name__}: {exc}")
            raise RuntimeError("All cold-start universe sources failed: " + " | ".join(warnings)) from exc

    if provider_source != "recent_candidate_cache":
        _save_candidate_cache(cache, candidates, source=provider_source or screen_source or "unknown")

    candidates_file = output / "candidates.csv"
    candidates.to_csv(candidates_file, index=False)
    now = pd.Timestamp.now(tz=CHINA_TZ)
    manifest = {
        "prepared_as_of": now.date().isoformat(),
        "candidate_count": len(candidates),
        "screen_source": screen_source,
        "allowed_exchanges": list(allowed_exchanges),
        "security_master_source": "degraded_current_universe",
        "security_master_warning": primary_reason,
        "security_master_observed_at": None,
        "spot_provider_source": provider_source,
        "fallback_warnings": warnings,
        "listing_age_verified": bool(candidates["listing_age_verified"].all()) if "listing_age_verified" in candidates else False,
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
