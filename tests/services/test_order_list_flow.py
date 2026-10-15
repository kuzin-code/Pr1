"""Приём заказа списком деталей (Excel по шаблону) с файлами STEP/PDF (ФТ-32)."""
from datetime import date

import pytest

from app.domain.access import AccessDenied
from app.domain.order_intake import match_files
from app.services import order_service as orders
from app.services.order_service import UploadedFile
from tests.fixtures.samples import make_box_step, make_order_list, make_pdf
from tests.services.conftest import MASTER, MON, TECH

ITEMS = [
    ("MBIS.AI.11000.19", "Обшивка боковая", 7, "Д16Т ГОСТ 4784-2019"),
    ("MBIS.AI.11000.19-01", "Обшивка боковая", "1", "Д16Т ГОСТ 4784-2019", "Анодирование", "по RAL заказчика"),
    ("MBIS.AI.18000.01", "Основание", 12, "Д16Т ГОСТ 4784-2019"),
]


def files(stem, pdf_lines=("Bore 10H9",)):
    return [UploadedFile(f"{stem}.step", make_box_step(150, 60, 40).encode()),
            UploadedFile(f"{stem}.pdf", make_pdf(list(pdf_lines)))]


def list_file(tmp_path, items=ITEMS, **kw):
    path = make_order_list(tmp_path / "Заказ.xlsx", items, sections={1: "Детали из алюминия"}, **kw)
    return UploadedFile("Заказ.xlsx", path.read_bytes())


def test_match_files_by_designation_prefix():
    matched, unmatched = match_files(
        ["MBIS.AI.11000.19", "MBIS.AI.11000.19-01"],
        ["MBIS.AI.11000.19 Обшивка боковая.step", "mbis.ai.11000.19-01.PDF", "MBIS.AI.11000.19-01_rev2.stp",
         "Чужая.pdf"])
    assert matched == {"MBIS.AI.11000.19": {"step": "MBIS.AI.11000.19 Обшивка боковая.step"},
                       "MBIS.AI.11000.19-01": {"pdf": "mbis.ai.11000.19-01.PDF",
                                               "step": "MBIS.AI.11000.19-01_rev2.stp"}}
    assert unmatched == ["Чужая.pdf"]


def test_order_from_list_with_files(session, tmp_path):
    order, warnings = orders.create_order_from_list(
        session, MASTER, MON, list_file(tmp_path),
        files("MBIS.AI.11000.19 Обшивка боковая") + files("MBIS.AI.11000.19-01"), tmp_path / "uploads")
    assert order.customer == "ООО «Тест»" and order.customer_order_date == date(2026, 3, 12)
    assert order.contacts["Ответственный"] == "Петров П. П."
    first, second, third = order.positions
    assert (first.designation, first.quantity) == ("MBIS.AI.11000.19 Обшивка боковая", 7)
    assert first.data["section"] == "Детали из алюминия"
    assert first.current_calc is not None  # рассчитана автоматически
    assert first.data["material"] == "Д16Т ГОСТ 4784-2019"  # в чертеже материала нет — из списка
    assert second.quantity == 1 and second.cooperation  # анодирование — внешняя операция
    assert "Анодирование" in second.data["coating"] and second.data["note"] == "по RAL заказчика"
    assert third.needs_link and third.current_calc is None
    assert any("прикрепите" in i for i in third.data["auto_issues"])
    assert warnings == []


def test_list_material_differs_from_drawing(session, tmp_path):
    order, _ = orders.create_order_from_list(
        session, MASTER, MON, list_file(tmp_path, ITEMS[:1]),
        files("MBIS.AI.11000.19", pdf_lines=("Material: AMg6", "Bore 10H9")), tmp_path / "uploads")
    pos = order.positions[0]
    assert pos.data["material"] == "AMg6"  # чертёж — главный документ
    assert any("отличается" in i for i in pos.data["auto_issues"])


def test_unmatched_files_reported(session, tmp_path):
    _, warnings = orders.create_order_from_list(
        session, MASTER, MON, list_file(tmp_path, ITEMS[:1]), files("Лишняя деталь"), tmp_path / "uploads")
    assert any("Лишняя деталь" in w for w in warnings)


def test_list_rights_and_limits(session, tmp_path):
    with pytest.raises(AccessDenied):
        orders.create_order_from_list(session, TECH, MON, list_file(tmp_path), [], tmp_path / "u")
    many = [(f"D-{i}", "Деталь", 1, "Д16Т") for i in range(51)]
    from app.domain.order_intake import OrderTooLargeError
    with pytest.raises(OrderTooLargeError):
        orders.create_order_from_list(session, MASTER, MON, list_file(tmp_path, many), [], tmp_path / "u")


def test_attach_files_to_position_later(session, tmp_path):
    order, _ = orders.create_order_from_list(session, MASTER, MON, list_file(tmp_path, ITEMS[2:]), [],
                                             tmp_path / "uploads")
    pos = order.positions[0]
    orders.attach_position_files(session, TECH, pos.id, files("MBIS.AI.18000.01"), tmp_path / "uploads")
    assert not pos.needs_link
    assert pos.current_calc is not None
    assert pos.quantity == 12 and pos.data["section"] == "Детали из алюминия"  # данные списка сохранены
