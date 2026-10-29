"""Разовая миграция 29.10.2026: запись журнала «… (SMM)» → второй станок основной записи."""
import importlib.util
from pathlib import Path

from app.domain.enums import CountMode, Stage
from app.domain.models import CalibrationEntry, Dimensions, StageNorm
from app.services import reference_service as ref
from app.services.history_service import list_history
from app.storage.convert import entry_to_row

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_second_machine.py"
spec = importlib.util.spec_from_file_location("migrate_second_machine", SCRIPT)
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)


def _entries(session, uploads: Path, same_files=True):
    main = [StageNorm(Stage.SETUP, 0.6, CountMode.PER_BATCH), StageNorm(Stage.MACHINING, 0.3, CountMode.PER_PIECE)]
    second = [StageNorm(Stage.SETUP, 0.7, CountMode.PER_BATCH), StageNorm(Stage.MACHINING, 0.2, CountMode.PER_PIECE)]
    rows = [entry_to_row(CalibrationEntry(mig.MAIN, "Оправа зеркала", "VOLZH1", "Д16Т", Dimensions(90, 50, 20), 12, 2,
                                          main)),
            entry_to_row(CalibrationEntry(mig.SECOND, "Оправа зеркала", "SMM", "Д16Т", Dimensions(90, 50, 20), 12, 2,
                                          second))]
    session.add_all(rows)
    session.commit()
    for row, content in zip(rows, (b"PDF", b"PDF" if same_files else b"other")):
        folder = uploads / f"entry_{row.id}"
        folder.mkdir(parents=True)
        (folder / "Оправа.pdf").write_bytes(content)
    return rows


def test_second_entry_becomes_second_machine(session, tmp_path):
    main, second = _entries(session, tmp_path)
    report = mig.migrate(session, tmp_path)
    assert ref.journal_entry(session, mig.MAIN).extra_machines == [
        {"machine": "SMM", "technology": "milling", "setup": 0.7, "machining": 0.2}]
    assert mig.SECOND not in {e.part_code for e in ref.journal_entries(session)}
    assert not (tmp_path / f"entry_{second.id}").exists() and (tmp_path / f"entry_{main.id}").exists()
    assert any("удалена" in line for line in report)
    history = list_history(session, entity="journal")
    assert {(h.entity_id, h.field) for h in history if h.reason == mig.REASON} >= {
        (mig.MAIN, "extra_machine"), (mig.SECOND, "delete")}
    assert mig.migrate(session, tmp_path) and ref.journal_entry(session, mig.MAIN).extra_machines[0]["setup"] == 0.7


def test_different_files_are_kept(session, tmp_path):
    _, second = _entries(session, tmp_path, same_files=False)
    mig.migrate(session, tmp_path)
    assert (tmp_path / f"entry_{second.id}" / "Оправа.pdf").exists()
