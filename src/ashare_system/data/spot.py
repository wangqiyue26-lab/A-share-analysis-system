from __future__ import annotations

from collections.abc import Collection

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
    for column in SPOT_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.drop_duplicates("symbol", keep="last")
    frame = frame[frame["last"].gt(0)].copy()
    if frame.empty:
        raise RuntimeError("Eastmoney all-A-share snapshot contains no valid prices")
    return frame.loc[:, SPOT_COLUMNS].reset_index(drop=True)


class AkshareEastmoneySpotProvider:
    """One-call all-A-share screen used only to reduce per-symbol history requests."""

    name = "akshare_eastmoney_all_a_spot"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def get_current(self) -> pd.DataFrame:
        import akshare as ak

        return normalize_eastmoney_spot(ak.stock_zh_a_spot_em())


def select_liquid_candidates(
    security_master: pd.DataFrame,
    spot: pd.DataFrame | None,
    *,
    as_of: str | pd.Timestamp,
    limit: int = 30,
    min_amount: float = 50_000_000.0,
    min_listing_days: int = 120,
    allowed_exchanges: Collection[str] | None = None,
) -> pd.DataFrame:
    """Build a bounded current research universe without silently accepting ST/new listings.

    `allowed_exchanges` controls the production universe without weakening the canonical
    security master. Phase 4B defaults the scheduled screen to SSE/SZSE while BSE history
    support is validated independently.

    When the bulk spot endpoint is unavailable, the function deliberately falls back to
    a size proxy from the complete security master. This is less precise than liquidity
    screening but avoids repeatedly hammering a second all-market endpoint.
    """
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

    if spot is not None and not spot.empty:
        snapshot = spot.loc[:, SPOT_COLUMNS].copy()
        merged = eligible.merge(snapshot, on="symbol", how="inner", suffixes=("", "_spot"))
        merged = merged[merged["amount"].ge(min_amount) & merged["last"].gt(0)].copy()
        if not merged.empty:
            merged["screen_source"] = "eastmoney_spot_amount"
            merged = merged.sort_values(
                ["amount", "float_market_cap", "symbol"],
                ascending=[False, False, True],
                na_position="last",
            )
            return merged.head(limit).reset_index(drop=True)

    eligible["size_proxy"] = pd.to_numeric(eligible["float_shares"], errors="coerce")
    eligible["size_proxy"] = eligible["size_proxy"].fillna(
        pd.to_numeric(eligible["total_shares"], errors="coerce")
    )
    eligible["size_proxy"] = eligible["size_proxy"].fillna(0.0)
    eligible["screen_source"] = "security_master_size_fallback"
    return eligible.sort_values(
        ["size_proxy", "symbol"], ascending=[False, True]
    ).head(limit).reset_index(drop=True)
