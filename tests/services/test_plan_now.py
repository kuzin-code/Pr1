"""План не в прошлом: неначатые позиции — от «сейчас», начатые — как есть; быстрый расчёт — от той же точки."""
from datetime import datetime

from app.services import operation_service as ops
from app.services import plan_service
from app.services import quick_service as quick
from tests.services.conftest import MON, part_files
from tests.services.test_operation_flow import CUTTER, approved
from tests.services.test_order_flow import prepared
from tests.services.test_quick_intake import MANAGER

NOW = datetime(2026, 10, 21, 10, 30)  # среда, после даты начала заказов MON


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


def test_machine_work_starts_at_actual_mark(session, make_order):
    """Наладку начали в пятницу после смены станка — план берёт отметку «Начал», а не понедельник."""
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
