from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.security_master import (
    AkshareSecurityMasterProvider,
    SecurityMasterSnapshotStore,
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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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
    """Build a bounded live universe using independent first-class spot providers."""
    output = Path(output_root)
    cache = Path(cache_root)
    output.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.now(tz=CHINA_TZ).tz_localize(None).normalize()

    master, master_source, master_warning = _complete_security_master(cache)
    live = fetch_live_spot(
        master,
        allowed_exchanges=allowed_exchanges,
        min_coverage=min_spot_coverage,
    )
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

    candidates_file = output / "candidates.csv"
    candidates.to_csv(candidates_file, index=False)
    manifest = {
        "prepared_as_of": today.date().isoformat(),
        "candidate_count": len(candidates),
        "screen_source": live.screen_source,
        "allowed_exchanges": list(allowed_exchanges),
        "security_master_source": master_source,
        "security_master_warning": master_warning,
        "security_master_observed_at": master["observed_at"].max().isoformat(),
        "spot_provider_source": live.provider_name,
        "spot_coverage": live.coverage,
        "spot_provider_warnings": list(live.warnings),
        "spot_warning": " | ".join(live.warnings) if live.warnings else None,
        "listing_age_verified": True,
        "degraded_mode": master_source == "security_master_cache",
        "candidates_file": str(candidates_file),
    }
    manifest_path = output / "universe_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
