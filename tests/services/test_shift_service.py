"""Сменное задание на станок: из текущего плана, права специалистов."""
from datetime import date

import pytest

from app.domain.access import AccessDenied
from app.services import plan_service as plans
from app.services import shift_service as shifts
from tests.services.conftest import HEAD, MASTER, MGMT, OPERATOR, TECH
from tests.services.test_operation_flow import CUTTER, approved
from tests.services.test_order_flow import prepared


def test_shift_from_current_plan(session, make_order):
    pos = approved(session, make_order)
    slot = next(s for s in plans.current_plan(session).slots if s.position_id == pos.id)
    machine, shift, positions = shifts.shift_for(session, MASTER, "VF2", slot.start.date())
    assert machine.code == "VF2" and shift.lines
    assert [l.op.operation for l in shift.lines][:2] == ["setup", "milling"]
    assert all(shift.start <= l.start < l.end <= shift.end for l in shift.lines)
    assert positions[pos.id].designation == pos.designation
    assert shift.hours <= 6


def test_preliminary_position_one_line(session, make_order):
    pos = prepared(session, make_order)
    slot = next(s for s in plans.current_plan(session).slots if s.position_id == pos.id)
    _, shift, _ = shifts.shift_for(session, HEAD, "VF2", slot.start.date())
    assert [l.op.operation for l in shift.lines] == ["machine"] and shift.lines[0].preliminary


def test_rights(session, make_order):
    approved(session, make_order)
    day = date(2026, 10, 12)
    for actor in (TECH, MASTER, HEAD, MGMT, OPERATOR):
        shifts.shift_for(session, actor, "VF2", day)
    with pytest.raises(AccessDenied):
        shifts.shift_for(session, CUTTER, "VF2", day)  # пила, а не фрезерный станок
