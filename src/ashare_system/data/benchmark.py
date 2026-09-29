from __future__ import annotations

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential


def to_index_symbol(code: str) -> str:
    normalized = str(code).strip().lower()
    if normalized.startswith(("sh", "sz", "bj", "csi")):
        return normalized
    digits = normalized.zfill(6)
    if digits.startswith("399"):
        return f"sz{digits}"
    if digits.startswith("899"):
        return f"bj{digits}"
    return f"sh{digits}"


def normalize_benchmark(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise RuntimeError("Benchmark provider returned no rows")
    date_column = "date" if "date" in frame.columns else "日期"
    close_column = "close" if "close" in frame.columns else "收盘"
    missing = [name for name, column in (("date", date_column), ("close", close_column)) if column not in frame.columns]
    if missing:
        raise ValueError(f"Benchmark payload missing canonical fields: {missing}")

    result = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(frame[date_column], errors="raise"),
            "close": pd.to_numeric(frame[close_column], errors="coerce"),
        }
    ).dropna(subset=["close"])
    if result.empty or (result["close"] <= 0).any():
        raise ValueError("Benchmark close values must be positive")
    if result["trade_date"].duplicated().any():
        raise ValueError("Duplicate benchmark trade dates detected")
    return result.sort_values("trade_date").reset_index(drop=True)


class AkshareBenchmarkProvider:
    """Fetch benchmark-index daily closes with Eastmoney -> Sina failover."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6), reraise=True)
    def _eastmoney(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        raw = ak.stock_zh_index_daily_em(symbol=symbol, start_date=start, end_date=end)
        return normalize_benchmark(raw)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6), reraise=True)
    def _sina(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        raw = normalize_benchmark(ak.stock_zh_index_daily(symbol=symbol))
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        return raw[(raw["trade_date"] >= start_ts) & (raw["trade_date"] <= end_ts)].reset_index(drop=True)

    def get_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        symbol = to_index_symbol(code)
        errors: list[str] = []
        for name, fetcher in (("eastmoney", self._eastmoney), ("sina", self._sina)):
            try:
                result = fetcher(symbol, start, end)
                if result.empty:
                    raise RuntimeError("empty benchmark window")
                result.attrs["provider"] = name
                result.attrs["symbol"] = symbol
                return result
            except Exception as exc:  # noqa: BLE001 - provider failover boundary
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        raise RuntimeError("All benchmark providers failed: " + " | ".join(errors))
