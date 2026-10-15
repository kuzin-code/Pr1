"""Рабочий экран: сводка по участку и задачи роли."""
from datetime import datetime

import pytest

from app.domain.access import AccessDenied
from app.services import approval_service as approval
from app.services import tech_service as tech
from app.services.dashboard_service import build_dashboard
from tests.services.conftest import HEAD, IT, MASTER, MGMT, OPERATOR, TECH, part_files
from tests.services.test_order_flow import prepared

NOW = datetime(2026, 10, 14, 10, 0)


def kpi(board, key):
    return next(k for k in board.kpis if k.key == key)


def test_counts_by_stage(session, make_order):
    a = prepared(session, make_order)  # проверка технолога
    b = prepared(session, make_order)
    tech.submit(session, TECH, b.id)  # ожидает утверждения
    c = prepared(session, make_order)
    tech.submit(session, TECH, c.id)
    approval.approve(session, HEAD, c.id)
    board = build_dashboard(session, HEAD, NOW)
    assert kpi(board, "orders").value == 3
    assert kpi(board, "review").value == 1
    assert kpi(board, "approval").value == 1
    assert kpi(board, "production").value == 1
    assert a.id in [t.position_id for t in build_dashboard(session, TECH, NOW).tasks]


def test_tasks_per_role(session, make_order):
    pos = prepared(session, make_order)
    tech.submit(session, TECH, pos.id)
    head_tasks = build_dashboard(session, HEAD, NOW).tasks
    assert [t.position_id for t in head_tasks] == [pos.id] and "Утвердить" in head_tasks[0].action
    assert head_tasks[0].cost is not None  # начальник видит себестоимость
    unlinked = make_order([part_files("Без пары")[0]]).positions[0]
    master_tasks = build_dashboard(session, MASTER, NOW).tasks
    assert any(t.position_id == unlinked.id and "файл" in t.action for t in master_tasks)
    prepared(session, make_order)  # ещё одна позиция на проверке технолога
    assert any(t.cost is not None for t in build_dashboard(session, TECH, NOW).tasks)  # технолог видит
    assert all(t.cost is None for t in build_dashboard(session, MGMT, NOW).tasks)  # руководство — нет


def test_week_load_and_deadlines(session, make_order):
    pos = prepared(session, make_order)
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    board = build_dashboard(session, MGMT, NOW)
    vf2 = next(m for m in board.machines if m.code == "VF2")
    assert vf2.hours == pytest.approx(7) and vf2.percent == pytest.approx(7 / 30 * 100)
    assert board.deadlines and board.deadlines[0].order_number.startswith("З-")
    assert kpi(board, "load").value == pytest.approx(round(7 / (30 * 5) * 100))


def test_greeting_and_events(session, make_order):
    prepared(session, make_order)
    board = build_dashboard(session, TECH, NOW)
    assert board.date_label == "среда, 14 октября 2026"
    assert board.events and all(e.text for e in board.events)


def test_only_office_roles(session):
    for actor in (OPERATOR, IT):
        with pytest.raises(AccessDenied):
            build_dashboard(session, actor, NOW)


@pytest.mark.parametrize("hour,text", [(8, "Доброе утро"), (13, "Добрый день"), (19, "Добрый вечер")])
def test_greeting_by_time_of_day(session, hour, text):
    assert build_dashboard(session, HEAD, datetime(2026, 10, 14, hour)).greeting == text


def test_events_show_designation(session, make_order):
    pos = prepared(session, make_order)
    assert any(pos.designation in e.text for e in build_dashboard(session, HEAD, NOW).events)
