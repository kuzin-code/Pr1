"""Второй станок — часть техпроцесса конкретной детали журнала (уточнение пользователя 29.10.2026):
та же деталь получает его в позицию, аналог по похожим — нет; ручные станки технолога не затираются."""
import pytest

from app.domain.enums import CountMode, Stage
from app.domain.models import CalibrationEntry, Dimensions, StageNorm
from app.services import journal_service as js
from app.services import reference_service as ref
from app.services import tech_service as tech
from app.services.actor import ServiceError
from app.services.history_service import list_history
from app.storage.convert import entry_to_row
from tests.services.conftest import TECH, part_files

CODE = "XWS.65.A.1.P001040 Оправа зеркала"
DIMS = (90, 50, 20)


def _add_part(session, code=CODE, extra=True):
    norms = [StageNorm(Stage.SETUP, 0.6, CountMode.PER_BATCH), StageNorm(Stage.MACHINING, 0.3, CountMode.PER_PIECE),
             StageNorm(Stage.LOCKSMITH, 0.25, CountMode.PER_PIECE)]
    entry = CalibrationEntry(code, "Оправа зеркала", "VF2", "Д16Т", Dimensions(*DIMS), 12, 2, norms)
    if extra:
        entry.extra_machines = [{"machine": "SMM", "setup": 0.7, "machining": 0.2}]
    row = entry_to_row(entry)
    session.add(row)
    session.commit()
    return row


def test_entry_keeps_second_machine(session):
    row = _add_part(session)
    entry = ref.journal_entry(session, CODE)
    assert entry.extra_machines == [{"machine": "SMM", "setup": 0.7, "machining": 0.2}]
    assert row.machine_code == "VF2"


def test_old_entries_without_second_machine_work(session, make_order):
    entry = ref.journal_entry(session, "DEMO-05")
    assert entry.extra_machines == []
    pos = make_order().positions[0]
    assert not pos.data.get("extra_machines") and pos.current_calc


def test_technologist_sets_changes_and_removes_second_machine_with_reason(session):
    row = _add_part(session, extra=False)
    with pytest.raises(ValueError, match="причин"):
        js.set_extra_machine(session, TECH, row.id, "SMM", 0.7, 0.2, "")
    js.set_extra_machine(session, TECH, row.id, "SMM", 0.7, 0.2, "по техпроцессу")
    assert ref.journal_entry(session, CODE).extra_machines[0] == \
        {"machine": "SMM", "technology": "milling", "setup": 0.7, "machining": 0.2}
    js.set_extra_machine(session, TECH, row.id, "SMM", 0.8, 0.25, "хронометраж")
    assert ref.journal_entry(session, CODE).extra_machines[0]["setup"] == 0.8
    with pytest.raises(ServiceError):
        js.set_extra_machine(session, TECH, row.id, "VF2", 1, 1, "основной")  # уже основной станок
    with pytest.raises(ServiceError):
        js.set_extra_machine(session, TECH, row.id, "BS275", 1, 1, "не ЧПУ")
    with pytest.raises(ServiceError):
        js.set_extra_machine(session, TECH, row.id, "SMM", -1, 1, "минус")
    js.set_extra_machine(session, TECH, row.id, None, 0, 0, "станок не нужен")
    assert ref.journal_entry(session, CODE).extra_machines == []
    reasons = [h.reason for h in list_history(session, entity="journal", entity_id=CODE) if h.field == "extra_machine"]
    assert {"по техпроцессу", "хронометраж", "станок не нужен"} <= set(reasons)


def test_same_part_gets_second_machine_with_journal_time(session, make_order):
    _add_part(session)
    pos = make_order(part_files(CODE, dims=DIMS)).positions[0]
    assert pos.analog_code == CODE
    extra = pos.data["extra_machines"]
    assert [(e["machine"], e["setup"], e["machining"]) for e in extra] == [("SMM", 0.7, 0.2)]
    assert extra[0]["source"] == "journal"
    assert "техпроцесс из журнала: второй станок SMM" in pos.data["auto_issues"]
    hours = {(t["stage"], t.get("machine")): t["planned_hours"] for t in pos.current_calc["stage_times"]}
    assert hours[("setup", "SMM")] == pytest.approx(0.7)
    assert hours[("machining", "SMM")] == pytest.approx(0.2 * pos.quantity)
    assert hours[("setup", None)] == pytest.approx(0.6)  # время основного станка не складывается со вторым


def test_similar_part_does_not_get_second_machine(session, make_order):
    _add_part(session)
    pos = make_order(part_files("Другая оправа-7", dims=DIMS)).positions[0]
    assert not pos.data.get("extra_machines")
    assert not any("второй станок" in i for i in pos.data["auto_issues"])


def test_technologist_machines_are_not_overwritten(session, make_order):
    _add_part(session)
    pos = make_order(part_files(CODE, dims=DIMS)).positions[0]
    tech.set_extra_machine_time(session, TECH, pos.id, "SMM", 1.0, 0.5, "по опыту")
    tech.auto_recalculate(session, TECH, pos.id)
    assert [(e["machine"], e["setup"], e["machining"]) for e in pos.data["extra_machines"]] == [("SMM", 1.0, 0.5)]
    tech.remove_machine(session, TECH, pos.id, "SMM")
    tech.auto_recalculate(session, TECH, pos.id)
    assert not pos.data.get("extra_machines")


def test_main_machine_set_to_second_one_drops_duplicate(session, make_order):
    _add_part(session)
    pos = make_order(part_files(CODE, dims=DIMS)).positions[0]
    tech.assign_machine(session, TECH, pos.id, "SMM")
    assert "SMM" not in [e["machine"] for e in pos.data.get("extra_machines") or []]


def test_import_excel_keeps_second_machine(session, tmp_path):
    from app.parsers.journal_excel import export_journal
    _add_part(session)
    path = tmp_path / "j.xlsx"
    export_journal(ref.journal_entries(session), path)
    ref.import_journal_file(session, TECH, path, replace=True)
    extra = ref.journal_entry(session, CODE).extra_machines
    assert [(e["machine"], e["setup"], e["machining"]) for e in extra] == [("SMM", 0.7, 0.2)]
