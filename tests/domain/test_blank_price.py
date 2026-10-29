"""Цена заготовки по прайсу поставщика (prutki.ru): соответствие марок, подбор куска, формула (n + 3 на брак)."""
import pytest

from app.domain.blank import SYSTEM, TECHNOLOGIST, Blank, BlankRules, suggest_blank
from app.domain.models import Dimensions
from app.domain.blank_price import (
    DEFAULT_RULES,
    PriceItem,
    PriceList,
    PriceRules,
    RulesError,
    blank_cost,
    fits,
    per_piece,
    plural,
    grades_text,
    parse_grades,
    site_grade,
)

RULES = PriceRules.from_dict(DEFAULT_RULES)


def item(code, form, grade, dims, price, stock=50, adjective="алюминиевый"):
    name = f"Заготовка {form} {adjective} {grade} {' х '.join(f'{d:g}' for d in dims)} мм"
    return PriceItem(str(code), name, form, grade, tuple(dims), price, stock)


PRICE = PriceList("2026-10-26", 22.0, (
    item(1, "плита", "Д16Т", (50, 100, 200), 3290),
    item(2, "плита", "Д16Т", (60, 100, 200), 3900),
    item(3, "лист", "Д16Т", (50, 200, 100), 3500),  # тот же размер дороже — берётся дешёвый
    item(4, "плита", "Д16Т", (18, 50, 100), 420),
    item(5, "плита", "Д16Т", (12, 50, 100), 380),  # тоньше заготовки — не подходит
    item(6, "пруток", "Д16Т", (60, 100), 900),
    item(7, "круг", "12Х18Н10Т", (60, 100), 2100, adjective="нержавеющий"),
    item(8, "круг", "AISI 321 (12Х18Н10Т)", (60, 100), 1900, stock=2, adjective="нержавеющий"),
    item(9, "шестигранник", "AISI 304", (24, 100), 700, adjective="нержавеющий"),
    item(10, "лист", "AISI 304", (10, 100, 200), 1500, adjective="нержавеющий"),
    item(11, "лист", "Д16АТ", (50, 100, 200), 100),  # другая марка
))


def blank(material, kind, sizes, quantity=10, source=SYSTEM):
    return Blank(material, kind, sizes, quantity, source)


@pytest.mark.parametrize("material,kind,grade", [
    ("AW 6061 T6", "sheet", "Д16Т"), ("Сплав 6082", "sheet", "Д16Т"), ("Д16Т ГОСТ 4784-97", "round", "Д16Т"),
    ("D16T", "sheet", "Д16Т"), ("АМг6", "sheet", "АМг6"),
    ("12Х18Н10Т", "round", "12Х18Н10Т"), ("AISI 304", "round", "12Х18Н10Т"), ("12X18H10T", "sheet", "AISI 304"),
    ("AISI 321", "hex", "AISI 304"), ("12Х18Н9Т", "strip", "AISI 304"),
    ("ЛС 59-1", "hex", "ЛС59-1"), ("Медь М1", "sheet", "М1"), ("Полиацеталь ПОМ-С белый", "sheet", "ПОМ-С"),
    ("POM-C", "round", "ПОМ-С"), ("Фторопласт-4", "round", "Ф-4"),
    ("Поликарбонат Borrex", "sheet", None), ("AISI 316", "round", None), ("БрАЖ9-4", "round", None),
    ("ВТ6", "round", None), ("", "sheet", None), ("Ф-4К20", "round", None), ("Д16АТ", "sheet", None),
])
def test_grade_mapping(material, kind, grade):
    assert site_grade(material, kind, RULES) == grade


def test_grades_text_round_trip_and_errors():
    text = grades_text(RULES)
    assert "AISI 304, 12Х18Н10Т, 12Х18Н9Т, AISI 321 = AISI 304; круг: 12Х18Н10Т" in text
    assert parse_grades(text) == RULES.grades
    with pytest.raises(RulesError, match="строка 1"):
        parse_grades("Д16Т")
    with pytest.raises(RulesError, match="сортамент"):
        parse_grades("Д16Т = Д16Т; труба: Д16Т")


def test_sheet_fits_with_rotation_and_thickness():
    b = blank("Д16Т", "sheet", {"h": 50, "length": 158, "width": 64})
    assert fits(b, PRICE.items[0]) and fits(b, PRICE.items[2])  # 200×100 и 100×200 — поворот
    assert not fits(blank("Д16Т", "sheet", {"h": 51, "length": 158, "width": 64}), PRICE.items[0])
    assert not fits(blank("Д16Т", "sheet", {"h": 50, "length": 210, "width": 64}), PRICE.items[0])
    assert not fits(blank("Д16Т", "sheet", {"h": 50, "length": 158, "width": 101}), PRICE.items[0])


def test_cheapest_fitting_sheet_and_formula():
    cost = blank_cost(blank("Д16Т", "sheet", {"h": 50, "length": 158, "width": 64}), PRICE, RULES)
    assert cost.status == "price" and cost.piece.code == "1" and cost.unit_price == 3290
    assert cost.pieces == 13 and cost.total == 42770  # 3 290 × (10 + 3 на брак)
    assert cost.net_total == pytest.approx(42770 / 1.22)
    assert cost.piece.title == "плита алюминиевый Д16Т 50×100×200 мм" and not cost.warning

    flange = blank_cost(blank("AW 6061 T6", "sheet", {"h": 14, "length": 48, "width": 48}, 20), PRICE, RULES)
    # из плиты 18×50×100 — 1 × 2 = 2 заготовки 48×48: на 20 + 3 шт. нужно 12 кусков
    assert flange.piece.code == "4" and flange.per_piece == 2 and flange.pieces == 12 and flange.total == 420 * 12


def test_round_and_hex_and_stock_warning():
    rnd = blank_cost(blank("AISI 304", "round", {"d": 56, "length": 66}, 5), PRICE, RULES)
    # 12Х18Н10Т и AISI 321 (12Х18Н10Т) — одна марка для круга; берётся дешёвый, остаток — предупреждение
    assert rnd.piece.code == "8" and rnd.total == 1900 * 8 and rnd.warning == "на складе 2 шт., нужно 8"
    hexa = blank_cost(blank("AISI 304", "hex", {"s": 22, "length": 90}, 1), PRICE, RULES)
    assert hexa.piece.code == "9" and hexa.pieces == 4
    al = blank_cost(blank("Д16Т", "round", {"d": 60, "length": 100}, 1), PRICE, RULES)
    assert al.piece.code == "6"


def test_reasons_when_not_found():
    def reason(b, price=PRICE):
        cost = blank_cost(b, price, RULES)
        assert cost.status == "none" and cost.total is None
        return cost.reason

    assert "марки нет в прайсе" in reason(blank("Поликарбонат", "sheet", {"h": 12, "length": 50, "width": 50}))
    assert "нет куска такого размера" in reason(blank("Д16Т", "sheet", {"h": 70, "length": 50, "width": 50}))
    assert "сортамента нет в прайсе" in reason(blank("Д16Т", "hex", {"s": 10, "length": 50}))
    assert "труба" in reason(blank("Д16Т", "tube", {"d": 10, "wall": 1, "length": 50}))
    assert "не определена" in reason(Blank("Д16Т", "sheet", {}, 10))
    assert "не загружен" in reason(blank("Д16Т", "sheet", {"h": 50, "length": 50, "width": 50}), None)


def test_manual_price_by_technologist():
    b = blank("Поликарбонат", "sheet", {"h": 12, "length": 50, "width": 50}, 5, TECHNOLOGIST)
    cost = blank_cost(b, PRICE, RULES, manual={"price": 250, "note": "счёт поставщика"})
    assert cost.status == "manual" and cost.unit_price == 250 and cost.total == 250 * 8
    assert cost.vat_percent == 22 and cost.note == "счёт поставщика"
    assert blank_cost(Blank("", None, {}, 2), None, RULES, manual={"price": 100}).total == 500


def test_scrap_is_parameter_and_rules_validated():
    rules = PriceRules.from_dict({**DEFAULT_RULES, "scrap_pieces": 0})
    cost = blank_cost(blank("Д16Т", "sheet", {"h": 50, "length": 158, "width": 64}), PRICE, rules)
    assert cost.total == 32900
    assert PriceRules.from_dict(None) == RULES
    with pytest.raises(RulesError):
        PriceRules.from_dict({**DEFAULT_RULES, "scrap_pieces": -1})


# Справочник по умолчанию: 1,5 мм на сторону (+3 мм на размер), округление вверх до 1 мм.
SEED_RULES = BlankRules.from_dict({
    "sheet_side_allowance_mm": 1.5, "sheet_thickness_side_allowance_mm": 1.5, "bar_diameter_side_allowance_mm": 1.5,
    "bar_length_side_allowance_mm": 1.5, "size_step_mm": 1, "sheet_thicknesses_mm": [5, 6, 8, 10, 12],
    "round_diameters_mm": {"steel": [10, 20], "aluminium": [10, 20], "brass": []}})


def test_user_example_zaglushka_22_from_piece():
    """Пример пользователя: Заглушка 35,7×23,2×5, AW 6061 (→ Д16Т), 40 шт.; кусок 8×100×300 — 22 заготовки."""
    b = suggest_blank(Dimensions(35.7, 23.2, 5), "AW 6061", "milling", 40, SEED_RULES)
    assert b.text == "AW 6061, лист h8 мм, 39×27 мм — 40 шт."  # 5+3=8; 38,7 → 39; 26,2 → 27
    plate = item(20, "плита", "Д16Т", (8, 100, 300), 1000)
    assert per_piece(b, plate) == 22  # 100/39 = 2 ряда × 300/27 = 11
    cost = blank_cost(b, PriceList("2026-10-26", 22.0, (plate,)), RULES)
    assert cost.per_piece == 22 and cost.need == 43 and cost.pieces == 2 and cost.total == 2000
    assert per_piece(b, plate, kerf=2) == 21  # рез 2 мм: 2 × 10 или 3 × 7 — лучшая сетка 21


def test_large_piece_is_candidate_and_min_total_wins():
    b = blank("Д16Т", "sheet", {"h": 8, "length": 39, "width": 27}, 40)
    small = item(21, "плита", "Д16Т", (8, 100, 300), 1000)  # 22 шт. из куска → 2 куска = 2 000 ₽
    big = item(22, "лист", "Д16Т", (8, 500, 1000), 15000)  # 12 × 37 = 444 шт. → 1 кусок = 15 000 ₽
    cheap_big = item(23, "лист", "Д16Т", (8, 500, 1000), 1500)
    price = PriceList("2026-10-26", 22.0, (big, small))
    assert blank_cost(b, price, RULES).piece.code == "21"
    cost = blank_cost(b, PriceList(None, None, (big, small, cheap_big)), RULES)
    assert cost.piece.code == "23" and cost.pieces == 1 and cost.total == 1500 and cost.vat_percent == 22


def test_bar_cut_by_length_and_stock_in_pieces():
    b = blank("Д16Т", "round", {"d": 20, "length": 30}, 10)
    bar = item(24, "пруток", "Д16Т", (20, 100), 300, stock=3)  # 3 заготовки из прутка → 13 / 3 → 5 кусков
    cost = blank_cost(b, PriceList("2026-10-26", 22.0, (bar,)), RULES)
    assert cost.per_piece == 3 and cost.pieces == 5 and cost.total == 1500 and cost.warning == "на складе 3 шт., нужно 5"


def test_plural():
    assert [plural(n, "кусок", "куска", "кусков") for n in (1, 2, 5, 11, 22, 101)] == [
        "1 кусок", "2 куска", "5 кусков", "11 кусков", "22 куска", "101 кусок"]
