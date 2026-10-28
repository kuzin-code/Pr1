"""Заготовка позиции: предложение системы, правка технологом с причиной, пересчёт не затирает, припуски."""
import pytest

from app.domain.access import AccessDenied
from app.domain.blank import SYSTEM, TECHNOLOGIST
from app.domain.history import ReasonRequired
from app.services import blank_service as blanks
from app.services import tech_service as tech
from app.services.actor import ServiceError
from app.services.history_service import list_history
from app.storage.orm import PositionRow
from tests.services.conftest import HEAD, IT, OPERATOR, TECH
from tests.services.test_order_flow import prepared

SHEET = {"h": "50", "length": "160", "width": "65"}


def test_system_suggests_sheet_from_step_dims(session, make_order):
    pos = prepared(session, make_order)  # 152×58×40, Д16Т, фрезерная, 10 шт.
    blank = blanks.position_blank(session, pos)
    assert blank.source == SYSTEM
    assert blank.text == "Д16Т, лист h45 мм, 156×62 мм — 10 шт."  # 40+2 → 45 по ряду; 152+2×2, 58+2×2


def test_technologist_edit_saved_to_history_and_kept_by_recalculation(session, make_order):
    pos = prepared(session, make_order)
    blank = blanks.set_blank(session, TECH, pos.id, "sheet", SHEET, "10", "плита со склада", "на складе плита 50")
    assert blank.source == TECHNOLOGIST and blank.text == "Д16Т, лист h50 мм, 160×65 мм — 10 шт."
    (row,) = [h for h in list_history(session, entity="position", entity_id=pos.id) if h.field == "blank"]
    assert row.reason == "на складе плита 50" and "предложено системой" in row.old and "h50" in row.new

    tech.auto_recalculate(session, TECH, pos.id)  # ФТ-26: решения технолога сохраняются
    tech.update_data(session, TECH, pos.id, {"material": "АМг6"}, "уточнено")
    blank = blanks.position_blank(session, session.get(PositionRow, pos.id))
    assert blank.source == TECHNOLOGIST and blank.text == "АМг6, лист h50 мм, 160×65 мм — 10 шт."
    assert blank.note == "плита со склада"

    back = blanks.reset_blank(session, TECH, pos.id, "вернуть расчёт системы")
    assert back.source == SYSTEM and back.text.startswith("АМг6, лист h45 мм")


def test_edit_rights_and_reason(session, make_order):
    pos = prepared(session, make_order)
    with pytest.raises(AccessDenied):
        blanks.set_blank(session, HEAD, pos.id, "sheet", SHEET, 10, "", "причина")
    with pytest.raises(ReasonRequired):
        blanks.set_blank(session, TECH, pos.id, "sheet", SHEET, 10, "", " ")
    with pytest.raises(ServiceError, match="Длина L"):
        blanks.set_blank(session, TECH, pos.id, "round", {"d": "40"}, 10, "", "причина")


def test_old_position_without_blank_and_dims_opens(session, make_order):
    pos = make_order().positions[0]
    pos.data = {k: v for k, v in pos.data.items() if k not in ("dims", "blank")}
    blank = blanks.position_blank(session, pos)
    assert not blank.defined and blank.text == "Заготовка не определена, задаёт технолог"
    assert blanks.blank_text(session, pos) is None


def test_rules_edited_by_reference_editor(session, make_order):
    pos = prepared(session, make_order)
    values = {**blanks.rules(session).to_dict(), "sheet_thickness_allowance_mm": "0",
              "sheet_thicknesses_mm": "10 20 40 50"}
    with pytest.raises(AccessDenied):
        blanks.set_rules(session, HEAD, values, "причина")
    with pytest.raises(AccessDenied):
        blanks.set_rules(session, OPERATOR, values, "причина")
    blanks.set_rules(session, IT, values, "ряд по складу")
    assert blanks.rules(session).sheet_thicknesses == (10, 20, 40, 50)
    assert blanks.position_blank(session, pos).sizes["h"] == 40
    with pytest.raises(ServiceError):
        blanks.set_rules(session, TECH, {**values, "round_diameters_mm": "abc"}, "причина")
    assert any(h.field == "blank_rules" for h in list_history(session, entity="parameters"))


def test_blank_editable_after_approval(session, make_order):
    from app.services import approval_service as approval
    pos = prepared(session, make_order)
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    blank = blanks.set_blank(session, TECH, pos.id, "sheet", SHEET, 10, "", "на складе плита 50")
    assert blank.text.startswith("Д16Т, лист h50 мм")  # на стоимость не влияет — правка и после утверждения
