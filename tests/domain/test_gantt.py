"""Диаграмма Ганта загрузки станков с ЧПУ: шкала дней и полосы работ."""
from datetime import date, datetime

import pytest

from app.domain.gantt import build_gantt
from app.domain.scheduling import MachineSlot

MACHINES = [("VF2", "Haas VF-2"), ("SMM", "Haas Super Mini Mill"), ("ST20Y", "Haas ST-20Y")]


def slot(pid, machine, start, end, preliminary=False):
    return MachineSlot(machine, pid, start, end, preliminary)


def test_rows_for_all_cnc_machines_even_without_work():
    g = build_gantt([slot(1, "VF2", datetime(2026, 10, 12, 10), datetime(2026, 10, 13, 10))], MACHINES,
                    now=datetime(2026, 10, 12, 9))
    assert [r.code for r in g.rows] == ["VF2", "SMM", "ST20Y"]
    assert g.rows[1].bars == [] and g.rows[0].hours == pytest.approx(6)  # 4 ч пн + 2 ч вт по графику станка


def test_day_scale_with_weekends_and_minimum_length():
    g = build_gantt([], MACHINES, now=datetime(2026, 10, 15, 12), min_days=14)
    assert g.days[0].day == date(2026, 10, 15) and len(g.days) == 14
    weekends = [d.day for d in g.days if d.weekend]
    assert weekends[:2] == [date(2026, 10, 17), date(2026, 10, 18)]
    assert g.width == 14 * g.day_px


def test_bar_geometry_and_today_line():
    g = build_gantt([slot(1, "VF2", datetime(2026, 10, 13, 12), datetime(2026, 10, 14, 0))], MACHINES,
                    now=datetime(2026, 10, 12, 12), day_px=48)
    bar = g.rows[0].bars[0]
    assert bar.left == pytest.approx(48 + 24)  # 13.10 12:00 от начала шкалы 12.10 00:00
    assert bar.width == pytest.approx(24)
    assert g.today == pytest.approx(24)


def test_short_bar_has_minimum_width():
    g = build_gantt([slot(1, "VF2", datetime(2026, 10, 12, 8), datetime(2026, 10, 12, 8, 30))], MACHINES,
                    now=datetime(2026, 10, 12, 8))
    assert g.rows[0].bars[0].width >= 6


def test_scale_starts_at_earliest_work_or_today():
    g = build_gantt([slot(1, "SMM", datetime(2026, 10, 5, 8), datetime(2026, 10, 6, 8))], MACHINES,
                    now=datetime(2026, 10, 12, 8))
    assert g.days[0].day == date(2026, 10, 5)
    assert g.today == pytest.approx(7 * g.day_px + g.day_px * 8 / 24)


def test_labels_kinds_and_links_passed_through():
    g = build_gantt([slot(7, "ST20Y", datetime(2026, 10, 12, 8), datetime(2026, 10, 12, 14), preliminary=True)],
                    MACHINES, now=datetime(2026, 10, 12, 8),
                    info={7: {"label": "Вал ×40", "title": "Заказ З-1", "kind": "preliminary"}})
    bar = g.rows[2].bars[0]
    assert (bar.label, bar.title, bar.kind, bar.position_id) == ("Вал ×40", "Заказ З-1", "preliminary", 7)


def test_slot_on_non_cnc_or_unknown_machine_ignored():
    g = build_gantt([slot(1, "BS275", datetime(2026, 10, 12, 8), datetime(2026, 10, 12, 9))], MACHINES,
                    now=datetime(2026, 10, 12, 8))
    assert all(not r.bars for r in g.rows)
