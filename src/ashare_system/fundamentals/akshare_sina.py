from __future__ import annotations

from datetime import datetime

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from ashare_system.data.akshare_sina_provider import to_sina_symbol

from .schema import validate_financial_facts

_META_COLUMNS = {
    "报告日",
    "公告日期",
    "数据源",
    "是否审计",
    "币种",
    "类型",
    "更新日期",
}


def normalize_sina_statement(
    raw: pd.DataFrame,
    symbol: str,
    statement: str,
) -> pd.DataFrame:
    """Convert AKShare Sina's wide statement table into timestamped long-form facts."""
    if raw is None or raw.empty:
        raise RuntimeError(f"Sina returned no {statement} rows for {symbol}")
    required = {"报告日", "公告日期"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Sina financial payload missing columns: {missing}")

    frame = raw.copy()
    metric_columns = [column for column in frame.columns if column not in _META_COLUMNS]
    if not metric_columns:
        raise ValueError("Sina financial payload contains no metric columns")

    id_columns = [column for column in frame.columns if column in _META_COLUMNS]
    long = frame.melt(
        id_vars=id_columns,
        value_vars=metric_columns,
        var_name="item",
        value_name="value",
    )
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["value"]).copy()
    long["symbol"] = str(symbol).zfill(6)
    long["statement"] = statement
    long["report_period"] = long["报告日"]
    long["announcement_date"] = long["公告日期"]
    long["currency"] = long.get("币种", pd.Series("", index=long.index)).fillna("")
    long["audited"] = long.get("是否审计", pd.Series("", index=long.index)).fillna("")
    long["source_update_date"] = long.get(
        "更新日期", pd.Series(pd.NaT, index=long.index)
    )
    long["source"] = "akshare_sina_financial_report"
    return validate_financial_facts(long)


class AkshareSinaFinancialProvider:
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
        if not frames:
            raise RuntimeError(f"No financial statements fetched for {symbol}")
        return pd.concat(frames, ignore_index=True)


def retrieval_timestamp() -> str:
    """Return a UTC provenance timestamp for callers that persist provider snapshots."""
    return datetime.now().astimezone().astimezone().isoformat()
