import pytest

from ashare_system.st_event_crosscheck import _to_bs_code


def test_event_crosscheck_code_mapping():
    assert _to_bs_code("600000") == "sh.600000"
    assert _to_bs_code("000001") == "sz.000001"
    assert _to_bs_code("300001") == "sz.300001"
    with pytest.raises(ValueError, match="Unsupported"):
        _to_bs_code("430047")
