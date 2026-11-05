import pandas as pd
import pytest

from ashare_system.historical_candidate_reconstruction import (
    _membership_for_date,
    normalize_lifecycle,
)
from ashare_system.verified_lifecycle_correction import (
    OFFICIAL_SSE_NOTICE,
    apply_reviewed_delisting_correction,
)


def _archive():
    return pd.DataFrame(
        {
            "symbol": ["600190", "600000"],
            "name": ["退市锦港", "浦发银行"],
            "exchange": ["SSE", "SSE"],
            "board": ["上证主板", "上证主板"],
            "list_date": ["1999-06-09", "1999-11-10"],
            "delist_date": ["2025-07-28", None],
        }
    )


def test_official_delisting_excludes_600190_on_actual_deletion_day():
    source = _archive()
    corrected, audit = apply_reviewed_delisting_correction(source)
    assert source.loc[0, "delist_date"] == "2025-07-28"
    assert corrected.loc[0, "delist_date"] == "2025-07-25"
    assert corrected.loc[1, "delist_date"] is None
    assert audit["official_source_url"] == OFFICIAL_SSE_NOTICE
    assert audit["strict_live_observed"] is False
    assert audit["changed_row_count"] == 1
    universe = normalize_lifecycle(corrected)
    prior = _membership_for_date(
        universe, pd.Timestamp("2025-07-24"),
        min_listing_days=120, allowed_exchanges=("SSE", "SZSE"),
    )
    delisted = _membership_for_date(
        universe, pd.Timestamp("2025-07-25"),
        min_listing_days=120, allowed_exchanges=("SSE", "SZSE"),
    )
    assert "600190" in set(prior["symbol"])
    assert "600190" not in set(delisted["symbol"])
    assert "600000" in set(delisted["symbol"])


def test_correction_refuses_unexpected_prior_delist_date():
    source = _archive()
    source.loc[0, "delist_date"] = "2025-07-27"
    with pytest.raises(ValueError, match="delisting mismatch"):
        apply_reviewed_delisting_correction(source)


def test_correction_refuses_missing_or_duplicate_symbol():
    with pytest.raises(ValueError, match="Expected exactly one"):
        apply_reviewed_delisting_correction(_archive().iloc[1:].copy())
    duplicate = pd.concat([_archive(), _archive().iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate lifecycle symbols"):
        apply_reviewed_delisting_correction(duplicate)


def test_correction_refuses_wrong_exchange():
    source = _archive()
    source.loc[0, "exchange"] = "SZSE"
    with pytest.raises(ValueError, match="non-SSE"):
        apply_reviewed_delisting_correction(source)
