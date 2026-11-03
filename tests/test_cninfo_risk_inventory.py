import pandas as pd

from ashare_system.cninfo_risk_inventory import (
    _classify_title,
    _month_windows,
    normalize_cninfo_announcements,
)


def test_month_windows_cover_period_without_overlap():
    windows = _month_windows("2024-01-15", "2024-03-05")
    assert [(a.date().isoformat(), b.date().isoformat()) for a, b in windows] == [
        ("2024-01-15", "2024-01-31"),
        ("2024-02-01", "2024-02-29"),
        ("2024-03-01", "2024-03-05"),
    ]


def test_risk_title_classification_is_conservative():
    assert _classify_title("关于公司股票交易被实施退市风险警示的公告") == (
        "risk_warning_apply_candidate"
    )
    assert _classify_title("关于撤销退市风险警示暨停牌的公告") == (
        "risk_warning_remove_candidate"
    )
    assert _classify_title("关于公司股票终止上市的公告") == "delisting_related"
    assert _classify_title("关于风险提示的公告") == "other_risk_disclosure"


def test_cninfo_normalizer_accepts_chinese_columns_and_keeps_announcement_time():
    raw = pd.DataFrame(
        {
            "代码": ["600001", "000001", "830001"],
            "简称": ["沪样本", "深样本", "北样本"],
            "公告标题": [
                "关于股票交易被实施其他风险警示的公告",
                "关于撤销其他风险警示的公告",
                "关于风险提示的公告",
            ],
            "公告时间": ["2024-05-06 18:00:00", "2025-05-07 19:00:00", "2024-01-01"],
            "公告链接": ["a", "b", "c"],
        }
    )
    result = normalize_cninfo_announcements(raw, category="特别处理和退市")
    assert result["symbol"].tolist() == ["600001", "000001"]
    assert result["event_candidate"].tolist() == [
        "risk_warning_apply_candidate",
        "risk_warning_remove_candidate",
    ]
    assert pd.Timestamp(result.iloc[0]["announcement_time"]) == pd.Timestamp(
        "2024-05-06 18:00:00"
    )


def test_cninfo_normalizer_accepts_raw_api_columns_and_preserves_pdf_metadata():
    timestamp_ms = int(
        pd.Timestamp("2024-04-30 12:00:00", tz="UTC").timestamp() * 1000
    )
    raw = pd.DataFrame(
        {
            "secCode": ["300001"],
            "secName": ["样本"],
            "announcementTitle": ["关于实施退市风险警示的公告"],
            "announcementTime": [timestamp_ms],
            "announcementId": ["123456"],
            "orgId": ["9900000001"],
            "adjunctUrl": ["finalpage/2024-04-30/123456.PDF"],
            "adjunctType": ["PDF"],
        }
    )
    result = normalize_cninfo_announcements(raw, category="风险提示")
    assert result.iloc[0]["symbol"] == "300001"
    assert result.iloc[0]["event_candidate"] == "risk_warning_apply_candidate"
    assert result.iloc[0]["announcement_id"] == "123456"
    assert result.iloc[0]["adjunct_type"] == "PDF"
    assert result.iloc[0]["pdf_url"] == (
        "http://static.cninfo.com.cn/finalpage/2024-04-30/123456.PDF"
    )
    assert pd.Timestamp(result.iloc[0]["announcement_time"]) == pd.Timestamp(
        "2024-04-30 20:00:00"
    )
