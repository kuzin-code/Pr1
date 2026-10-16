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
