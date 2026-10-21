"""Не больше 100 шт. в позиции: приём заказа, список Excel, правка количества, быстрый расчёт, ступени КП."""
import pytest

from app.domain.order_intake import MAX_QUANTITY, QUANTITY_LIMIT_MESSAGE, IntakeError, validate_quantity
from app.domain.quantity_breaks import parse_quantities
from app.parsers.order_list import OrderListError, read_order_list
from app.services import order_service as orders
from app.services import quick_service as quick
from app.services.order_service import UploadedFile
from tests.fixtures.samples import make_order_list
from tests.services.conftest import MASTER, MON, part_files
from tests.services.test_quick_intake import MANAGER


def test_limit_and_message():
    assert MAX_QUANTITY == 100 and QUANTITY_LIMIT_MESSAGE == "Количество в позиции — не больше 100 шт."
    validate_quantity(1)
    validate_quantity(100)
    for bad in (0, 101, 100_000):
        with pytest.raises(IntakeError):
            validate_quantity(bad)


def test_order_list_row_over_limit(tmp_path):
    path = make_order_list(tmp_path / "z.xlsx", [("A-1", "Деталь", 5, "Д16Т"), ("A-2", "Деталь", 101, "Д16Т")])
    with pytest.raises(OrderListError, match=r"строка 9.*не больше 100 шт"):
        read_order_list(path)


def test_order_form_and_set_quantity(session, tmp_path, make_order):
    with pytest.raises(IntakeError, match="не больше 100 шт"):
        orders.create_order(session, MASTER, MON, part_files(), tmp_path / "up", quantity=101)
    pos = make_order().positions[0]
    with pytest.raises(IntakeError, match="не больше 100 шт"):
        orders.set_quantity(session, MASTER, pos.id, 150)
    assert orders.set_quantity(session, MASTER, pos.id, 100).quantity == 100


def test_quick_calc_and_edit(session, tmp_path):
    with pytest.raises(IntakeError, match="не больше 100 шт"):
        quick.calculate(session, MANAGER, part_files(), tmp_path / "work", quantity=500, start_date=MON)
    r = quick.calculate(session, MANAGER, part_files(), tmp_path / "work", quantity=5, start_date=MON)
    d = r.summary["positions"][0]["designation"]
    with pytest.raises(IntakeError, match="не больше 100 шт"):
        quick.update_position(session, MANAGER, tmp_path / "work", r.token, d, {"quantity": 101})
    assert quick.load(tmp_path / "work", r.token)["positions"][0]["quantity"] == 5


def test_quantity_breaks_limit():
    assert parse_quantities("1, 100") == [1, 100]
    with pytest.raises(ValueError, match="не больше 100 шт"):
        parse_quantities("1, 101")


def test_order_list_through_intake(session, tmp_path):
    path = make_order_list(tmp_path / "z.xlsx", [("A-1", "Деталь", 120, "Д16Т")])
    files = [UploadedFile("список.xlsx", path.read_bytes())]
    from app.services import intake_service
    with pytest.raises(OrderListError, match="строка 8.*не больше 100 шт"):
        intake_service.quick_intake(session, MANAGER, files, tmp_path / "up", start_date=MON)
