"""Желаемая дата заказчика и расчётная дата изготовления: успеваем ли и с каким запасом (в рабочих днях)."""
from datetime import date, datetime

from app.domain.desired_date import check_desired


def test_in_time_with_margin_in_workdays():
    c = check_desired(datetime(2026, 10, 16, 15), date(2026, 10, 21))  # пт → ср: пн, вт, ср
    assert c.status == "ok" and c.workdays == 3
    assert "запас 3 раб. дн." in c.label


def test_same_day_is_in_time():
    c = check_desired(datetime(2026, 10, 21, 16), date(2026, 10, 21))
    assert c.status == "ok" and c.workdays == 0


def test_late_counts_workdays_over_weekend():
    c = check_desired(datetime(2026, 10, 26, 10), date(2026, 10, 22))  # чт → пн: пт, пн
    assert c.status == "late" and c.workdays == 2
    assert "не успеваем" in c.label and "2 раб. дн." in c.label


def test_unknown_without_dates():
    assert check_desired(None, date(2026, 10, 21)).status == "unknown"
    assert check_desired(datetime(2026, 10, 21), None).status == "none"


def test_proposal_range_ten_percent_rounded_up_in_workdays():
    from app.domain.desired_date import proposal_range
    r = proposal_range(date(2026, 10, 19), datetime(2026, 11, 6, 15))  # пн 19.10 — пт 06.11: 15 раб. дн.
    assert r.duration == 15 and r.delta == 2  # 1,5 → 2
    assert r.earliest == date(2026, 11, 4) and r.latest == date(2026, 11, 10)


def test_short_order_gets_at_least_one_day():
    from app.domain.desired_date import proposal_range
    r = proposal_range(date(2026, 10, 19), datetime(2026, 10, 21, 12))
    assert r.duration == 3 and r.delta == 1 and r.latest == date(2026, 10, 22)


def test_desired_against_range():
    from app.domain.desired_date import proposal_range
    r = proposal_range(date(2026, 10, 19), datetime(2026, 11, 6, 15))
    assert r.against(date(2026, 11, 12)).status == "ok"
    inside = r.against(date(2026, 11, 5))
    assert inside.status == "range" and "до 3 раб. дн." in inside.label
    late = r.against(date(2026, 11, 2))
    assert late.status == "late" and "4 раб. дн." in late.label
    assert r.against(None).status == "none"
