"""План не в прошлом: неначатые позиции — от «сейчас», начатые — как есть; быстрый расчёт — от той же точки."""
from datetime import datetime

import pytest

from app.services import operation_service as ops
from app.services import plan_service
from app.services import quick_service as quick
from tests.services.conftest import MON, part_files
from tests.services.test_operation_flow import CUTTER, approved
from tests.services.test_order_flow import prepared
from tests.services.test_quick_intake import MANAGER

NOW = datetime(2026, 10, 21, 10, 30)


@pytest.fixture
def marks_on(monkeypatch):
    """Логика «по факту» явно включена (по умолчанию план только по плановому времени)."""
    from app.domain import scheduling
    monkeypatch.setattr(scheduling, "PLAN_FROM_MARKS", True)  # среда, после даты начала заказов MON


def test_unstarted_position_planned_from_now(session, make_order):
    pos = prepared(session, make_order)
    before = plan_service.current_plan(session).positions[pos.id]  # «сейчас» в тестах — раньше MON
    assert before.machine_start.date() == MON
    now = plan_service.current_plan(session, now=NOW).positions[pos.id]
    assert now.cutting_end > NOW and now.machine_start >= NOW and now.finish > before.finish


def test_started_position_not_moved(session, make_order):
    pos = approved(session, make_order)
    first = ops.position_operations(session, pos.id)[0]
    ops.start(session, CUTTER, first.id, datetime(2026, 10, 12, 9))
    assert plan_service.current_plan(session, now=NOW).positions[pos.id] == \
        plan_service.current_plan(session).positions[pos.id]


def test_quick_calc_where_faster_and_busy_from_same_moment(session, tmp_path, monkeypatch):
    monkeypatch.setattr(plan_service, "clock", lambda: NOW)
    r = quick.calculate(session, MANAGER, part_files("Кронштейн-1"), tmp_path / "work", start_date=MON)
    p = r.summary["positions"][0]
    current = next(a for a in p["alternatives"] if a["current"])
    busy = next(m for m in r.summary["machines"] if m["model"] == current["model"])
    assert current["free_from"] >= NOW.isoformat() and busy["start"] >= NOW.isoformat()
    assert current["free_from"][:10] == busy["start"][:10] == NOW.date().isoformat()
    assert r.summary["calc_date"] >= NOW.isoformat()


def test_machine_work_starts_at_actual_mark(session, make_order, marks_on):
    """PLAN_FROM_MARKS: наладку начали в пятницу после смены станка — план берёт отметку «Начал»."""
    from tests.services.conftest import OPERATOR
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    cut, setup = rows[0], next(r for r in rows if r.stage == "setup")
    ops.start(session, CUTTER, cut.id, datetime(2026, 10, 16, 14, 0))
    ops.finish(session, CUTTER, cut.id, datetime(2026, 10, 16, 14, 30))
    ops.start(session, OPERATOR, setup.id, datetime(2026, 10, 16, 15, 20))
    plan = plan_service.current_plan(session, now=datetime(2026, 10, 16, 18, 0))
    assert plan.positions[pos.id].machine_start == datetime(2026, 10, 16, 15, 20)
    assert all(s.end >= s.start for s in plan.slots)


def test_load_is_work_volume_and_dashboard_matches_plan_tables(session, make_order, marks_on):
    """PLAN_FROM_MARKS: операцию «забыли» закончить на 3 дня — не 18 ч простоя; главная = «По неделям»."""
    from app.domain.machine_work import IDLE_CAP_FACTOR
    from app.services import dashboard_service
    from tests.services.conftest import HEAD, OPERATOR
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    cut, setup, mill = rows[0], rows[1], rows[2]
    ops.start(session, CUTTER, cut.id, datetime(2026, 10, 20, 9))
    ops.finish(session, CUTTER, cut.id, datetime(2026, 10, 20, 9, 30))
    ops.start(session, OPERATOR, setup.id, datetime(2026, 10, 20, 10))
    ops.finish(session, OPERATOR, setup.id, datetime(2026, 10, 20, 10, 30))
    ops.start(session, OPERATOR, mill.id, datetime(2026, 10, 20, 10, 30))
    ops.finish(session, OPERATOR, mill.id, datetime(2026, 10, 23, 14, 30))  # «висела» начатой
    now = datetime(2026, 10, 23, 16)
    load = plan_service.load_tables(session, now=now)
    week = load.weekly["VF2"][(2026, 43)].hours
    assert week == pytest.approx(0.5 + min(mill.planned_hours * IDLE_CAP_FACTOR, 22.0))
    board = dashboard_service.build_dashboard(session, HEAD, now=now)
    vf2 = next(m for m in board.machines if m.code == "VF2")
    assert vf2.hours == pytest.approx(week)


def _mark_all_machine_ops(session, pos):
    from tests.services.conftest import OPERATOR
    rows = ops.position_operations(session, pos.id)
    ops.finish(session, CUTTER, rows[0].id, datetime(2026, 10, 20, 9, 30))
    setup, mill = rows[1], rows[2]  # наладка вне смены, обработка «висела» 3 дня
    ops.start(session, OPERATOR, setup.id, datetime(2026, 10, 20, 19, 0))
    ops.finish(session, OPERATOR, setup.id, datetime(2026, 10, 20, 20, 0))
    ops.start(session, OPERATOR, mill.id, datetime(2026, 10, 20, 20, 0))
    ops.finish(session, OPERATOR, mill.id, datetime(2026, 10, 23, 18, 0))


def test_marks_do_not_change_bars_or_load(session, make_order):
    """Решение пользователя: «Начал»/«Закончил» — только прослеживаемость; полосы и загрузка — по плану."""
    from app.services import dashboard_service
    from tests.services.conftest import HEAD
    pos = approved(session, make_order)
    now = datetime(2026, 10, 23, 18, 30)
    ops.start(session, CUTTER, ops.position_operations(session, pos.id)[0].id, datetime(2026, 10, 20, 9))
    plan_before = plan_service.current_plan(session, now)
    load_before = plan_service.load_tables(session, plan_before, now)
    _mark_all_machine_ops(session, pos)
    assert plan_service.fact_ends(session, pos.id)  # факт для журнала записан, как прежде
    plan_after = plan_service.current_plan(session, now)
    assert plan_after.slots == plan_before.slots
    assert plan_after.positions == plan_before.positions
    load_after = plan_service.load_tables(session, plan_after, now)
    assert load_after.daily == load_before.daily and load_after.weekly == load_before.weekly
    total = sum(c.hours for c in load_after.daily["VF2"].values())
    assert total == pytest.approx(sum(s.hours for s in plan_after.slots if s.machine_code == "VF2"))
    assert all(s.end >= s.start for s in plan_after.slots)
    board = dashboard_service.build_dashboard(session, HEAD, now=now)
    week = load_after.weekly["VF2"].get((2026, 43))
    assert next(m for m in board.machines if m.code == "VF2").hours == pytest.approx(week.hours if week else 0)
