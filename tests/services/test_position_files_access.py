"""Чертёж PDF, STEP и 3D-модель позиции: специалисты открывают только позиции со своими операциями."""
import pytest

from app.domain.access import AccessDenied
from app.domain.enums import Role
from app.services import operation_service as ops
from app.services.actor import Actor
from tests.services.conftest import MASTER, OPERATOR, TECH
from tests.services.test_operation_flow import CUTTER, KONTROL, SLESAR, approved

MANAGER = Actor("manager", Role.MANAGER)
IT = Actor("it", Role.IT)
MANAGEMENT = Actor("rukovodstvo", Role.MANAGEMENT)


def test_specialists_open_files_of_positions_with_their_operations(session, make_order):
    pos = approved(session, make_order)
    for actor in (OPERATOR, CUTTER, SLESAR, KONTROL):
        ops.require_position_files(session, actor, pos.id)


def test_specialists_cannot_open_positions_without_operations(session, make_order):
    other = make_order().positions[0]  # не утверждена — операций нет
    for actor in (OPERATOR, CUTTER, SLESAR, KONTROL):
        with pytest.raises(AccessDenied):
            ops.require_position_files(session, actor, other.id)
    with pytest.raises(AccessDenied):
        ops.require_position_files(session, OPERATOR, 999999)


def test_roles_with_orders_open_any_position_others_none(session, make_order):
    other = make_order().positions[0]
    for actor in (TECH, MASTER, MANAGER):
        ops.require_position_files(session, actor, other.id)
    for actor in (IT, MANAGEMENT):
        with pytest.raises(AccessDenied):
            ops.require_position_files(session, actor, other.id)
