"""Справочник оборудования: добавление станка, правка характеристик, подсветка пробелов (Д-5)."""
import pytest

from app.domain.access import AccessDenied
from app.services import reference_service as ref
from app.services.actor import ServiceError
from app.services.history_service import list_history
from tests.services.conftest import IT, TECH


def test_gaps_show_missing_characteristics(session):
    gaps = ref.machine_gaps(ref.get_machine(session, "MV6"))
    assert "Точность" in gaps and "Мощность" in gaps and "Обороты шпинделя, об/мин" in gaps
    assert "Рабочая зона, мм" not in gaps
    saw_gaps = ref.machine_gaps(ref.get_machine(session, "BS275"))
    assert "Рабочая зона, мм" not in saw_gaps and "Обороты шпинделя, об/мин" not in saw_gaps


def test_it_updates_characteristics_with_history(session):
    ref.update_machine(session, IT, "MV6", {
        "work_zone": "650 x 420 x 450", "accuracy": "±0,005 мм", "power": "11 кВт", "axes": "3",
        "spindle_rpm": "10000", "tool_count": "24", "max_weight_kg": "350", "table_size": "800×420",
        "inventory_number": "ИН-0042", "controller": "Fanuc 0i-MF"}, "паспорт станка")
    m = ref.get_machine(session, "MV6")
    assert m.work_zone == [650, 420, 450]
    assert (m.axes, m.spindle_rpm, m.tool_count, m.max_weight_kg) == (3, 10000, 24, 350)
    assert ref.machine_gaps(m) == []
    assert any(h.field == "work_zone" and h.reason == "паспорт станка"
               for h in list_history(session, entity="machine", entity_id="MV6"))


def test_new_work_zone_changes_allowed_machines(session):
    from app.services.reference_service import domain_machines
    ref.update_machine(session, IT, "SMM", {"work_zone": "500 x 400 x 300"}, "уточнено")
    smm = next(m for m in domain_machines(session) if m.code == "SMM")
    assert smm.work_zone.sorted() == (500, 400, 300)


def test_add_new_machine(session):
    ref.create_machine(session, IT, {"code": "DMU50", "model": "DMG Mori DMU 50", "kind": "5-осевой фрезерный",
                                     "is_cnc": True, "technologies": "milling", "work_zone": "650x520x475",
                                     "hours_per_day": "6"})
    assert ref.get_machine(session, "DMU50").technologies == ["milling"]
    with pytest.raises(ServiceError, match="уже есть"):
        ref.create_machine(session, IT, {"code": "DMU50", "model": "x", "kind": "x"})


def test_validation(session):
    with pytest.raises(ServiceError, match="зона"):
        ref.update_machine(session, IT, "VF2", {"work_zone": "762 x abc"}, "")
    with pytest.raises(ServiceError):
        ref.update_machine(session, IT, "VF2", {"spindle_rpm": "-5"}, "")
    with pytest.raises(ServiceError, match="технолог"):
        ref.update_machine(session, IT, "VF2", {"technologies": "welding"}, "")


def test_technologist_and_it_edit_equipment_others_cannot(session):
    from tests.services.conftest import HEAD, MASTER
    ref.update_machine(session, TECH, "VF2", {"power": "22,4 кВт", "axes": "3"}, "паспорт")
    assert ref.get_machine(session, "VF2").axes == 3
    ref.create_machine(session, TECH, {"code": "X1", "model": "Новый", "kind": "фрезерный", "is_cnc": True,
                                       "technologies": "milling", "work_zone": "500x400x300"})
    for actor in (HEAD, MASTER):
        with pytest.raises(AccessDenied):
            ref.update_machine(session, actor, "VF2", {"power": "1"}, "")
