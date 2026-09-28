from __future__ import annotations

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from .akshare_sina_provider import to_sina_symbol
from .point_in_time import validate_point_in_time_metrics

_META_COLUMNS = {
    "报告日",
    "公告日期",
    "更新日期",
    "数据源",
    "是否审计",
    "币种",
    "类型",
}


def normalize_sina_statement(
    raw: pd.DataFrame,
    symbol: str,
    statement: str,
) -> pd.DataFrame:
    """Normalize one Sina statement into the repository's PIT metric schema.

    Sina may expose a later ``更新日期`` for the currently visible version of a
    report. When present, the conservative availability time is the later of the
    original announcement and update dates so a revised value is never used
    before that revision existed.
    """
    if raw is None or raw.empty:
        raise RuntimeError(f"Sina returned no {statement} rows for {symbol}")

    required = {"报告日", "公告日期"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Sina financial payload missing columns: {missing}")

    metric_columns = [column for column in raw.columns if column not in _META_COLUMNS]
    if not metric_columns:
        raise ValueError("Sina financial payload contains no metric columns")

    id_columns = [column for column in raw.columns if column in _META_COLUMNS]
    long = raw.melt(
        id_vars=id_columns,
        value_vars=metric_columns,
        var_name="item",
        value_name="value",
    )
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["value"]).copy()
    if long.empty:
        raise ValueError("Sina financial payload contains no numeric facts")

    announced = pd.to_datetime(long["公告日期"], errors="coerce")
    available = announced.copy()
    if "更新日期" in long.columns:
        updated = pd.to_datetime(long["更新日期"], errors="coerce")
        available = pd.concat([announced, updated], axis=1).max(axis=1)

    frame = pd.DataFrame(
        {
            "symbol": str(symbol).zfill(6),
            "metric": statement + "::" + long["item"].astype(str),
            "value": long["value"],
            "period_end": long["报告日"],
            "available_at": available,
            "source": "akshare_sina_financial_report",
        }
    )
    return validate_point_in_time_metrics(frame)


class AkshareSinaFinancialProvider:
    """Disclosure-timestamped A-share financial statements via AKShare/Sina."""

    name = "akshare_sina_financial_report"
    statements = ("资产负债表", "利润表", "现金流量表")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def get_statement(self, symbol: str, statement: str) -> pd.DataFrame:
        if statement not in self.statements:
            raise ValueError(f"Unsupported financial statement: {statement}")

        import akshare as ak

        raw = ak.stock_financial_report_sina(
            stock=to_sina_symbol(symbol),
            symbol=statement,
        )
        return normalize_sina_statement(raw, symbol, statement)

    def get_all(self, symbol: str) -> pd.DataFrame:
        frames = [self.get_statement(symbol, statement) for statement in self.statements]
        return validate_point_in_time_metrics(pd.concat(frames, ignore_index=True))
