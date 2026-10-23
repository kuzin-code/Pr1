"""Ручной приоритет срочной позиции: очередь, причина в истории, сдвиг сроков, уведомления."""
import pytest

from app.domain.access import AccessDenied
from app.domain.enums import Role
from app.domain.history import ReasonRequired
from app.services import approval_service as approval
from app.services import plan_service as plans
from app.services import priority_service as prio
from app.services import tech_service as tech
from app.services.actor import Actor
from app.services.history_service import list_history
from app.storage.orm import NotificationRow
from tests.services.conftest import HEAD, MASTER, OPERATOR, TECH, part_files
from tests.services.test_order_flow import prepared

MANAGER = Actor("manager", Role.MANAGER)


def _approved_pair(session, make_order):
    first = prepared(session, make_order)
    second = prepared(session, lambda: make_order(part_files("Кронштейн-2")))
    for pos in (first, second):
        tech.submit(session, TECH, pos.id)
        approval.approve(session, HEAD, pos.id)
    return first, second


def test_urgent_goes_first_and_shifts_reported(session, make_order):
    first, second = _approved_pair(session, make_order)
    before = plans.current_plan(session)
    assert before.positions[first.id].machine_start < before.positions[second.id].machine_start
    change = prio.set_priority(session, MASTER, second.id, 1, "заказчик просит раньше")
    after = plans.current_plan(session)
    assert after.positions[second.id].machine_start < after.positions[first.id].machine_start
    moved = {s.position_id: s for s in change.shifts}
    assert set(moved) == {first.id, second.id}
    assert moved[first.id].before < moved[first.id].after
    assert moved[second.id].after < moved[second.id].before
    h = [x for x in list_history(session, "position", second.id) if x.field == "priority"][0]
    assert (h.old, h.new, h.reason) == (0, 1, "заказчик просит раньше")


def test_notifications_to_manager_and_affected_roles(session, make_order):
    _, second = _approved_pair(session, make_order)
    prio.set_priority(session, HEAD, second.id, 1, "срочно")
    roles = {n.role for n in session.query(NotificationRow).filter_by(kind="priority")}
    assert {Role.MANAGER.value, Role.MASTER.value, Role.OPERATOR.value} <= roles
    texts = [n.text for n in plans.list_notifications(session, MANAGER)]
    assert any("приоритет" in t and "Кронштейн-2" in t for t in texts)


def test_rights_and_reason(session, make_order):
    _, second = _approved_pair(session, make_order)
    for actor in (TECH, OPERATOR, MANAGER):
        with pytest.raises(AccessDenied):
            prio.set_priority(session, actor, second.id, 1, "срочно")
    with pytest.raises(ReasonRequired):
        prio.set_priority(session, MASTER, second.id, 1, "  ")
    with pytest.raises(ValueError, match="Приоритет"):
        prio.set_priority(session, MASTER, second.id, -1, "срочно")


def test_back_to_normal_restores_order(session, make_order):
    first, second = _approved_pair(session, make_order)
    prio.set_priority(session, MASTER, second.id, 1, "срочно")
    change = prio.set_priority(session, MASTER, second.id, 0, "срочность снята")
    assert second.priority == 0
    plan = plans.current_plan(session)
    assert plan.positions[first.id].machine_start < plan.positions[second.id].machine_start
    assert change.shifts


def test_same_priority_no_shift(session, make_order):
    _, second = _approved_pair(session, make_order)
    change = prio.set_priority(session, MASTER, second.id, 0, "без изменений")
    assert change.shifts == []
