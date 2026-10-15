"""Ведение журнала калибровки технологом в приложении: файлы, характеристики, время операций, замеры."""
import pytest

from app.domain.access import AccessDenied
from app.domain.enums import CountMode, Stage, StageGroup
from app.services import journal_service as js
from app.services import reference_service as ref
from app.services.actor import ServiceError
from app.services.history_service import list_history
from tests.services.conftest import HEAD, IT, TECH, part_files
from tests.services.test_auto_flow import plastic_part


@pytest.fixture
def storage(tmp_path):
    return tmp_path / "journal_files"


def test_new_entry_from_step_and_pdf_is_prefilled(session, storage):
    row = js.create_entry_from_files(session, TECH, plastic_part("КПУ.100.001 Стойка", dims=(60, 40, 30)), storage)
    entry = ref.journal_entry(session, "КПУ.100.001 Стойка")
    assert sorted(entry.dims.as_list(), reverse=True) == [60, 40, 30]
    assert entry.material == "Poliacetal POM-S"
    assert entry.tolerance_grade == 9 and entry.complexity == 3
    assert entry.technology == "milling" and entry.machine_code == "VF2"
    assert entry.stages and all(s.hours > 0 for s in entry.stages if s.stage is Stage.MACHINING)
    assert row.step_path and row.pdf_path and row.step_name.endswith(".stp")


def test_only_technologist_edits_journal(session, storage):
    with pytest.raises(AccessDenied):
        js.create_entry_from_files(session, HEAD, plastic_part(), storage)
    with pytest.raises(AccessDenied):
        js.create_entry_from_files(session, IT, plastic_part(), storage)


def test_duplicate_designation_rejected(session, storage):
    js.create_entry_from_files(session, TECH, plastic_part(), storage)
    with pytest.raises(ServiceError, match="уже есть"):
        js.create_entry_from_files(session, TECH, plastic_part(), storage)


def test_update_characteristics_with_history(session):
    row = js.entry_row_by_code(session, "DEMO-05")
    version = ref.journal_version(session)
    js.update_entry(session, TECH, row.id, {"name": "Фланец Ду50", "material": "Сталь 20", "dims": [165, 165, 22],
                                            "tolerance_grade": 7, "complexity": 4, "machine_code": "VF2"}, "по чертежу")
    entry = ref.journal_entry(session, "DEMO-05")
    assert (entry.name, entry.material, entry.tolerance_grade, entry.complexity, entry.machine_code) == \
        ("Фланец Ду50", "Сталь 20", 7, 4, "VF2")
    assert ref.journal_version(session) == version + 1
    assert any(h.field == "material" and h.reason == "по чертежу"
               for h in list_history(session, entity="journal", entity_id="DEMO-05"))


def test_unknown_machine_rejected(session):
    row = js.entry_row_by_code(session, "DEMO-05")
    with pytest.raises(ServiceError, match="Станок"):
        js.update_entry(session, TECH, row.id, {"machine_code": "XXX"}, "")


def test_set_time_for_each_operation(session):
    row = js.entry_row_by_code(session, "DEMO-01")
    js.set_stage_times(session, TECH, row.id, {
        Stage.CUTTING: (0.2, CountMode.PER_PIECE), Stage.PROGRAMMING: (1.5, CountMode.ONCE),
        Stage.SETUP: (0.7, CountMode.PER_BATCH), Stage.MACHINING: (0.45, CountMode.PER_PIECE),
        Stage.LOCKSMITH: (None, CountMode.PER_PIECE), Stage.FINISHING: (0.1, CountMode.PER_PIECE),
        Stage.INSPECTION: (0.25, CountMode.PER_BATCH)}, "хронометраж")
    stages = {s.stage: s for s in ref.journal_entry(session, "DEMO-01").stages}
    assert stages[Stage.MACHINING].hours == 0.45
    assert Stage.LOCKSMITH not in stages  # пустое время — этапа нет в маршруте


def test_negative_time_rejected(session):
    row = js.entry_row_by_code(session, "DEMO-01")
    with pytest.raises(ServiceError):
        js.set_stage_times(session, TECH, row.id, {Stage.MACHINING: (-1, CountMode.PER_PIECE)}, "")


def test_manual_measurement_trains_k(session):
    row = js.entry_row_by_code(session, "DEMO-01")
    js.add_manual_measurement(session, TECH, row.id, StageGroup.MACHINE, calc_hours=2.0, actual_hours=2.5)
    from app.domain.calibration import k_for
    assert k_for(ref.journal_entry(session, "DEMO-01"), StageGroup.MACHINE).value == pytest.approx(1.25)
    with pytest.raises(ServiceError):
        js.add_manual_measurement(session, TECH, row.id, StageGroup.MACHINE, 2.0, 0)


def test_attach_files_to_existing_entry(session, storage):
    row = js.entry_row_by_code(session, "DEMO-02")
    js.attach_files(session, TECH, row.id, part_files("DEMO-02"), storage)
    assert row.step_path and row.pdf_path
    with pytest.raises(ServiceError, match="STEP"):
        js.attach_files(session, TECH, row.id, [part_files("x")[0].__class__("a.docx", b"1")], storage)


def test_delete_entry(session):
    row = js.entry_row_by_code(session, "DEMO-09")
    js.delete_entry(session, TECH, row.id, "демо-деталь")
    assert "DEMO-09" not in [e.part_code for e in ref.journal_entries(session)]
