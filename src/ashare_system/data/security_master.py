from __future__ import annotations

from pathlib import Path

import pandas as pd

SECURITY_MASTER_COLUMNS = (
    "symbol",
    "name",
    "exchange",
    "board",
    "list_date",
    "industry",
    "total_shares",
    "float_shares",
    "is_st",
    "is_listed",
    "observed_at",
)


def _optional_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(pd.NA, index=frame.index, dtype="object")


def validate_security_master(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in SECURITY_MASTER_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing security-master columns: {missing}")

    result = frame.loc[:, SECURITY_MASTER_COLUMNS].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["name"] = result["name"].astype(str)
    result["exchange"] = result["exchange"].astype(str)
    result["board"] = result["board"].astype(str)
    result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce")
    result["industry"] = result["industry"].astype("string")
    result["total_shares"] = pd.to_numeric(result["total_shares"], errors="coerce")
    result["float_shares"] = pd.to_numeric(result["float_shares"], errors="coerce")
    result["is_st"] = result["is_st"].astype(bool)
    result["is_listed"] = result["is_listed"].astype(bool)
    result["observed_at"] = pd.to_datetime(result["observed_at"], utc=True, errors="raise")

    invalid_exchange = ~result["exchange"].isin({"SSE", "SZSE", "BSE"})
    if invalid_exchange.any():
        values = sorted(result.loc[invalid_exchange, "exchange"].unique())
        raise ValueError(f"Unsupported exchanges in security master: {values}")
    if result["symbol"].duplicated().any():
        duplicates = sorted(result.loc[result["symbol"].duplicated(False), "symbol"].unique())
        raise ValueError(f"Duplicate security symbols: {duplicates[:10]}")
    return result.sort_values("symbol").reset_index(drop=True)


def normalize_sh_security_master(
    frame: pd.DataFrame,
    *,
    board: str,
    observed_at: pd.Timestamp,
) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "symbol": frame["证券代码"],
            "name": frame["证券简称"],
            "exchange": "SSE",
            "board": board,
            "list_date": frame["上市日期"],
            "industry": _optional_series(frame, "所属行业"),
            "total_shares": _optional_series(frame, "总股本"),
            "float_shares": _optional_series(frame, "流通股本"),
        }
    )
    return _finish_current_master(result, observed_at)


def normalize_sz_security_master(
    frame: pd.DataFrame,
    *,
    observed_at: pd.Timestamp,
) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "symbol": frame["A股代码"],
            "name": frame["A股简称"],
            "exchange": "SZSE",
            "board": frame["板块"],
            "list_date": frame["A股上市日期"],
            "industry": _optional_series(frame, "所属行业"),
            "total_shares": _optional_series(frame, "A股总股本"),
            "float_shares": _optional_series(frame, "A股流通股本"),
        }
    )
    return _finish_current_master(result, observed_at)


def normalize_bj_security_master(
    frame: pd.DataFrame,
    *,
    observed_at: pd.Timestamp,
) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "symbol": frame["证券代码"],
            "name": frame["证券简称"],
            "exchange": "BSE",
            "board": "北交所",
            "list_date": frame["上市日期"],
            "industry": _optional_series(frame, "所属行业"),
            "total_shares": _optional_series(frame, "总股本"),
            "float_shares": _optional_series(frame, "流通股本"),
        }
    )
    return _finish_current_master(result, observed_at)


def _finish_current_master(frame: pd.DataFrame, observed_at: pd.Timestamp) -> pd.DataFrame:
    result = frame.copy()
    names = result["name"].astype(str).str.upper()
    result["is_st"] = names.str.contains("ST", regex=False)
    result["is_listed"] = True
    result["observed_at"] = observed_at
    return validate_security_master(result)


class AkshareSecurityMasterProvider:
    """Build the current A-share master from official-exchange AKShare interfaces."""

    name = "akshare_exchange_security_master"

    def get_current(self) -> pd.DataFrame:
        import akshare as ak

        observed_at = pd.Timestamp.now(tz="UTC")
        sh_main = normalize_sh_security_master(
            ak.stock_info_sh_name_code(symbol="主板A股"),
            board="上证主板",
            observed_at=observed_at,
        )
        sh_star = normalize_sh_security_master(
            ak.stock_info_sh_name_code(symbol="科创板"),
            board="科创板",
            observed_at=observed_at,
        )
        sz = normalize_sz_security_master(
            ak.stock_info_sz_name_code(symbol="A股列表"),
            observed_at=observed_at,
        )
        bj = normalize_bj_security_master(
            ak.stock_info_bj_name_code(),
            observed_at=observed_at,
        )
        return validate_security_master(pd.concat([sh_main, sh_star, sz, bj], ignore_index=True))


class SecurityMasterSnapshotStore:
    """Append-only Parquet snapshots of the observable current security master."""

    def __init__(self, root: str | Path = "data/cache") -> None:
        self.root = Path(root)

    def save(self, frame: pd.DataFrame) -> Path:
        master = validate_security_master(frame)
        observed_at = master["observed_at"].max()
        stamp = observed_at.strftime("%Y%m%dT%H%M%SZ")
        path = self.root / "security_master" / f"{stamp}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        master.to_parquet(path, index=False)
        return path

    def load_latest(self) -> pd.DataFrame:
        directory = self.root / "security_master"
        candidates = sorted(directory.glob("*.parquet"))
        if not candidates:
            raise FileNotFoundError(f"No security-master snapshots under {directory}")
        return validate_security_master(pd.read_parquet(candidates[-1]))
