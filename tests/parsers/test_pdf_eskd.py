"""Чертёж ЕСКД: материал в основной надписи без слова «Материал»; радиусы — не посадки."""
from app.parsers.pdf_reader import parse_drawing_text

# Фрагменты текстового слоя реальных чертежей (КОМПАС, формат А3/А4).
KORPUS = """A-A ( 1 : 1 )
Инв. №подл.
КПУ.741645.001
Копировал Формат А3
Лит Масса Масштаб
ДатаПодп.№ докум.ЛистИзм.
Разраб.
Утв.
Лист  1 Листов
Полиацеталь
ПОМ-С белый
 1:1
 0,098
КПУ.741645.001
Корпус
3. Неуказанные пред. откл. размеров H12, h12, ±IT12/2.
26,5 `0,05
R35
R8
4отв. M3x6-5
n 18 H9
n 23 H9
M6M6
R0,4**
"""

PRIZHIM = """Лит Масса Масштаб
Лист  1 Листов
Полиацеталь
ПОМ-С белый
 4:1
 0,001
КПУ.711141.001
Прижим
1. Неуказанные пред. откл. размеров H12, h12, ±IT12/2.
3отв. n 3,4/ n 6,6x90°
"""


def test_material_from_title_block():
    assert parse_drawing_text(KORPUS).material == "Полиацеталь ПОМ-С белый"
    assert parse_drawing_text(PRIZHIM).material == "Полиацеталь ПОМ-С белый"


def test_mass_from_title_block():
    assert parse_drawing_text(KORPUS).mass_kg == 0.098
    assert parse_drawing_text(PRIZHIM).mass_kg == 0.001


def test_radius_is_not_a_fit():
    data = parse_drawing_text(KORPUS)
    assert "R8" not in data.fits
    assert data.tolerance_grade == 9


def test_general_tolerance_grade_when_no_fits():
    assert parse_drawing_text(PRIZHIM).tolerance_grade == 12


def test_threads_from_eskd_drawing():
    assert parse_drawing_text(KORPUS).threads == ["M3x6", "M6"]


def test_plastic_material_without_title_block():
    assert parse_drawing_text("Деталь: Капролон ПА6 блочный").material.startswith("Капролон")
