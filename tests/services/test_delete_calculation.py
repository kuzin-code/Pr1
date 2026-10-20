"""Удаление расчёта (позиции или заказа) до утверждения: с причиной, в историю; утверждённое не удаляется."""
from pathlib import Path

import pytest

from app.domain.access import AccessDenied
from app.domain.history import ReasonRequired
from app.services import approval_service as approval
from app.services import order_service as orders
from app.services import tech_service as tech
from app.services.actor import ServiceError
from app.storage.orm import HistoryRow, NotificationRow, OrderRow, PositionRow
from tests.services.conftest import HEAD, MASTER, OPERATOR, TECH, part_files
from tests.services.test_quick_intake import MANAGER
from app.services import intake_service as intake
from tests.services.conftest import MON


def make(session, tmp_path, names=("Кронштейн-1", "Планка-2")):
    files = [f for n in names for f in part_files(n)]
    return intake.quick_intake(session, MANAGER, files, tmp_path / "up", start_date=MON)


def test_technologist_deletes_position_with_reason(session, tmp_path):
    order = make(session, tmp_path)
    pos = order.positions[0]
    step = Path(pos.step_path)
    orders.delete_position(session, TECH, pos.id, "дубль, заказчик прислал не ту деталь")
    assert session.get(PositionRow, pos.id) is None and len(order.positions) == 1
    assert not step.exists()
    h = session.query(HistoryRow).filter_by(entity="position", field="delete").one()
    assert h.reason == "дубль, заказчик прислал не ту деталь" and h.old["designation"] == "Кронштейн-1"


def test_last_position_deletes_order(session, tmp_path):
    order = make(session, tmp_path, ("Кронштейн-1",))
    orders.delete_position(session, MASTER, order.positions[0].id, "заказ отменён")
    assert session.get(OrderRow, order.id) is None
    assert session.query(NotificationRow).count() == 0


def test_delete_whole_order(session, tmp_path):
    order = make(session, tmp_path)
    orders.delete_order(session, HEAD, order.id, "заказчик отказался")
    assert session.get(OrderRow, order.id) is None and session.query(PositionRow).count() == 0
    assert session.query(HistoryRow).filter_by(entity="order", field="delete").count() == 1


def test_reason_required_and_rights(session, tmp_path):
    order = make(session, tmp_path)
    with pytest.raises(ReasonRequired):
        orders.delete_position(session, TECH, order.positions[0].id, " ")
    with pytest.raises(AccessDenied):
        orders.delete_order(session, OPERATOR, order.id, "просто так")


def test_approved_position_cannot_be_deleted(session, tmp_path):
    order = make(session, tmp_path)
    pos = order.positions[0]
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    with pytest.raises(ServiceError, match="утвержден"):
        orders.delete_position(session, TECH, pos.id, "ошибка")
    with pytest.raises(ServiceError, match="утвержден"):
        orders.delete_order(session, HEAD, order.id, "ошибка")
