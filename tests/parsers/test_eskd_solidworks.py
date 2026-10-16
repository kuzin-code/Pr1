"""Чертежи из SolidWorks: другой порядок основной надписи, номера ТТ приклеены в конце строки,
опечатки в общих допусках, трубная резьба, допуск резьбы, склеенные «1х45» и «3 отв.»."""
from app.parsers.eskd import parse_eskd

NUT = """M16x1.0
6 отв. M2.5
*Размер для справок1
Общие допуски по ГОСТ 30893.1: Н14, hl4, 1Т14/22
Общие допуски формы и расположения - ГОСТ 30893.2-К3
Общие допуски резьбы - 6H/6g, шероховатость поверхности Ra3,24
Острые кромки притупить5
Ra 2,5
Инв. № подл.
Формат A4
Лист Листов 1
4.5 г
МасштабМассаЛит.
XWS.65.A.1.P000089
Иванов И.И.
Разраб.
Утв.
XWS.65.A.1.P000089
Файл: cadmodel-0001
Шифр: XWS
 ЛC 59-1 ГОСТ 15527-2004
Гайка конденсора
4:1
Копировал1
"""

HOLDER = """Общие допуски по ГОСТ 30893.1: Н14, h14, IТ14/22
Общие допуски резьбы - 6H/6g4
Финишная обработка: аквабластинг6
Лист Листов 1
2.5 г
МасштабМассаЛит.
XWS.65.B.P000631
Утв.
XWS.65.B.P000631
Шифр: XWS
 AW 6061 T6
Держатель лампы
5:1
"""

RADIATOR = """2 отв. G1/4
4 отв. M3
2 отв. M5
4 отв. M4х8-10/1х45 2 отв. M5х8-25/1х453 отв. M2.5х5-7/1х45
Лит. Масса Масштаб
Утв.
Формат А2Копировал
Лист 1 Листов 1
МБЦР.107.072.010
Петров П.П. 1:11048г15.01.2025
МБЦР.107.072.010
Файл: МБЦР.107.070.010 Радиатор
Радиатор
* - размеры для справок1.
Общие допуски по ГОСТ 30893.1: H12, h12, ±IT12/22.
Сплав 6061 ГОСТ 4784-97
"""


def test_designation_most_frequent_eskd_number():
    assert parse_eskd(NUT).designation == "XWS.65.A.1.P000089"
    assert parse_eskd(RADIATOR).designation == "МБЦР.107.072.010"  # из штампа, не из имени файла


def test_name_from_title_block_or_file_hint():
    assert parse_eskd(NUT).name == "Гайка конденсора"
    assert parse_eskd(RADIATOR).name == "Радиатор"
    assert parse_eskd("Лист 1\nXWS.1.2.3\n", file_stem="XWS.1.2.3 Втулка опорная").name == "Втулка опорная"


def test_material_line_with_gost_and_brand_names():
    assert parse_eskd(NUT).material == "ЛС 59-1"  # латинская «C» исправлена, стандарт не читаем
    assert parse_eskd(HOLDER).material == "AW 6061 T6"
    assert parse_eskd(RADIATOR).material == "Сплав 6061"


def test_general_tolerance_with_typos_and_trailing_item_numbers():
    nut = parse_eskd(NUT)
    assert nut.general_grade == 14  # «Н14, hl4, 1Т14/2» + номер пункта «2»
    assert "Острые кромки притупить" in nut.requirements
    assert nut.general_tolerance_class is None


def test_thread_tolerance_and_pipe_thread_are_not_fits():
    assert parse_eskd(HOLDER).tolerance_grade == 14  # «6H/6g4» — допуск резьбы + номер пункта
    assert parse_eskd(RADIATOR).tolerance_grade == 12  # «G1/4» — трубная резьба


def test_glued_counts_do_not_explode():
    r = parse_eskd(RADIATOR)
    assert r.hole_count <= 20 and r.thread_count <= 20


def test_mass_in_grams_and_scale():
    nut = parse_eskd(NUT)
    assert nut.mass_kg == 0.0045 and nut.scale == "4:1" and nut.sheet_format == "А4"
    assert parse_eskd(RADIATOR).mass_kg is None  # «1:11048г…» неоднозначно — не гадаем


def test_coating_on_next_line_and_trailing_item_number():
    text = "Общие допуски по ГОСТ 30893.1: H12, h12, ±IT12/22.\nПокрытие: 7.\nАн. Окс. бцв. (бесцветное), кроме резьбовых поверхностей\n"
    assert parse_eskd(text).coating.startswith("Ан. Окс. бцв.")
    area = "Покрытие: 7.\nАн. Окс. бцв., , кроме резьбы, площадь покрытия 7.7 \nдм²\n"
    assert parse_eskd(area).coating == "Ан. Окс. бцв., кроме резьбы, площадь покрытия 7.7 дм²"
    assert parse_eskd("Покрытие: ан. окс. бесцветный.7.\n").coating == "ан. окс. бесцветный"


def test_scale_must_be_standard_not_glued_with_mass():
    assert parse_eskd("1:14.50\n").scale is None  # «1:1» + масса «4.50»
    assert parse_eskd("Лист 1\n2:1\n").scale == "2:1"


def test_name_from_file_stem_has_priority_and_stamp_words_skipped():
    text = "МБЦР.107.000.162-01\nПодпись и дата\nМБЦР.107.000.162-01\n"
    assert parse_eskd(text, file_stem="МБЦР.107.000.162-01 Основание корпуса (1)").name == "Основание корпуса"
    assert parse_eskd(text).name is None


def test_pdf_reader_uses_eskd_fits_not_thread_tolerances():
    from app.parsers.pdf_reader import parse_drawing_text
    d = parse_drawing_text(HOLDER)
    assert d.tolerance_grade == 14 and "g4" not in d.fits
    assert parse_drawing_text(RADIATOR).tolerance_grade == 12
