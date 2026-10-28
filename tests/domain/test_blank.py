"""Заготовка для заготовителя: формат строки, ряды, сортамент из чертежа, правка технологом."""
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
    round_up,
    suggest_blank,
)
from app.domain.models import Dimensions

RULES = BlankRules.from_dict({
    "sheet_side_allowance_mm": 2, "sheet_thickness_allowance_mm": 2, "bar_diameter_allowance_mm": 3,
    "bar_length_allowance_mm": 5, "size_step_mm": 1,
    "sheet_thicknesses_mm": [10, 12, 16, 20, 25, 30, 40], "round_diameters_mm": [30, 32, 36, 40, 45, 50]})


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


def test_sheet_suggested_for_milling():
    blank = suggest_blank(Dimensions(152, 58, 20), "Д16Т", "milling", 25, RULES)
    assert blank.source == SYSTEM and blank.kind == "sheet"
    assert blank.sizes == {"h": 25, "length": 156, "width": 62}  # 20+2 → 25 по ряду; 152+2×2, 58+2×2
    assert blank.text == "Д16Т, лист h25 мм, 156×62 мм — 25 шт."
    assert any("по технологии" in r for r in blank.remarks)


def test_round_suggested_for_turning_shaft_and_disc():
    shaft = suggest_blank(Dimensions(60, 36.5, 36.5), "Д16Т", "turning", 10, RULES)
    assert shaft.text == "Д16Т, круг Ø40 мм, L 65 мм — 10 шт."  # 36,5+3 → 40; 60+5
    disc = suggest_blank(Dimensions(44, 44, 12), "Сталь 45", "turning", 2, RULES)
    assert disc.sizes == {"d": 50, "length": 17}  # ось диска — меньший габарит


def test_drawing_sortament_beats_technology():
    blank = suggest_blank(Dimensions(60, 28, 28), "Д16Т", "milling", 4, RULES, "круг", "Круг 30-В ГОСТ 2590-2006")
    assert blank.kind == "round" and blank.sizes == {"d": 30, "length": 65}  # размер проката — по чертежу
    assert any("по чертежу" in r for r in blank.remarks)
    plate = suggest_blank(Dimensions(60, 28, 28), "Д16Т", "turning", 4, RULES, "плита", "Плита ГОСТ 17232-99")
    assert plate.kind == "sheet" and plate.sizes["h"] == 30


def test_drawing_size():
    assert drawing_size("Круг 30-В ГОСТ 2590-2006") == 30
    assert drawing_size("Лист 2,5 ГОСТ 21631-76") == 2.5
    assert drawing_size("Полоса 10х40 ГОСТ 103-2006") is None
    assert drawing_size("Лист Б-ПН-0-25 ГОСТ 19903") is None


def test_undefined_without_dims_rules_or_beyond_series():
    no_dims = suggest_blank(None, "Д16Т", "milling", 5, RULES)
    assert not no_dims.defined and no_dims.text == UNDEFINED_TEXT
    assert not suggest_blank(Dimensions(100, 50, 20), "Д16Т", "milling", 5, None).defined
    assert not suggest_blank(Dimensions(100, 50, 45), "Д16Т", "milling", 5, RULES).defined  # 47 > 40
    angle = suggest_blank(Dimensions(100, 50, 20), "Ст3", "milling", 5, RULES, "уголок", "Уголок 50х50")
    assert angle.kind is None and not angle.defined


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
