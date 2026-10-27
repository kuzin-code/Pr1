"""Причина отклонения при «Закончил»: обязательность, замер в журнале, исправление мастером, сводка."""
from datetime import datetime, timedelta

import pytest

from app.domain.access import AccessDenied
from app.domain.downtime import DowntimeError
from app.domain.enums import StageGroup
from app.domain.history import ReasonRequired
from app.domain.work_calendar import BENCH_CALENDAR, MACHINE_CALENDAR
from app.services import downtime_service as dts
from app.services import operation_service as ops
from app.services import reference_service as ref
from app.services.history_service import list_history
from app.storage.orm import FactRow
from tests.services.conftest import HEAD, MASTER, OPERATOR
from tests.services.test_operation_flow import CUTTER, approved

START = datetime(2026, 10, 12, 9, 0)


def _run(session, actor, row, start, factor, reason=None, note="", calendar=MACHINE_CALENDAR):
    """Начал в start, закончил через план × factor рабочих часов."""
    ops.start(session, actor, row.id, start)
    end = calendar.add_hours(start, row.planned_hours * factor)
    ops.finish(session, actor, row.id, end, reason=reason, note=note)
    return end


def _to_machining(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    end = _run(session, CUTTER, rows[0], START, 1.0, calendar=BENCH_CALENDAR)
    return pos, rows, end


def test_finish_over_threshold_requires_reason(session, make_order):
    pos, rows, end = _to_machining(session, make_order)
    setup = rows[1]
    ops.start(session, OPERATOR, setup.id, end)
    late = MACHINE_CALENDAR.add_hours(end, setup.planned_hours * 1.5)
    with pytest.raises(DowntimeError, match="причину"):
        ops.finish(session, OPERATOR, setup.id, late)
    session.rollback()
    ops.finish(session, OPERATOR, setup.id, late, reason="tool")
    assert setup.reason == "tool" and setup.status == "done"
    h = [x for x in list_history(session, "position", pos.id) if x.field == "finish_setup"][0]
    assert h.new["reason"] == "tool"


def test_within_threshold_no_reason_needed(session, make_order):
    _, rows, end = _to_machining(session, make_order)
    _run(session, OPERATOR, rows[1], end, 1.1)
    assert rows[1].reason is None


def test_loss_reason_marks_measurement_not_in_k(session, make_order):
    pos, rows, end = _to_machining(session, make_order)
    end = _run(session, OPERATOR, rows[1], end, 1.0)
    _run(session, OPERATOR, rows[2], end, 2.0, reason="repair")
    fact = session.query(FactRow).filter_by(position_id=pos.id, kind="machine").one()
    assert fact.reason == "repair"
    m = ref.journal_entry(session, "DEMO-02").measurements[-1]
    assert m.group is StageGroup.MACHINE and m.reason == "repair" and not m.in_k


def test_setup_reason_stays_in_k(session, make_order):
    _, rows, end = _to_machining(session, make_order)
    end = _run(session, OPERATOR, rows[1], end, 2.0, reason="setup")
    _run(session, OPERATOR, rows[2], end, 1.0)
    m = ref.journal_entry(session, "DEMO-02").measurements[-1]
    assert m.reason == "setup" and m.in_k


def test_other_needs_comment(session, make_order):
    _, rows, end = _to_machining(session, make_order)
    ops.start(session, OPERATOR, rows[1].id, end)
    late = MACHINE_CALENDAR.add_hours(end, rows[1].planned_hours * 3)
    with pytest.raises(DowntimeError, match="комментарий"):
        ops.finish(session, OPERATOR, rows[1].id, late, reason="other")
    session.rollback()
    ops.finish(session, OPERATOR, rows[1].id, late, reason="other", note="не было крана")
    assert rows[1].reason_note == "не было крана"


def test_master_corrects_reason_with_comment(session, make_order):
    pos, rows, end = _to_machining(session, make_order)
    end = _run(session, OPERATOR, rows[1], end, 1.0)
    _run(session, OPERATOR, rows[2], end, 2.0, reason="repair")
    with pytest.raises(AccessDenied):
        dts.set_reason(session, OPERATOR, rows[2].id, "tool", "", "ошибся")
    with pytest.raises(ReasonRequired):
        dts.set_reason(session, MASTER, rows[2].id, "tool", "", " ")
    with pytest.raises(DowntimeError, match="причину"):
        dts.set_reason(session, MASTER, rows[2].id, "", "", "убрать причину")
    dts.set_reason(session, MASTER, rows[2].id, "tool", "", "на самом деле сломалась фреза")
    assert rows[2].reason == "tool"
    m = ref.journal_entry(session, "DEMO-02").measurements[-1]
    assert m.reason == "tool" and m.in_k
    assert session.query(FactRow).filter_by(position_id=pos.id, kind="machine").one().reason == "tool"
    h = [x for x in list_history(session, "position", pos.id) if x.field == "downtime_reason"][0]
    assert (h.old, h.new, h.reason) == ("ремонт", "инструмент", "на самом деле сломалась фреза")


def test_correct_only_finished_operation(session, make_order):
    _, rows, _ = _to_machining(session, make_order)
    with pytest.raises(DowntimeError, match="выполненной"):
        dts.set_reason(session, MASTER, rows[1].id, "tool", "", "проба")


def test_overrun_report_week_and_month(session, make_order):
    _, rows, end = _to_machining(session, make_order)
    end = _run(session, OPERATOR, rows[1], end, 2.0, reason="tool")
    over = rows[1].duration_hours - rows[1].planned_hours
    now = end + timedelta(days=1)
    with pytest.raises(AccessDenied):
        dts.overrun_report(session, OPERATOR, now)
    report = dts.overrun_report(session, HEAD, now)
    assert [title for title, _ in report] == ["Эта неделя", "Этот месяц"]
    week = report[0][1]
    assert week.rows[0][0] == "инструмент" and week.rows[0][1]["VF2"] == pytest.approx(over)


def test_overrun_periods_are_calendar_week_and_month():
    now = datetime(2026, 10, 28, 14, 0)  # среда
    (_, week), (_, month) = dts.periods(now)
    assert week == (datetime(2026, 10, 26), now) and month == (datetime(2026, 10, 1), now)
    monday = datetime(2026, 11, 2, 9, 0)  # неделя 02.11 — уже новый месяц
    assert dts.periods(monday)[0][1][0] == datetime(2026, 11, 2)
    assert dts.periods(monday)[1][1][0] == datetime(2026, 11, 1)
