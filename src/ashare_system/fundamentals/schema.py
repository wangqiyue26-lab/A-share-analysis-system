from __future__ import annotations

import pandas as pd

FINANCIAL_FACT_COLUMNS = [
    "symbol",
    "report_period",
    "announcement_date",
    "statement",
    "item",
    "value",
    "currency",
    "audited",
    "source_update_date",
    "source",
]


def validate_financial_facts(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize long-form financial facts with disclosure timestamps."""
    missing = [column for column in FINANCIAL_FACT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Financial facts missing required columns: {missing}")

    result = frame[FINANCIAL_FACT_COLUMNS].copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["report_period"] = pd.to_datetime(result["report_period"], errors="coerce")
    result["announcement_date"] = pd.to_datetime(result["announcement_date"], errors="coerce")
    result["source_update_date"] = pd.to_datetime(result["source_update_date"], errors="coerce")
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result["statement"] = result["statement"].astype(str)
    result["item"] = result["item"].astype(str)

    required_dates = result[["report_period", "announcement_date"]]
    if required_dates.isna().any().any():
        raise ValueError("Financial facts require valid report_period and announcement_date")
    if (result["announcement_date"] < result["report_period"]).any():
        raise ValueError("announcement_date cannot precede report_period")
    if result["value"].isna().any():
        raise ValueError("Financial facts contain non-numeric values")
    if (result["item"].str.len() == 0).any():
        raise ValueError("Financial facts contain blank item names")

    return result.sort_values(
        ["symbol", "report_period", "statement", "item", "announcement_date", "source_update_date"],
        na_position="first",
    ).reset_index(drop=True)
