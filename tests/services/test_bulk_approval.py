"""Утверждение заказа целиком: технолог передаёт все готовые позиции, начальник утверждает все ожидающие."""
import pytest

from app.domain.access import AccessDenied
from app.domain.enums import PositionStatus as S
from app.services import approval_service as approval
from app.services import operation_service as ops
from app.services import tech_service as tech
from app.services.order_service import create_order
from tests.services.conftest import HEAD, MASTER, MON, TECH, part_files


def order_of_three(session, tmp_path):
    files = part_files("Деталь-1") + part_files("Деталь-2") + [part_files("Без пары")[0]]
    return create_order(session, MASTER, MON, files, tmp_path / "u")


def test_submit_all_ready_positions(session, tmp_path):
    order = order_of_three(session, tmp_path)
    submitted, skipped = tech.submit_all(session, TECH, order.id)
    assert submitted == 2
    assert [p.status for p in order.positions].count(S.AWAITING_APPROVAL.value) == 2
    assert len(skipped) == 1 and "Без пары" in skipped[0]


def test_approve_all_in_order(session, tmp_path):
    order = order_of_three(session, tmp_path)
    tech.submit_all(session, TECH, order.id)
    assert approval.approve_all(session, HEAD, order.id) == 2
    approved = [p for p in order.positions if p.status == S.APPROVED.value]
    assert [p.approved_seq for p in approved] == [1, 2]
    assert all(ops.position_operations(session, p.id) for p in approved)  # операции созданы


def test_bulk_rights(session, tmp_path):
    order = order_of_three(session, tmp_path)
    with pytest.raises(AccessDenied):
        tech.submit_all(session, HEAD, order.id)
    with pytest.raises(AccessDenied):
        approval.approve_all(session, TECH, order.id)
    assert approval.approve_all(session, HEAD, order.id) == 0
