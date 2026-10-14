"""ФТ-04, О-1: чтение материала, квалитетов и допусков, резьб из чертежа PDF."""
from app.parsers.pdf_reader import parse_drawing_text, read_pdf
from tests.fixtures.samples import make_pdf

DRAWING = """
Корпус КР-01.002
Материал: Д16Т ГОСТ 4784-97
Ø20H7, 15h6, отв. 4 отв. М6-6Н, М8×1,25
Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2
10±0,05
"""


def test_material_from_label():
    assert parse_drawing_text(DRAWING).material == "Д16Т"


def test_material_by_known_list_when_no_label():
    text = "Деталь из 12Х18Н10Т, термообработка не требуется"
    assert parse_drawing_text(text, known_materials=["Д16Т", "12Х18Н10Т"]).material == "12Х18Н10Т"


def test_material_by_pattern():
    assert parse_drawing_text("Сталь 45 ГОСТ 1050").material == "Сталь 45"


def test_fits_and_best_grade():
    data = parse_drawing_text(DRAWING)
    assert "H7" in data.fits and "h6" in data.fits
    assert data.tolerance_grade == 6  # самый точный квалитет
    assert "±0,05" in data.tolerances


def test_threads_normalized():
    assert parse_drawing_text(DRAWING).threads == ["M6-6H", "M8x1.25"]


def test_thread_letter_not_taken_as_fit():
    data = parse_drawing_text("Резьба М10")
    assert data.fits == [] and data.tolerance_grade is None
    assert data.threads == ["M10"]


def test_explicit_it_grade():
    assert parse_drawing_text("Точность по IT8").tolerance_grade == 8


def test_nothing_found_lists_unread_fields():
    data = parse_drawing_text("пустой лист")
    assert data.material is None
    assert set(data.unread_fields) == {"material", "tolerance_grade"}


def test_read_real_pdf(tmp_path):
    path = tmp_path / "drawing.pdf"
    path.write_bytes(make_pdf(["Part KR-01", "Material: AMg6", "Bore 20H7", "Thread M6x1"]))
    data = read_pdf(path)
    assert data.errors == []
    assert data.material == "AMg6"
    assert data.tolerance_grade == 7
    assert data.threads == ["M6x1"]


def test_corrupted_pdf_reported(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a pdf at all")
    data = read_pdf(path)
    assert data.errors and "не читается" in data.errors[0]
    assert data.material is None
