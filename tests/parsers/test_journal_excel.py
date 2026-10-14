"""ФТ-25, Д-4: загрузка журнала калибровки из Excel."""
import pytest
from openpyxl import Workbook

from app.domain.enums import CountMode, Stage, StageGroup
from app.parsers.journal_excel import (
    JOURNAL_SHEET,
    JournalFormatError,
    export_journal,
    import_journal,
    journal_headers,
)
from tests.domain.conftest import machine_measurements, make_entry


def test_roundtrip_nine_parts(tmp_path):
    journal = [make_entry(f"P-{i}", dims=(100 + i, 60, 20), measurements=machine_measurements(1.1))
               for i in range(9)]
    path = tmp_path / "journal.xlsx"
    export_journal(journal, path)
    loaded = import_journal(path)
    assert len(loaded) == 9
    first = loaded[0]
    assert first.part_code == "P-0"
    assert first.dims.as_list() == [100, 60, 20]
    assert first.stages == journal[0].stages
    assert first.measurements[0].group is StageGroup.MACHINE
    assert first.measurements[0].ratio == pytest.approx(1.1)


def test_empty_stage_hours_mean_stage_absent(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = JOURNAL_SHEET
    headers = journal_headers()
    ws.append(headers)
    row = {h: None for h in headers}
    row.update({"Обозначение": "X-1", "Наименование": "Втулка", "Станок": "ST20Y", "Материал": "Сталь 45",
                "Габарит 1, мм": 40, "Габарит 2, мм": 40, "Габарит 3, мм": 60, "Квалитет": 7,
                "Балл сложности": 2, "Технология": "turning",
                "Обработка на станке, ч": 0.4, "Обработка на станке, счёт": "штука",
                "Наладка, ч": 1, "Наладка, счёт": "партия"})
    ws.append([row[h] for h in headers])
    path = tmp_path / "j.xlsx"
    wb.save(path)
    (entry,) = import_journal(path)
    assert [s.stage for s in entry.stages] == [Stage.SETUP, Stage.MACHINING]
    assert entry.stages[1].count_mode is CountMode.PER_PIECE
    assert entry.technology == "turning"


def test_missing_column_error(tmp_path):
    wb = Workbook()
    wb.active.title = JOURNAL_SHEET
    wb.active.append(["Обозначение"])
    path = tmp_path / "j.xlsx"
    wb.save(path)
    with pytest.raises(JournalFormatError, match="Нет столбцов"):
        import_journal(path)


def test_bad_row_reports_row_number(tmp_path):
    path = tmp_path / "j.xlsx"
    export_journal([make_entry()], path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    wb[JOURNAL_SHEET]["E2"] = "много"
    wb.save(path)
    with pytest.raises(JournalFormatError, match="строка 2"):
        import_journal(path)
