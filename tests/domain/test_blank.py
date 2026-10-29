"""Заготовка для заготовителя: формат строки, ряды по группе материала, сортамент из чертежа, спорные случаи."""
import pytest

from app.domain.blank import (
    SYSTEM,
    TECHNOLOGIST,
    UNDEFINED_TEXT,
    Blank,
    BlankError,
    BlankRules,
    drawing_size,
    manual_blank,
    round_group,
    round_up,
    suggest_blank,
)
from app.domain.models import Dimensions

# Тестовые правила: припуск 3 мм на сторону (в справочнике по умолчанию — 1,5 мм); ряды — укороченные для наглядности.
RULES = BlankRules.from_dict({
    "sheet_side_allowance_mm": 3, "sheet_thickness_side_allowance_mm": 3, "bar_diameter_side_allowance_mm": 3,
    "bar_length_side_allowance_mm": 3, "size_step_mm": 1, "sheet_thicknesses_mm": [10, 12, 16, 20, 25, 30, 40, 50],
    "round_diameters_mm": {"steel": [30, 32, 36, 40, 45, 50, 55, 60], "aluminium": [30, 40, 50, 60], "brass": []}})


def test_text_format_sheet_and_round():
    sheet = Blank("Д16Т", "sheet", {"h": 25, "length": 100, "width": 100}, 25)
    assert sheet.text == "Д16Т, лист h25 мм, 100×100 мм — 25 шт."
    bar = Blank("Д16Т", "round", {"d": 40, "length": 65}, 10)
    assert bar.text == "Д16Т, круг Ø40 мм, L 65 мм — 10 шт."
    assert Blank("Д16Т", "sheet", {"h": 1.5, "length": 80, "width": 40}, 1).text.startswith("Д16Т, лист h1,5 мм")


def test_round_up_to_standard_series():
    assert round_up(22, (10, 20, 25, 30)) == 25
    assert round_up(25, (10, 20, 25, 30)) == 25
    assert round_up(31, (10, 20, 25, 30)) is None


def test_sheet_three_mm_per_side():
    blank = suggest_blank(Dimensions(152, 58, 20), "Д16Т", "milling", 25, RULES)
    assert blank.source == SYSTEM and blank.kind == "sheet"
    assert blank.sizes == {"h": 30, "length": 158, "width": 64}  # 20+2×3=26 → 30 по ряду; 152+2×3, 58+2×3
    assert blank.text == "Д16Т, лист h30 мм, 158×64 мм — 25 шт."
    assert any("по технологии" in r for r in blank.remarks)


def test_round_series_by_material_group():
    shaft = suggest_blank(Dimensions(60, 36.5, 36.5), "Д16Т", "turning", 10, RULES)
    assert shaft.text == "Д16Т, круг Ø50 мм, L 66 мм — 10 шт."  # 36,5+6 → 50 по ряду алюминия; 60+6
    disc = suggest_blank(Dimensions(44, 44, 12), "Сталь 45", "turning", 2, RULES)
    assert disc.sizes == {"d": 50, "length": 18}  # ряд стали; ось диска — меньший габарит
    # нет своего ряда (в RULES ряд латуни пуст, у нержавейки ряда нет) — ряд стали и «проверьте»
    for material in ("ЛС59-1", "12Х18Н10Т"):
        blank = suggest_blank(Dimensions(60, 20, 20), material, "turning", 1, RULES)
        assert blank.defined and blank.sizes["d"] == 30 and "ряд диаметров стали" in blank.remarks[-1]
    assert round_group("ЛС 59-1") == "brass" and round_group("БрАЖ9-4") == "bronze"


def test_drawing_sortament_beats_technology():
    blank = suggest_blank(Dimensions(60, 28, 28), "Д16Т", "milling", 4, RULES, "круг", "Круг 30-В ГОСТ 2590-2006")
    assert blank.kind == "round" and blank.sizes == {"d": 30, "length": 66}  # размер проката — по чертежу
    assert any("по чертежу" in r for r in blank.remarks)
    plate = suggest_blank(Dimensions(60, 28, 28), "Д16Т", "turning", 4, RULES, "плита", "Плита ГОСТ 17232-99")
    assert plate.kind == "sheet" and plate.sizes["h"] == 40  # 28+6=34 → 40
    hexagon = suggest_blank(Dimensions(60, 27.7, 24), "Сталь 45", "turning", 3, RULES, "шестигранник",
                            "Шестигранник 24 ГОСТ 2879-2006")
    assert hexagon.text == "Сталь 45, шестигранник S24 мм, L 66 мм — 3 шт."


def test_drawing_size():
    assert drawing_size("Круг 30-В ГОСТ 2590-2006") == 30
    assert drawing_size("Лист 2,5 ГОСТ 21631-76") == 2.5
    assert drawing_size("Полоса 10х40 ГОСТ 103-2006") is None
    assert drawing_size("Лист Б-ПН-0-25 ГОСТ 19903") is None


@pytest.mark.parametrize("dims, material, rules, word, sortament, why", [
    (None, "Д16Т", RULES, None, None, "нет габаритов"),
    (Dimensions(100, 50, 20), "Д16Т", None, None, None, "не заданы припуски"),
    (Dimensions(60, 28, 10), "Д16Т", RULES, "труба", "Труба 30х2", "труба"),
    (Dimensions(100, 50, 20), "Ст3", RULES, "уголок", "Уголок 50х50", "задаёт технолог"),
])
def test_undefined_only_without_data(dims, material, rules, word, sortament, why):
    blank = suggest_blank(dims, material, "milling", 5, rules, word, sortament)
    assert not blank.defined and blank.text == UNDEFINED_TEXT
    assert why in blank.remarks[-1]


@pytest.mark.parametrize("dims, material, word, sortament, sizes, note", [
    (Dimensions(100, 50, 45), "Д16Т", None, None, {"h": 51, "length": 106, "width": 56}, "вне стандартного ряда"),
    (Dimensions(100, 50, 20), "Полиацеталь ПОМ-С", None, None, {"h": 30, "length": 106, "width": 56}, "пластик"),
    (Dimensions(613, 200, 5.4), "Поликарбонат", None, None, {"h": 12, "length": 619, "width": 206}, "пластик"),
    (Dimensions(100, 50, 20), "", None, None, {"h": 30, "length": 106, "width": 56}, "материал не указан"),
    (Dimensions(60, 28, 28), "Д16Т", "круг", "Круг 25 ГОСТ 2590", {"d": 40, "length": 66}, "меньше детали"),
    (Dimensions(60, 28, 28), "Д16Т", "шестигранник", "Шестигранник ГОСТ 2879", {"s": 34, "length": 66},
     "по габаритам"),
    (Dimensions(60, 28, 10), "Д16Т", "полоса", "Полоса 10х30 ГОСТ 103", {"h": 16, "width": 34, "length": 66},
     "по габаритам"),
])
def test_disputed_cases_proposed_with_check_remark(dims, material, word, sortament, sizes, note):
    """Система определяет заготовку сама, спорное — замечание «проверьте» (решение пользователя 28.10.2026)."""
    blank = suggest_blank(dims, material, "milling", 5, RULES, word, sortament)
    assert blank.defined and blank.source == SYSTEM and blank.sizes == sizes
    assert any(note in r for r in blank.remarks) and any("проверьте" in r for r in blank.remarks)


def test_old_format_rules_not_read():
    assert BlankRules.from_dict({"sheet_side_allowance_mm": 2, "round_diameters_mm": [10, 20]}) is None
    assert BlankRules.from_dict(None) is None


def test_manual_blank_validated():
    blank = manual_blank("Д16Т", "sheet", {"h": "25", "length": "100", "width": "100,5"}, "20", " на две детали ")
    assert blank.source == TECHNOLOGIST and blank.quantity == 20 and blank.note == "на две детали"
    assert blank.text == "Д16Т, лист h25 мм, 100×100,5 мм — 20 шт."
    restored = Blank.from_dict(blank.to_dict(), "Д16Т")
    assert restored == blank
    with pytest.raises(BlankError):
        manual_blank("Д16Т", "round", {"d": "40"}, 1)  # нет длины
    with pytest.raises(BlankError):
        manual_blank("Д16Т", "unknown", {}, 1)
    with pytest.raises(BlankError):
        manual_blank("Д16Т", "round", {"d": "40", "length": "60"}, 0)
