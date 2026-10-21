"""Учёт выполнения операций специалистами: очередь, «Начал»/«Закончил», статусы, факт, уведомления."""
from datetime import datetime

import pytest

from app.domain.access import AccessDenied
from app.domain.enums import Operation, PositionStatus, Role, StageGroup
from app.domain.operations import OpStatus, OperationError
from app.services import approval_service as approval
from app.services import operation_service as ops
from app.services import plan_service as plans
from app.services import reference_service as ref
from app.services import tech_service as tech
from app.services.actor import Actor
from tests.services.conftest import HEAD, MASTER, OPERATOR, TECH
from tests.services.test_order_flow import prepared

CUTTER = Actor("zagotovitel", Role.CUTTER)
SLESAR = Actor("slesar", Role.LOCKSMITH)
KONTROL = Actor("kontroler", Role.INSPECTOR)
MON = datetime(2026, 10, 12, 9, 0)


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute)


def approved(session, make_order):
    pos = prepared(session, make_order)  # DEMO-02 на VF2, 10 шт.
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    return pos


def test_seed_machine_operations_and_specialist_accounts(session):
    assert ref.get_machine(session, "VF2").operations == ["setup", "milling"]
    assert ref.get_machine(session, "ST20Y").operations == ["setup", "turning"]
    assert ref.get_machine(session, "BS275").operations == ["cutting"]
    assert ref.get_machine(session, "AKB1000").operations == ["locksmith", "finishing"]
    for username in ("zagotovitel", "slesar", "kontroler"):
        assert ref.authenticate(session, username, "demo") is not None


def test_operations_created_on_approval(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    assert [(r.operation, r.resource) for r in rows] == [
        ("cutting", "BS275"), ("setup", "VF2"), ("milling", "VF2"),
        ("locksmith", "AKB1000"), ("finishing", "AKB1000"), ("inspection", None)]
    assert [r.status for r in rows] == ["ready"] + ["waiting"] * 5


def test_queues_by_specialist(session, make_order):
    approved(session, make_order)
    assert [r.operation for r in ops.queue(session, CUTTER)] == ["cutting"]
    assert {r.operation for r in ops.queue(session, OPERATOR)} == {"setup", "milling"}
    assert {r.operation for r in ops.queue(session, SLESAR)} == {"locksmith", "finishing"}
    assert [r.operation for r in ops.queue(session, KONTROL)] == ["inspection"]
    assert len(ops.queue(session, MASTER)) == 6
    assert [r.operation for r in ops.queue(session, OPERATOR, machine="VF2")] == ["setup", "milling"]
    assert ops.queue(session, OPERATOR, machine="SMM") == []
    with pytest.raises(AccessDenied):
        ops.queue(session, TECH)


def test_full_cycle_statuses_facts_and_k(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    ops.start(session, CUTTER, rows[0].id, at(12, 9))
    assert pos.status == PositionStatus.MACHINING.value  # изготовление (включая резку)
    ops.finish(session, CUTTER, rows[0].id, at(12, 11))
    assert rows[0].duration_hours == pytest.approx(2) and rows[1].status == OpStatus.READY.value
    ops.start(session, OPERATOR, rows[1].id, at(12, 11))
    ops.finish(session, OPERATOR, rows[1].id, at(12, 12))
    ops.start(session, OPERATOR, rows[2].id, at(12, 12))
    ops.finish(session, OPERATOR, rows[2].id, at(13, 13))  # 3 ч пн + 4 ч вт по 6-часовой смене
    assert rows[2].duration_hours == pytest.approx(7)
    assert pos.status == PositionStatus.BENCH.value
    m = ref.journal_entry(session, "DEMO-02").measurements[-1]
    assert m.group is StageGroup.MACHINE and m.actual_hours == pytest.approx(8)  # наладка 1 + обработка 7
    for row, actor, s, e in ((rows[3], SLESAR, at(13, 13), at(13, 16)), (rows[4], SLESAR, at(13, 16), at(13, 17))):
        ops.start(session, actor, row.id, s)
        ops.finish(session, actor, row.id, e)
    assert pos.status == PositionStatus.INSPECTION.value
    assert ref.journal_entry(session, "DEMO-02").measurements[-1].actual_hours == pytest.approx(4)
    ops.start(session, KONTROL, rows[5].id, at(14, 9))
    ops.finish(session, KONTROL, rows[5].id, at(14, 10))
    assert pos.status == PositionStatus.DONE.value
    assert plans.order_is_done(session, pos.order_id)


def test_sequence_and_rights_enforced(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    with pytest.raises(OperationError, match="предыдущ"):
        ops.start(session, OPERATOR, rows[1].id, at(12, 9))
    with pytest.raises(AccessDenied):
        ops.start(session, SLESAR, rows[0].id, at(12, 9))
    with pytest.raises(OperationError, match="не начата"):
        ops.finish(session, CUTTER, rows[0].id, at(12, 10))
    ops.start(session, CUTTER, rows[0].id, at(12, 9))
    with pytest.raises(OperationError, match="раньше"):
        ops.finish(session, CUTTER, rows[0].id, at(12, 8))
    with pytest.raises(OperationError, match="уже"):
        ops.start(session, CUTTER, rows[0].id, at(12, 9))
    ops.finish(session, CUTTER, rows[0].id, at(12, 11))
    with pytest.raises(OperationError, match="раньше окончания"):
        ops.start(session, OPERATOR, rows[1].id, at(12, 10))


def test_machine_busy_with_another_position(session, make_order):
    first, second = approved(session, make_order), approved(session, make_order)
    a, b = ops.position_operations(session, first.id), ops.position_operations(session, second.id)
    for rows in (a, b):
        ops.start(session, CUTTER, rows[0].id, at(12, 9))
        ops.finish(session, CUTTER, rows[0].id, at(12, 10))
    ops.start(session, OPERATOR, a[1].id, at(12, 10))
    with pytest.raises(OperationError, match="VF2 занят"):
        ops.start(session, OPERATOR, b[1].id, at(12, 10))


def test_notifications_to_master_and_next_specialist(session, make_order):
    pos = approved(session, make_order)
    rows = ops.position_operations(session, pos.id)
    for row, actor, s, e in ((rows[0], CUTTER, at(12, 9), at(12, 11)), (rows[1], OPERATOR, at(12, 11), at(12, 12)),
                             (rows[2], OPERATOR, at(12, 12), at(13, 13))):
        ops.start(session, actor, row.id, s)
        ops.finish(session, actor, row.id, e)
    master = [n.text for n in plans.list_notifications(session, MASTER)]
    assert any("Резка заготовки" in t and "выполнена" in t for t in master)
    operator = [n.text for n in plans.list_notifications(session, OPERATOR)]
    assert any("Наладка" in t and "можно начинать" in t for t in operator)
    slesar = [n.text for n in plans.list_notifications(session, SLESAR)]
    assert any("Слесарная" in t for t in slesar)
    assert not any("Резка" in t for t in slesar)


def test_existing_approved_positions_get_operations(session, make_order):
    from app.storage.orm import OperationRow
    pos = approved(session, make_order)
    session.query(OperationRow).delete()
    session.commit()
    ops.ensure_all_operations(session)
    assert len(ops.position_operations(session, pos.id)) == 6
