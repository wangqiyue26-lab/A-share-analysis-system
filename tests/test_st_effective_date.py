import pandas as pd

from ashare_system.st_effective_date import (
    parse_effective_st_event,
    select_pilot_candidates,
)


def test_parse_verified_apply_st_event():
    text = (
        "公司股票将于2023年5月4日停牌一天，"
        "并于2023年5月5日开市起复牌并被实施其他风险警示。"
        "股票简称由“样本股份”变更为“ST样本”。"
    )
    status, event = parse_effective_st_event(
        text,
        announcement_time="2023-05-04 18:00:00",
        symbol="600001",
    )
    assert status == "verified"
    assert event is not None
    assert event.effective_date == "2023-05-05"
    assert event.is_st_before is False
    assert event.is_st_after is True
    assert event.action == "apply_st"


def test_parse_verified_remove_st_event():
    text = (
        "经交易所审核同意，自2024年6月3日开市起撤销其他风险警示。"
        "股票简称由“ST样本”变更为“样本股份”。"
    )
    status, event = parse_effective_st_event(
        text,
        announcement_time="2024-05-31 18:00:00",
        symbol="000001",
    )
    assert status == "verified"
    assert event is not None
    assert event.effective_date == "2024-06-03"
    assert event.is_st_before is True
    assert event.is_st_after is False
    assert event.action == "remove_st"


def test_st_to_star_st_does_not_change_boolean_state():
    text = (
        "自2024年5月6日开市起公司股票被实施退市风险警示。"
        "股票简称由“ST样本”变更为“*ST样本”。"
    )
    status, event = parse_effective_st_event(
        text,
        announcement_time="2024-04-30 20:00:00",
    )
    assert status == "no_boolean_name_transition"
    assert event is None


def test_remove_one_warning_but_continue_st_is_not_false_exit():
    text = (
        "自2023年1月9日开市起撤销退市风险警示并继续实施其他风险警示。"
        "股票简称由“*ST样本”变更为“ST样本”。"
    )
    status, event = parse_effective_st_event(
        text,
        announcement_time="2023-01-07 18:00:00",
    )
    assert status == "no_boolean_name_transition"
    assert event is None


def test_date_without_year_uses_announcement_context():
    text = (
        "公司股票自5月8日开市起被实施其他风险警示，"
        "股票简称由“样本股份”变更为“ST样本”。"
    )
    status, event = parse_effective_st_event(
        text,
        announcement_time="2025-05-07 20:00:00",
    )
    assert status == "verified"
    assert event is not None
    assert event.effective_date == "2025-05-08"


def test_pilot_sampling_is_deterministic_across_four_cohorts():
    rows = []
    for exchange, prefix in (("SSE", "600"), ("SZSE", "000")):
        for direction, candidate in (
            ("apply", "risk_warning_apply_candidate"),
            ("remove", "risk_warning_remove_candidate"),
        ):
            for index in range(5):
                rows.append(
                    {
                        "symbol": f"{prefix}{index:03d}",
                        "name": "样本",
                        "announcement_time": pd.Timestamp("2024-01-01")
                        + pd.Timedelta(days=index),
                        "announcement_title": f"{exchange}-{direction}-{index}",
                        "announcement_id": f"{exchange}-{direction}-{index}",
                        "pdf_url": f"http://static.cninfo.com.cn/{index}.PDF",
                        "event_candidate": candidate,
                    }
                )
    frame = pd.DataFrame(rows)
    first = select_pilot_candidates(frame, per_cohort=3)
    second = select_pilot_candidates(frame, per_cohort=3)
    pd.testing.assert_frame_equal(first, second)
    assert first.groupby("cohort").size().to_dict() == {
        "SSE_apply": 3,
        "SSE_remove": 3,
        "SZSE_apply": 3,
        "SZSE_remove": 3,
    }
