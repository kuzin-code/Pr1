"""Отмена ошибочной отметки «Начал» / «Закончил» мастером: операция, факт, замер k, статус позиции."""
import pytest

from app.domain.access import AccessDenied
from app.domain.enums import PositionStatus
from app.domain.history import ReasonRequired
from app.domain.operations import OperationError, OpStatus
from app.services import operation_service as ops
from app.services import plan_service as plans
from app.services import reference_service as ref
from tests.services.conftest import MASTER, OPERATOR
from tests.services.test_operation_flow import CUTTER, SLESAR, approved, at


def run(session, rows, upto):
    plan = [(CUTTER, at(12, 8), at(12, 10)), (OPERATOR, at(12, 10), at(12, 11)),
            (OPERATOR, at(12, 11), at(13, 12)), (SLESAR, at(13, 12), at(13, 15))]
    for row, (actor, s, e) in list(zip(rows, plan))[:upto]:
        ops.start(session, actor, row.id, s)
        ops.finish(session, actor, row.id, e, reason="tool")


def test_undo_start(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    ops.start(session, CUTTER, rows[0].id, at(12, 8))
    ops.undo_last(session, MASTER, pos.id, "нажал не ту позицию")
    assert rows[0].status == OpStatus.READY.value and rows[0].started_at is None
    assert pos.status == PositionStatus.APPROVED.value


def test_undo_finish_of_machine_group_reverts_fact_k_and_status(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    run(session, rows, 3)
    assert pos.status == PositionStatus.BENCH.value
    assert len(ref.journal_entry(session, "DEMO-02").measurements) == 1
    ops.undo_last(session, MASTER, pos.id, "ошибочно нажал «Закончил»")
    assert rows[2].status == OpStatus.IN_PROGRESS.value and rows[2].finished_at is None
    assert rows[3].status == OpStatus.WAITING.value
    assert pos.status == PositionStatus.MACHINING.value
    assert ref.journal_entry(session, "DEMO-02").measurements == []  # замер убран — k не испорчен
    from app.domain.enums import FactKind
    assert FactKind.MACHINE not in plans.fact_ends(session, pos.id)
    ops.finish(session, OPERATOR, rows[2].id, at(13, 13), reason="tool")  # повторная правильная отметка
    assert pos.status == PositionStatus.BENCH.value


def test_undo_goes_back_one_mark_at_a_time(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    run(session, rows, 1)
    ops.start(session, OPERATOR, rows[1].id, at(12, 10))
    ops.undo_last(session, MASTER, pos.id, "ошибка")  # снимает «Начал» наладки
    assert rows[1].status == OpStatus.READY.value and rows[0].status == OpStatus.DONE.value
    ops.undo_last(session, MASTER, pos.id, "ошибка")  # затем «Закончил» резки
    assert rows[0].status == OpStatus.IN_PROGRESS.value and rows[1].status == OpStatus.WAITING.value


def test_undo_rules(session, make_order):
    pos = approved(session, make_order)
    with pytest.raises(OperationError, match="нет отметок"):
        ops.undo_last(session, MASTER, pos.id, "ошибка")
    rows = ops.position_operations(session, pos.id)
    ops.start(session, CUTTER, rows[0].id, at(12, 8))
    with pytest.raises(ReasonRequired):
        ops.undo_last(session, MASTER, pos.id, " ")
    with pytest.raises(AccessDenied):
        ops.undo_last(session, OPERATOR, pos.id, "ошибка")


def test_last_mark_description(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    assert ops.last_mark(session, pos.id) is None
    run(session, rows, 1)
    assert ops.last_mark(session, pos.id) == "«Закончил»: Резка заготовки (заготовительная), 12.10 10:00"
