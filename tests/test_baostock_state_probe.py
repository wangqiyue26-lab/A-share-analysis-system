import pandas as pd
import pytest

from ashare_system.baostock_state_probe import (
    _filter_a_share_codes,
    _normalize_bool_text,
    _to_bs_code,
)


def test_baostock_code_mapping():
    assert _to_bs_code("600000", "SSE") == "sh.600000"
    assert _to_bs_code("000001", "SZSE") == "sz.000001"
    assert _to_bs_code("300001", "SZSE") == "sz.300001"
    with pytest.raises(ValueError, match="Unsupported exchange"):
        _to_bs_code("430047", "BSE")


def test_baostock_a_share_filter_excludes_indexes_and_b_shares():
    frame = pd.DataFrame(
        {
            "code": [
                "sh.600000",
                "sh.900901",
                "sh.000001",
                "sz.000001",
                "sz.300001",
                "sz.200002",
                "sz.399001",
            ]
        }
    )
    filtered = _filter_a_share_codes(frame)
    assert filtered["code"].tolist() == ["sh.600000", "sz.000001", "sz.300001"]


def test_baostock_boolean_normalization_is_fail_closed():
    result = _normalize_bool_text(pd.Series(["0", "1", "0"]))
    assert result.tolist() == [False, True, False]
    with pytest.raises(ValueError, match="Unexpected BaoStock boolean"):
        _normalize_bool_text(pd.Series(["0", "", "1"]))
