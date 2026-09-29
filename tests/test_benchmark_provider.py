import pandas as pd

from ashare_system.data.benchmark import normalize_benchmark, to_index_symbol


def test_index_symbol_inference():
    assert to_index_symbol("000300") == "sh000300"
    assert to_index_symbol("399001") == "sz399001"
    assert to_index_symbol("899050") == "bj899050"
    assert to_index_symbol("sh000905") == "sh000905"


def test_normalize_benchmark_supports_eastmoney_and_chinese_columns():
    english = pd.DataFrame(
        {"date": ["2024-01-02", "2024-01-03"], "close": [3000.0, 3030.0]}
    )
    chinese = pd.DataFrame(
        {"日期": ["2024-01-02", "2024-01-03"], "收盘": [5000.0, 5050.0]}
    )

    first = normalize_benchmark(english)
    second = normalize_benchmark(chinese)
    assert list(first.columns) == ["trade_date", "close"]
    assert first.iloc[-1]["close"] == 3030.0
    assert second.iloc[-1]["close"] == 5050.0
