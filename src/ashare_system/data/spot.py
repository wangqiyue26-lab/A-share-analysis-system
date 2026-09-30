from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from .security_master import REQUIRED_EXCHANGES, validate_security_master

SPOT_COLUMNS = (
    "symbol",
    "name",
    "last",
    "amount",
    "turnover_pct",
    "market_cap",
    "float_market_cap",
)


class SpotProvider(Protocol):
    name: str
    screen_source: str

    def get_current(self) -> pd.DataFrame: ...


@dataclass(frozen=True)
class LiveSpotResult:
    frame: pd.DataFrame
    provider_name: str
    screen_source: str
    coverage: float
    warnings: tuple[str, ...]


def normalize_eastmoney_spot(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize AKShare's all-A-share Eastmoney snapshot."""
    if raw is None or raw.empty:
        raise RuntimeError("Eastmoney all-A-share snapshot is empty")
    required = {"代码", "名称", "最新价", "成交额", "换手率", "总市值", "流通市值"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Eastmoney spot payload missing columns: {missing}")

    frame = pd.DataFrame(
        {
            "symbol": raw["代码"].astype(str).str.zfill(6),
            "name": raw["名称"].astype(str),
            "last": raw["最新价"],
            "amount": raw["成交额"],
            "turnover_pct": raw["换手率"],
            "market_cap": raw["总市值"],
            "float_market_cap": raw["流通市值"],
        }
    )
    return _finish_spot(frame, source="Eastmoney")


def normalize_sina_spot(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize AKShare's independent Sina all-A-share snapshot."""
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
            "last": raw["最新价"],
            "amount": raw["成交额"],
            "turnover_pct": pd.NA,
            "market_cap": pd.NA,
            "float_market_cap": pd.NA,
        }
    )
    return _finish_spot(frame, source="Sina")


def _finish_spot(frame: pd.DataFrame, *, source: str) -> pd.DataFrame:
    result = frame.copy()
    result = result[result["symbol"].notna()].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    for column in SPOT_COLUMNS[2:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.drop_duplicates("symbol", keep="last")
    result = result[result["last"].gt(0) & result["amount"].ge(0)].copy()
    if result.empty:
        raise RuntimeError(f"{source} all-A-share snapshot contains no valid prices")
    return result.loc[:, SPOT_COLUMNS].reset_index(drop=True)


class AkshareSinaSpotProvider:
    """Independent Sina all-A-share live snapshot used as the cloud-first source."""

    name = "akshare_sina_all_a_spot"
    screen_source = "sina_spot_amount"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def get_current(self) -> pd.DataFrame:
        import akshare as ak

        return normalize_sina_spot(ak.stock_zh_a_spot())


class AkshareEastmoneySpotProvider:
    """Eastmoney all-A-share live snapshot; useful but often rate-limited on cloud IPs."""

    name = "akshare_eastmoney_all_a_spot"
    screen_source = "eastmoney_spot_amount"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def get_current(self) -> pd.DataFrame:
        import akshare as ak

        return normalize_eastmoney_spot(ak.stock_zh_a_spot_em())


def live_spot_coverage(
    security_master: pd.DataFrame,
    spot: pd.DataFrame,
    *,
    allowed_exchanges: Collection[str] = ("SSE", "SZSE"),
) -> float:
    """Return coverage of currently listed exchange symbols by a live snapshot."""
    master = validate_security_master(security_master)
    allowed = {str(exchange).upper() for exchange in allowed_exchanges}
    eligible = master[master["is_listed"] & master["exchange"].isin(allowed)].copy()
    if eligible.empty:
        raise RuntimeError("Security master contains no listed symbols for live spot validation")
    spot_symbols = set(spot["symbol"].astype(str).str.zfill(6))
    return float(eligible["symbol"].isin(spot_symbols).mean())


def fetch_live_spot(
    security_master: pd.DataFrame,
    *,
    allowed_exchanges: Collection[str] = ("SSE", "SZSE"),
    min_coverage: float = 0.80,
    providers: Sequence[SpotProvider] | None = None,
) -> LiveSpotResult:
    """Fetch a current all-market snapshot from independent first-class providers.

    Sina is deliberately tried first on GitHub-hosted runners because Eastmoney's all-market
    endpoint frequently closes connections or rate-limits cloud IPs. Both sources are treated
    as live primary sources. Cache/history fallbacks belong outside this function.
    """
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")

    chain: Sequence[SpotProvider] = providers or (
        AkshareSinaSpotProvider(),
        AkshareEastmoneySpotProvider(),
    )
    warnings: list[str] = []
    for provider in chain:
        try:
            frame = provider.get_current()
            coverage = live_spot_coverage(
                security_master,
                frame,
                allowed_exchanges=allowed_exchanges,
            )
            if coverage < min_coverage:
                raise RuntimeError(
                    f"live snapshot coverage {coverage:.1%} below required {min_coverage:.1%}"
                )
            return LiveSpotResult(
                frame=frame,
                provider_name=provider.name,
                screen_source=provider.screen_source,
                coverage=coverage,
                warnings=tuple(warnings),
            )
        except Exception as exc:  # noqa: BLE001 - independent provider failover boundary
            warnings.append(f"{provider.name}: {type(exc).__name__}: {exc}")

    raise RuntimeError("All live all-market spot providers failed: " + " | ".join(warnings))


def select_liquid_candidates(
    security_master: pd.DataFrame,
    spot: pd.DataFrame | None,
    *,
    as_of: str | pd.Timestamp,
    limit: int = 30,
    min_amount: float = 50_000_000.0,
    min_listing_days: int = 120,
    allowed_exchanges: Collection[str] | None = None,
    screen_source: str = "eastmoney_spot_amount",
) -> pd.DataFrame:
    """Build a bounded current research universe without silently accepting ST/new listings."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if min_amount < 0:
        raise ValueError("min_amount cannot be negative")
    if min_listing_days < 0:
        raise ValueError("min_listing_days cannot be negative")

    master = validate_security_master(security_master)
    if allowed_exchanges is not None:
        allowed = {str(exchange).upper() for exchange in allowed_exchanges}
        unknown = sorted(allowed - set(REQUIRED_EXCHANGES))
        if unknown:
            raise ValueError(f"Unsupported allowed exchanges: {unknown}")
        if not allowed:
            raise ValueError("allowed_exchanges cannot be empty")
        master = master[master["exchange"].isin(allowed)].copy()

    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_localize(None)
    cutoff = cutoff.normalize()
    listed_before = cutoff - pd.Timedelta(days=min_listing_days)

    eligible = master[
        master["is_listed"]
        & ~master["is_st"]
        & master["list_date"].notna()
        & master["list_date"].le(listed_before)
    ].copy()
    if eligible.empty:
        raise RuntimeError("No eligible securities after exchange/listing/ST filters")

    if spot is None or spot.empty:
        raise RuntimeError("Current all-market spot snapshot is unavailable")

    snapshot = spot.loc[:, SPOT_COLUMNS].copy()
    merged = eligible.merge(snapshot, on="symbol", how="inner", suffixes=("", "_spot"))
    merged = merged[merged["amount"].ge(min_amount) & merged["last"].gt(0)].copy()
    if merged.empty:
        raise RuntimeError("Current spot snapshot produced no eligible liquid candidates")
    merged["screen_source"] = screen_source
    merged = merged.sort_values(
        ["amount", "float_market_cap", "symbol"],
        ascending=[False, False, True],
        na_position="last",
    )
    return merged.head(limit).reset_index(drop=True)
