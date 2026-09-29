from __future__ import annotations

import re

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

SPOT_COLUMNS = (
    "symbol",
    "name",
    "price",
    "change_pct",
    "volume",
    "amount",
    "turnover_pct",
    "pe_dynamic",
    "pb",
    "total_market_cap",
    "float_market_cap",
    "observed_at",
    "source",
)


def _optional_numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _clean_symbol(value: object) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"^(sh|sz|bj)", "", text)
    return text.zfill(6)


def validate_spot_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in SPOT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing spot columns: {missing}")
    result = frame.loc[:, SPOT_COLUMNS].copy()
    result["symbol"] = result["symbol"].map(_clean_symbol)
    result["name"] = result["name"].astype(str).str.strip()
    numeric = [
        "price",
        "change_pct",
        "volume",
        "amount",
        "turnover_pct",
        "pe_dynamic",
        "pb",
        "total_market_cap",
        "float_market_cap",
    ]
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["observed_at"] = pd.to_datetime(result["observed_at"], utc=True, errors="raise")
    result["source"] = result["source"].astype(str)
    result = result[result["symbol"].str.fullmatch(r"\d{6}", na=False)].copy()
    result = result.drop_duplicates("symbol", keep="last")
    return result.sort_values("symbol").reset_index(drop=True)


def normalize_eastmoney_spot(raw: pd.DataFrame, observed_at: pd.Timestamp) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise RuntimeError("Eastmoney full-market spot returned no rows")
    required = {"代码", "名称", "最新价", "成交额"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Eastmoney spot missing columns: {missing}")
    frame = pd.DataFrame(
        {
            "symbol": raw["代码"],
            "name": raw["名称"],
            "price": _optional_numeric(raw, "最新价"),
            "change_pct": _optional_numeric(raw, "涨跌幅"),
            # Eastmoney documents 成交量 in hands; normalize to shares.
            "volume": _optional_numeric(raw, "成交量") * 100.0,
            "amount": _optional_numeric(raw, "成交额"),
            "turnover_pct": _optional_numeric(raw, "换手率"),
            "pe_dynamic": _optional_numeric(raw, "市盈率-动态"),
            "pb": _optional_numeric(raw, "市净率"),
            "total_market_cap": _optional_numeric(raw, "总市值"),
            "float_market_cap": _optional_numeric(raw, "流通市值"),
            "observed_at": observed_at,
            "source": "akshare_eastmoney_spot",
        }
    )
    return validate_spot_snapshot(frame)


def normalize_sina_spot(raw: pd.DataFrame, observed_at: pd.Timestamp) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise RuntimeError("Sina full-market spot returned no rows")
    required = {"代码", "名称", "最新价", "成交额"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Sina spot missing columns: {missing}")
    frame = pd.DataFrame(
        {
            "symbol": raw["代码"],
            "name": raw["名称"],
            "price": _optional_numeric(raw, "最新价"),
            "change_pct": _optional_numeric(raw, "涨跌幅"),
            # Sina documents 成交量 in shares already.
            "volume": _optional_numeric(raw, "成交量"),
            "amount": _optional_numeric(raw, "成交额"),
            "turnover_pct": float("nan"),
            "pe_dynamic": float("nan"),
            "pb": float("nan"),
            "total_market_cap": float("nan"),
            "float_market_cap": float("nan"),
            "observed_at": observed_at,
            "source": "akshare_sina_spot",
        }
    )
    return validate_spot_snapshot(frame)


def prefilter_spot_snapshot(
    frame: pd.DataFrame,
    *,
    top_n: int = 100,
    min_amount: float = 50_000_000.0,
    min_price: float = 1.0,
    exclude_st: bool = True,
) -> pd.DataFrame:
    """Select a liquid research candidate universe before expensive history requests."""
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    snapshot = validate_spot_snapshot(frame)
    eligible = snapshot[
        snapshot["price"].notna()
        & snapshot["amount"].notna()
        & (snapshot["price"] >= min_price)
        & (snapshot["amount"] >= min_amount)
    ].copy()
    if exclude_st:
        upper_names = eligible["name"].str.upper()
        eligible = eligible[~upper_names.str.contains("ST", regex=False)].copy()
    return eligible.sort_values(["amount", "symbol"], ascending=[False, True]).head(top_n).reset_index(drop=True)


class AkshareSpotProvider:
    """Fetch one full A-share market snapshot with Eastmoney -> Sina failover."""

    name = "akshare_full_market_spot"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=5), reraise=True)
    def _eastmoney(self) -> pd.DataFrame:
        import akshare as ak

        observed_at = pd.Timestamp.now(tz="UTC")
        return normalize_eastmoney_spot(ak.stock_zh_a_spot_em(), observed_at)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=5), reraise=True)
    def _sina(self) -> pd.DataFrame:
        import akshare as ak

        observed_at = pd.Timestamp.now(tz="UTC")
        return normalize_sina_spot(ak.stock_zh_a_spot(), observed_at)

    def get_snapshot(self) -> pd.DataFrame:
        failures: list[str] = []
        for provider_name, fetcher in (("eastmoney", self._eastmoney), ("sina", self._sina)):
            try:
                snapshot = fetcher()
                if len(snapshot) < 1_000:
                    raise RuntimeError(f"suspiciously small A-share snapshot: {len(snapshot)} rows")
                snapshot.attrs["provider"] = provider_name
                return snapshot
            except Exception as exc:  # noqa: BLE001 - provider failover boundary
                failures.append(f"{provider_name}: {type(exc).__name__}: {exc}")
        raise RuntimeError("All full-market spot providers failed: " + " | ".join(failures))
