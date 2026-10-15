"""Автоматический расчёт при загрузке; технолог только проверяет и корректирует."""
import pytest

from app.domain.enums import Mark, PositionStatus, Stage
from app.services import journal_service
from app.services import order_service as orders
from app.services import reference_service as ref
from app.services import tech_service as tech
from app.services.actor import ServiceError
from tests.fixtures.samples import make_box_step, make_pdf
from tests.services.conftest import HEAD, MASTER, TECH, part_files
from app.domain.access import AccessDenied
from app.services.order_service import UploadedFile


def plastic_part(name="КПУ.741645.001 Корпус", dims=(84, 76, 26)):
    pdf = make_pdf(["Material: Poliacetal POM-S", "Bore 18H9", "Bore 23H9", "Thread M3x6"])
    return [UploadedFile(f"{name}.stp", make_box_step(*dims).encode()), UploadedFile(f"{name}.pdf", pdf)]


def test_uploaded_position_is_calculated_automatically(session, make_order):
    pos = make_order().positions[0]
    assert pos.status == PositionStatus.TECH_REVIEW.value
    assert pos.complexity is not None
    assert pos.analog_code is not None
    assert pos.machine_code == "VF2"
    assert pos.current_calc is not None
    assert pos.data["auto"] == {"complexity": True, "analog": True, "machine": True, "route": True}


def test_new_material_gets_nearest_milling_analog_and_vf2(session, make_order):
    pos = make_order(plastic_part()).positions[0]
    assert pos.data["analog_approximate"] is True
    assert ref.journal_entry(session, pos.analog_code).technology == "milling"
    assert pos.machine_code == "VF2"
    calc = pos.current_calc
    assert Mark.APPROX_ANALOG.value in calc["marks"]
    assert calc["cost"]["batch_cost"] > 0


def test_quantity_change_recalculates(session, make_order):
    pos = make_order().positions[0]
    before = pos.current_calc["cost"]["batch_cost"]
    orders.set_quantity(session, MASTER, pos.id, 10)
    assert pos.current_calc["quantity"] == 10
    assert pos.current_calc["cost"]["batch_cost"] > before


def test_manual_corrections_are_kept_on_recalculation(session, make_order):
    pos = make_order().positions[0]
    tech.set_complexity(session, TECH, pos.id, 4, "много установов")
    tech.assign_machine(session, TECH, pos.id, "SMM")
    tech.edit_route_stage(session, TECH, pos.id, Stage.MACHINING, 1.5, "piece", "по опыту")
    orders.set_quantity(session, MASTER, pos.id, 3)
    assert pos.complexity == 4
    assert pos.machine_code == "SMM"
    assert next(s for s in pos.route if s["stage"] == "machining")["hours"] == 1.5
    assert pos.data["auto"] == {"complexity": False, "analog": True, "machine": False, "route": False}
    assert pos.current_calc["machine_code"] == "SMM"


def test_technologist_edit_triggers_recalculation(session, make_order):
    pos = make_order().positions[0]
    tech.update_data(session, TECH, pos.id, {"material": "Сталь 45"}, "уточнено")
    assert pos.current_calc is not None
    assert pos.status == PositionStatus.TECH_REVIEW.value


def test_unpaired_position_not_calculated_but_reported(session, make_order):
    order = make_order([part_files("Вал")[0]])
    pos = order.positions[0]
    assert pos.current_calc is None
    assert pos.status == PositionStatus.CALCULATION.value
    assert any("пары" in i for i in pos.data["auto_issues"])


def test_recalculate_button_rereads_files(session, make_order):
    pos = make_order().positions[0]
    pos.data = {**pos.data, "material": None}
    tech.auto_recalculate(session, TECH, pos.id)
    assert pos.data["material"] == "D16T"
    assert pos.current_calc is not None


def test_add_calculated_position_to_journal(session, make_order):
    pos = make_order(plastic_part()).positions[0]
    with pytest.raises(AccessDenied):
        journal_service.add_to_journal(session, HEAD, pos.id)
    version = ref.journal_version(session)
    entry = journal_service.add_to_journal(session, TECH, pos.id)
    assert entry.part_code == "КПУ.741645.001 Корпус"
    assert entry.technology == "milling" and entry.machine_code == "VF2"
    assert entry.stages and entry.measurements == []
    assert ref.journal_version(session) == version + 1
    with pytest.raises(ServiceError, match="уже есть"):
        journal_service.add_to_journal(session, TECH, pos.id)
    # Повторная загрузка той же детали находит её как строгий аналог.
    again = make_order(plastic_part()).positions[0]
    assert again.analog_code == "КПУ.741645.001 Корпус"
    assert again.data["analog_approximate"] is False


def test_recalculate_overwrites_read_values_but_keeps_manual_edits(session, make_order):
    pos = make_order().positions[0]
    pos.data = {**pos.data, "tolerance_grade": 8}  # значение, прочитанное старой версией
    tech.update_data(session, TECH, pos.id, {"material": "Д16Т"}, "по штампу")
    tech.auto_recalculate(session, TECH, pos.id)
    assert pos.data["tolerance_grade"] == 9  # перечитано из чертежа
    assert pos.data["material"] == "Д16Т"  # исправление технолога сохранено
    assert pos.data["edited"] == ["material"]


def test_journal_code_drops_browser_copy_suffix(session, make_order):
    pos = make_order(plastic_part("КПУ.741645.001 Корпус (1)")).positions[0]
    assert journal_service.add_to_journal(session, TECH, pos.id).part_code == "КПУ.741645.001 Корпус"
