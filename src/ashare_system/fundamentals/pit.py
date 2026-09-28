from __future__ import annotations

import pandas as pd

from .schema import validate_financial_facts


def point_in_time(
    facts: pd.DataFrame,
    as_of: str | pd.Timestamp,
    *,
    respect_source_update_date: bool = True,
) -> pd.DataFrame:
    """Return only facts that were observable by *as_of*.

    When a provider exposes a source update timestamp, revisions dated after the
    requested historical date are excluded. This is intentionally conservative.
    """
    normalized = validate_financial_facts(facts)
    cutoff = pd.Timestamp(as_of).normalize()

    visible = normalized[normalized["announcement_date"] <= cutoff].copy()
    if respect_source_update_date:
        update_visible = visible["source_update_date"].isna() | (
            visible["source_update_date"] <= cutoff
        )
        visible = visible[update_visible].copy()

    if visible.empty:
        return visible.reset_index(drop=True)

    visible["_update_sort"] = visible["source_update_date"].fillna(
        visible["announcement_date"]
    )
    visible = visible.sort_values(
        [
            "symbol",
            "report_period",
            "statement",
            "item",
            "announcement_date",
            "_update_sort",
        ]
    )
    visible = visible.drop_duplicates(
        subset=["symbol", "report_period", "statement", "item"],
        keep="last",
    )
    return visible.drop(columns="_update_sort").reset_index(drop=True)
