"""Чертежи из папки проекта «КД» → журнал калибровки; время не подтверждено, пока его не проверит технолог."""
from app.domain.enums import CountMode, Stage
from app.services import journal_service as js
from app.services import reference_service as ref
from app.services.auto_service import SYSTEM
from app.services.file_check import check_entry_files
from tests.fixtures.samples import make_box_step, make_pdf
from tests.services.conftest import TECH


def put(folder, stem, step=True, pdf=True, sub=""):
    target = folder / sub if sub else folder
    target.mkdir(parents=True, exist_ok=True)
    if step:
        (target / f"{stem}.stp").write_text(make_box_step(90, 50, 20))
    if pdf:
        (target / f"{stem}.pdf").write_bytes(make_pdf([stem.split()[0], "Material: D16T", "Bore 10H9"]))


def test_folder_pairs_become_unconfirmed_journal_entries(session, tmp_path):
    kd = tmp_path / "КД"
    put(kd, "KPU.500.001 Планка")
    put(kd, "KPU.500.002 Ось", sub="Токарные")
    put(kd, "KPU.500.003 Без модели", step=False)
    report = js.import_folder(session, SYSTEM, kd, tmp_path / "jf")
    assert sorted(report.created) == ["KPU.500.001 Планка", "KPU.500.002 Ось"]
    assert report.unpaired == ["KPU.500.003 Без модели.pdf"]
    row = js.entry_row_by_code(session, "KPU.500.001 Планка")
    assert row.confirmed is False
    assert all(s.ok for s in check_entry_files(row))
    assert ref.journal_entry(session, "KPU.500.001 Планка").material == "D16T"
    assert ref.journal_entry(session, "KPU.500.001 Планка").name == "Планка"  # не «KPU.500» из точек обозначения


def test_reimport_skips_known_and_takes_new(session, tmp_path):
    kd = tmp_path / "КД"
    put(kd, "KPU.500.001 Планка")
    js.import_folder(session, SYSTEM, kd, tmp_path / "jf")
    put(kd, "KPU.500.004 Втулка")
    report = js.import_folder(session, SYSTEM, kd, tmp_path / "jf")
    assert report.created == ["KPU.500.004 Втулка"] and report.skipped == ["KPU.500.001 Планка"]


def test_existing_entry_without_files_gets_them(session, tmp_path):
    kd = tmp_path / "КД"
    put(kd, "DEMO-05 Фланец")
    report = js.import_folder(session, SYSTEM, kd, tmp_path / "jf")
    assert report.files_attached == ["DEMO-05"]
    assert all(s.ok for s in check_entry_files(js.entry_row_by_code(session, "DEMO-05")))


def test_saving_times_confirms_entry(session, tmp_path):
    kd = tmp_path / "КД"
    put(kd, "KPU.500.001 Планка")
    js.import_folder(session, SYSTEM, kd, tmp_path / "jf")
    row = js.entry_row_by_code(session, "KPU.500.001 Планка")
    js.set_stage_times(session, TECH, row.id, {Stage.MACHINING: (0.4, CountMode.PER_PIECE)}, "хронометраж")
    assert row.confirmed is True
    other = js.entry_row_by_code(session, "DEMO-01")
    js.confirm_times(session, TECH, other.id)
    assert other.confirmed is True


def test_missing_folder_reported(session, tmp_path):
    report = js.import_folder(session, SYSTEM, tmp_path / "нет", tmp_path / "jf")
    assert report.errors and "не найдена" in report.errors[0]


def test_browser_copy_suffixes_and_solidworks_files(session, tmp_path):
    kd = tmp_path / "КД"
    kd.mkdir()
    (kd / "KPU.700.001 Крышка (1).stp").write_text(make_box_step(90, 50, 20))
    (kd / "KPU.700.001 Крышка (2).PDF").write_bytes(make_pdf(["KPU.700.001", "Material: D16T"]))
    (kd / "KPU.700.002 Изолятор.SLDPRT").write_bytes(b"solidworks")
    (kd / "KPU.700.002 Изолятор.pdf").write_bytes(make_pdf(["KPU.700.002"]))
    report = js.import_folder(session, SYSTEM, kd, tmp_path / "jf")
    assert report.created == ["KPU.700.001 Крышка"]
    assert any("KPU.700.002 Изолятор.SLDPRT" in n and "STEP" in n for n in report.solidworks)
    assert "KPU.700.002 Изолятор.pdf" in report.unpaired
