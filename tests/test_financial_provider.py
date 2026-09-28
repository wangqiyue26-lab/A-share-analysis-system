import pandas as pd

from ashare_system.data.financials import normalize_sina_statement


def test_normalize_sina_statement_uses_later_revision_timestamp():
    raw = pd.DataFrame(
        {
            "报告日": ["2024-03-31"],
            "营业收入": [123.0],
            "净利润": [10.0],
            "公告日期": ["2024-04-26"],
            "更新日期": ["2024-05-03"],
            "币种": ["CNY"],
            "是否审计": ["否"],
            "数据源": ["test"],
            "类型": ["合并报表"],
        }
    )
    facts = normalize_sina_statement(raw, "000001", "利润表")

    assert set(facts["metric"]) == {"利润表::营业收入", "利润表::净利润"}
    assert set(facts["symbol"]) == {"000001"}
    assert facts["period_end"].dt.strftime("%Y-%m-%d").unique().tolist() == ["2024-03-31"]
    assert facts["available_at"].dt.strftime("%Y-%m-%d").unique().tolist() == ["2024-05-03"]
