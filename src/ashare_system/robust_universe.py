from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .bootstrap_universe import _history_liquidity_candidates, _load_recent_candidate_cache
from .data.security_master import (
    AkshareSecurityMasterProvider,
    SecurityMasterSnapshotStore,
    validate_security_master,
)
from .data.spot import fetch_live_spot, select_liquid_candidates

CHINA_TZ = ZoneInfo("Asia/Shanghai")


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


def _save_candidate_cache(cache_root: Path, candidates: pd.DataFrame, manifest: dict[str, object]) -> None:
    directory = cache_root / "candidate_universe"
    directory.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(directory / "latest.csv", index=False)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "saved_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "source": manifest.get("screen_source"),
                "candidate_count": len(candidates),
                "spot_provider_source": manifest.get("spot_provider_source"),
                "spot_coverage": manifest.get("spot_coverage"),
                "security_master_source": manifest.get("security_master_source"),
                "listing_age_verified": bool(manifest.get("listing_age_verified")),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _validate_cached_candidates(
    candidates: pd.DataFrame,
    master: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    limit: int,
    min_amount: float,
    min_listing_days: int,
    allowed_exchanges: tuple[str, ...],
) -> pd.DataFrame:
    """Revalidate a recent candidate cache against the current observable security master."""
    required = {"symbol", "amount"}
    missing = sorted(required - set(candidates.columns))
    if missing:
        raise ValueError(f"Candidate cache missing columns: {missing}")

    frame = candidates.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")

    current = validate_security_master(master)
    allowed = {item.upper() for item in allowed_exchanges}
    listed_before = as_of.normalize() - pd.Timedelta(days=min_listing_days)
    eligible = current[
        current["exchange"].isin(allowed)
        & current["is_listed"]
        & ~current["is_st"]
        & current["list_date"].notna()
        & current["list_date"].le(listed_before)
    ].copy()

    cached_columns = [
        column
        for column in ["symbol", "amount", "float_market_cap", "history_provider"]
        if column in frame.columns
    ]
    merged = eligible.merge(frame.loc[:, cached_columns], on="symbol", how="inner")
    merged = merged[merged["amount"].ge(min_amount)].copy()
    merged["screen_source"] = "candidate_cache_fallback"
    merged["listing_age_verified"] = True
    if "float_market_cap" not in merged:
        merged["float_market_cap"] = pd.NA
    merged = merged.sort_values(
        ["amount", "float_market_cap", "symbol"],
        ascending=[False, False, True],
        na_position="last",
    ).head(limit)
    if len(merged) < limit:
        raise RuntimeError(
            f"Recent candidate cache has only {len(merged)} currently eligible liquid rows; need {limit}"
        )
    return merged.reset_index(drop=True)


def prepare_robust_universe(
    *,
    output_root: str | Path = "reports/universe",
    cache_root: str | Path = "data/cache",
    candidate_limit: int = 30,
    min_amount: float = 50_000_000.0,
    min_listing_days: int = 120,
    min_spot_coverage: float = 0.80,
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE"),
) -> dict[str, object]:
    """Build a bounded universe through live, cache and history-liquidity layers."""
    output = Path(output_root)
    cache = Path(cache_root)
    output.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.now(tz=CHINA_TZ).tz_localize(None).normalize()

    master, master_source, master_warning = _complete_security_master(cache)
    warnings: list[str] = []
    live_provider: str | None = None
    live_coverage: float | None = None
    cache_origin: dict[str, object] | None = None
    degraded = master_source == "security_master_cache"

    candidates: pd.DataFrame | None = None
    screen_source: str | None = None

    try:
        live = fetch_live_spot(
            master,
            allowed_exchanges=allowed_exchanges,
            min_coverage=min_spot_coverage,
        )
        warnings.extend(live.warnings)
        candidates = select_liquid_candidates(
            master,
            live.frame,
            as_of=today,
            limit=candidate_limit,
            min_amount=min_amount,
            min_listing_days=min_listing_days,
            allowed_exchanges=allowed_exchanges,
            screen_source=live.screen_source,
        )
        if len(candidates) < candidate_limit:
            raise RuntimeError(
                f"Live liquidity screen produced only {len(candidates)} candidates; need {candidate_limit}"
            )
        candidates["listing_age_verified"] = True
        screen_source = live.screen_source
        live_provider = live.provider_name
        live_coverage = live.coverage
    except Exception as exc:  # noqa: BLE001 - continue into explicit stale-data layers
        warnings.append(f"live_spot_chain: {type(exc).__name__}: {exc}")
        candidates = None

    if candidates is None:
        degraded = True
        try:
            cached, cache_origin = _load_recent_candidate_cache(cache, max_age_days=3)
            candidates = _validate_cached_candidates(
                cached,
                master,
                as_of=today,
                limit=candidate_limit,
                min_amount=min_amount,
                min_listing_days=min_listing_days,
                allowed_exchanges=allowed_exchanges,
            )
            screen_source = "candidate_cache_fallback"
        except Exception as exc:  # noqa: BLE001 - continue into history-liquidity fallback
            warnings.append(f"candidate_cache: {type(exc).__name__}: {exc}")
            candidates = None

    if candidates is None:
        degraded = True
        try:
            candidates = _history_liquidity_candidates(
                cache,
                limit=candidate_limit,
                min_amount=min_amount,
                allowed_exchanges=allowed_exchanges,
            )
            screen_source = "history_liquidity_fallback"
        except Exception as exc:
            warnings.append(f"history_liquidity: {type(exc).__name__}: {exc}")
            raise RuntimeError("All universe source layers failed: " + " | ".join(warnings)) from exc

    candidates_file = output / "candidates.csv"
    candidates.to_csv(candidates_file, index=False)
    manifest = {
        "prepared_as_of": today.date().isoformat(),
        "candidate_count": len(candidates),
        "screen_source": screen_source,
        "allowed_exchanges": list(allowed_exchanges),
        "security_master_source": master_source,
        "security_master_warning": master_warning,
        "security_master_observed_at": master["observed_at"].max().isoformat(),
        "spot_provider_source": live_provider,
        "spot_coverage": live_coverage,
        "spot_provider_warnings": warnings,
        "spot_warning": " | ".join(warnings) if warnings else None,
        "listing_age_verified": (
            bool(candidates["listing_age_verified"].all())
            if "listing_age_verified" in candidates
            else False
        ),
        "degraded_mode": degraded,
        "candidate_cache_origin": cache_origin,
        "candidates_file": str(candidates_file),
    }
    manifest_path = output / "universe_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if screen_source in {"sina_spot_amount", "eastmoney_spot_amount", "history_liquidity_fallback"}:
        _save_candidate_cache(cache, candidates, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.robust_universe")
    parser.add_argument("--output-root", default="reports/universe")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--min-amount", type=float, default=50_000_000.0)
    parser.add_argument("--min-listing-days", type=int, default=120)
    parser.add_argument("--min-spot-coverage", type=float, default=0.80)
    parser.add_argument("--allowed-exchanges", default="SSE,SZSE")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    exchanges = tuple(part.strip().upper() for part in args.allowed_exchanges.split(",") if part.strip())
    result = prepare_robust_universe(
        output_root=args.output_root,
        cache_root=args.cache_root,
        candidate_limit=args.candidate_limit,
        min_amount=args.min_amount,
        min_listing_days=args.min_listing_days,
        min_spot_coverage=args.min_spot_coverage,
        allowed_exchanges=exchanges,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
