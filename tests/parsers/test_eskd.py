"""Чтение чертежа по ЕСКД: основная надпись (ГОСТ 2.104), ТТ (ГОСТ 2.316), допуски, шероховатость, резьбы."""
import pytest

from app.parsers.eskd import parse_eskd

# Текстовый слой реального чертежа КОМПАС (КПУ.741645.001), сокращённо.
KORPUS = """A-A ( 1 : 1 )
Инв. №подл.
Инв. №дубл.
Подп. и дата
Справ. №
Перв. примен.
КПУ.741645.001
Копировал Формат А3
Лит Масса Масштаб
ДатаПодп.№ докум.ЛистИзм.
Разраб.
Пров.
Т.контр.
Н.контр.
Утв.
Лист  1 Листов
Полиацеталь
ПОМ-С белый
 1:1
 0,098
КПУ.741645.001
Корпус
1. * Размеры для справок.
2. ** Размеры обеспечиваются инстр.
3. Неуказанные пред. откл. размеров H12, h12, ±IT12/2.
4. Остальные ТТ по ОСТ 4Г 0.070.014
26,5 `0,05
R35
4отв. M3x6-5
4 отв. n 4,5/ n 7,2x4
n 18 H9
n 23 H9
M6M6
Ra2,5
Ra3,2 ( )
f 0,05 Г
"""

STEEL = """Лист  1 Листов
Круг 30-В ГОСТ 2590-2006
45-2б-ТВ ГОСТ 1050-2013
 2:1
 0,15
АБВГ.715411.003
Вал
1. 35...40 HRC.
2. Общие допуски по ГОСТ 30893.1-m.
3. Покрытие Хим.Окс.прм.
n 20 h6
n 25 k6
Ra 0,8
Ra 6,3 ( )
M12x1,25-6g
"""


def test_title_block_fields():
    d = parse_eskd(KORPUS)
    assert d.designation == "КПУ.741645.001"
    assert d.name == "Корпус"
    assert d.material == "Полиацеталь ПОМ-С белый"
    assert d.mass_kg == 0.098 and d.scale == "1:1" and d.sheet_format == "А3"


def test_sortament_fraction_material():
    d = parse_eskd(STEEL)
    assert d.sortament == "Круг 30-В ГОСТ 2590-2006"
    assert d.material == "45-2б-ТВ"  # графа 3 — только марка, без стандарта
    assert d.blank == "круг"


def test_technical_requirements_numbered():
    d = parse_eskd(KORPUS)
    assert d.requirements[2] == "Неуказанные пред. откл. размеров H12, h12, ±IT12/2."
    assert len(d.requirements) == 4


def test_general_tolerance_from_requirements():
    assert parse_eskd(KORPUS).general_grade == 12
    assert parse_eskd(STEEL).general_tolerance_class == "m"
    assert parse_eskd(STEEL).general_grade == 14  # класс «средний» по ГОСТ 30893.1 ≈ IT14


def test_fits_with_kompas_diameter_glyph():
    d = parse_eskd(KORPUS)
    assert d.tolerance_grade == 9 and set(d.fits) >= {"H9", "H12", "h12"}
    assert parse_eskd(STEEL).tolerance_grade == 6


def test_roughness():
    assert parse_eskd(KORPUS).ra_min == 2.5
    assert parse_eskd(STEEL).ra_min == 0.8


def test_threads_and_holes_with_multiplicity():
    d = parse_eskd(KORPUS)
    assert d.threads == ["M3x6", "M6"]
    assert d.thread_count == 6  # 4 отв. M3 + 2×M6
    assert d.hole_count == 8  # 4 отв. M3 + 4 отв. Ø4,5
    assert parse_eskd(STEEL).threads == ["M12x1.25-6g"]


def test_geometric_tolerance_detected():
    assert parse_eskd(KORPUS).has_form_tolerances  # «f 0,05 Г» — допуск с базой
    assert not parse_eskd("Лист  1 Листов\nСталь 45\n 1:1\n 0,1\n").has_form_tolerances


@pytest.mark.parametrize("text,coating,heat", [
    ("1. Покрытие Хим.Окс.прм.", "Хим.Окс.прм", ""),
    ("2. Покрытие: Ан.Окс.нхр", "Ан.Окс.нхр", ""),
    ("3. Покрытие Ц9.хр", "Ц9.хр", ""),
    ("4. Эмаль ПФ-115 серая ГОСТ 6465", "Эмаль ПФ-115 серая ГОСТ 6465", ""),
    ("1. 35...40 HRC.", "", "35...40 HRC"),
    ("1. Цементировать h 0,8...1,2 мм, 58...62 HRC", "", "Цементировать h 0,8...1,2 мм, 58...62 HRC"),
    ("1. * Размеры для справок.", "", ""),
])
def test_coating_and_heat_treatment(text, coating, heat):
    d = parse_eskd(text)
    assert d.coating == coating and d.heat_treatment == heat


def test_external_operations_summary():
    assert parse_eskd(STEEL).external_operations == ["термообработка: 35...40 HRC", "покрытие: Хим.Окс.прм"]
    assert parse_eskd(KORPUS).external_operations == []
