"""Корректность файлов детали журнала: есть, читаются, относятся к этой детали."""
import pytest

from app.services import journal_service as js
from app.services.actor import ServiceError
from app.services.file_check import check_entry_files
from app.services.order_service import UploadedFile
from tests.fixtures.samples import make_box_step, make_pdf
from tests.services.conftest import TECH


def files(name, step=None, pdf_lines=("KPU.741645.001", "Material: POM")):
    return [UploadedFile(f"{name}.stp", (step or make_box_step()).encode()),
            UploadedFile(f"{name}.pdf", make_pdf(list(pdf_lines)))]


def test_missing_files_reported(session):
    status = check_entry_files(js.entry_row_by_code(session, "DEMO-01"))
    assert [(s.kind, s.ok) for s in status] == [("pdf", False), ("step", False)]
    assert status[0].message == "нет чертежа PDF"


def test_correct_files_ok(session, tmp_path):
    row = js.create_entry_from_files(session, TECH, files("КПУ.741645.001 Корпус"), tmp_path)
    status = check_entry_files(row)
    assert all(s.ok and not s.warning for s in status), status


def test_file_deleted_from_disk(session, tmp_path):
    row = js.create_entry_from_files(session, TECH, files("КПУ.741645.001 Корпус"), tmp_path)
    from pathlib import Path
    Path(row.pdf_path).unlink()
    pdf = check_entry_files(row)[0]
    assert not pdf.ok and "не найден" in pdf.message


def test_file_of_another_part_warned(session, tmp_path):
    row = js.entry_row_by_code(session, "DEMO-02")
    warnings = js.attach_files(session, TECH, row.id, files("КПУ.999.001 Чужая", pdf_lines=("KPU.999999.001",)), tmp_path)
    assert warnings and "не совпадает" in warnings[0]
    status = check_entry_files(row)
    assert all(s.ok for s in status) and any(s.warning for s in status)


def test_pdf_stamp_designation_checked(session, tmp_path):
    row = js.create_entry_from_files(session, TECH,
                                     files("КПУ.741645.001 Корпус", pdf_lines=("KPU.123456.002", "Material: POM")), tmp_path)
    pdf = check_entry_files(row)[0]
    assert pdf.ok and "741645.001" in pdf.warning


def test_corrupted_files_rejected_on_attach(session, tmp_path):
    row = js.entry_row_by_code(session, "DEMO-03")
    with pytest.raises(ServiceError, match="STEP"):
        js.attach_files(session, TECH, row.id, [UploadedFile("DEMO-03.stp", b"garbage")], tmp_path)
    with pytest.raises(ServiceError, match="PDF"):
        js.attach_files(session, TECH, row.id, [UploadedFile("DEMO-03.pdf", b"not a pdf")], tmp_path)
    assert row.step_path is None and row.pdf_path is None


def test_missing_files_counted(session, tmp_path):
    js.create_entry_from_files(session, TECH, files("КПУ.741645.001 Корпус"), tmp_path)
    assert js.entries_without_files(session) == 9  # демо-детали без файлов
