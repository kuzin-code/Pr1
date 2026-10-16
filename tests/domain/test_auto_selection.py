"""Автоматический выбор: группа материала, балл сложности, аналог по технологии, станок."""
import pytest

from app.domain.analog import pick_analog
from app.domain.complexity import estimate_complexity
from app.domain.enums import Stage
from app.domain.machines import choose_machine
from app.domain.materials import machinability, material_group
from app.domain.models import Dimensions, PartFeatures
from tests.domain.conftest import make_entry


@pytest.mark.parametrize("name,group", [
    ("Полиацеталь ПОМ-С белый", "plastic"), ("Капролон", "plastic"), ("Д16Т", "aluminium"),
    ("D16T", "aluminium"), ("АМг6", "aluminium"), ("Сталь 45", "steel"), ("40Х", "steel"),
    ("12Х18Н10Т", "stainless"), ("ВТ6", "titanium"), ("непонятно", "other"), (None, "other"),
])
def test_material_groups(name, group):
    assert material_group(name) == group


def test_machinability_relative_to_aluminium():
    assert machinability("Д16Т") == 1.0
    assert machinability("Полиацеталь ПОМ-С") < 1.0 < machinability("Сталь 45") < machinability("12Х18Н10Т")


@pytest.mark.parametrize("grade,threads,expected", [
    (12, 0, 1), (11, 0, 2), (9, 2, 3), (8, 0, 3), (7, 0, 4), (6, 0, 5), (9, 5, 4), (6, 5, 5),
])
def test_estimate_complexity(grade, threads, expected):
    assert estimate_complexity(grade, threads) == expected


def features(dims=(84, 76, 26), material="Полиацеталь ПОМ-С белый", grade=9, complexity=3, tech="milling"):
    return PartFeatures(Dimensions(*dims), material, grade, complexity, tech)


def test_strict_analog_preferred():
    journal = [make_entry("strict", dims=(84, 76, 26), material="Полиацеталь ПОМ-С белый", grade=9, complexity=3),
               make_entry("other", dims=(80, 70, 20))]
    choice = pick_analog(features(), journal)
    assert choice.entry.part_code == "strict"
    assert not choice.approximate
    assert choice.route == choice.entry.stages  # время без пересчёта


def test_nearest_by_technology_when_no_strict_match():
    journal = [make_entry("turned", dims=(84, 76, 26), technology="turning"),
               make_entry("far", dims=(400, 250, 40), complexity=2),
               make_entry("near", dims=(100, 100, 15), complexity=3, grade=8)]
    choice = pick_analog(features(), journal)
    assert choice.entry.part_code == "near"  # токарная деталь не рассматривается
    assert choice.approximate
    assert choice.reasons


def test_route_scaled_by_size_material_and_complexity():
    analog = make_entry("a", dims=(100, 100, 15), complexity=3, material="Д16Т")
    choice = pick_analog(features(), [analog])
    by = {s.stage: s for s in choice.route}
    base = {s.stage: s for s in analog.stages}
    assert by[Stage.SETUP].hours == base[Stage.SETUP].hours  # наладка не масштабируется
    assert by[Stage.PROGRAMMING].hours == base[Stage.PROGRAMMING].hours
    assert 0 < by[Stage.MACHINING].hours < base[Stage.MACHINING].hours  # пластик и меньше габарит
    assert by[Stage.MACHINING].count_mode == base[Stage.MACHINING].count_mode


def test_no_analog_of_same_technology():
    assert pick_analog(features(tech="turning"), [make_entry()]) is None


def test_choose_machine_prefers_strict_analog_machine(machines):
    part = features(dims=(100, 60, 20))
    assert choose_machine(part, machines, analog_machine="SMM", strict=True,
                          preferred={"milling": "VF2"}).code == "SMM"


def test_choose_machine_uses_preferred_for_technology(machines):
    part = features(dims=(100, 60, 20))
    assert choose_machine(part, machines, analog_machine="SMM", strict=False,
                          preferred={"milling": "VF2"}).code == "VF2"


def test_choose_machine_falls_back_to_any_allowed(machines):
    part = features(dims=(700, 300, 100))
    assert choose_machine(part, machines, None, False, {"milling": "SMM"}).code == "VF2"
    assert choose_machine(features(dims=(2000, 10, 10)), machines, None, False, {}) is None


@pytest.mark.parametrize("grade,threads,ra,form,expected", [
    (9, 0, 0.8, False, 4), (9, 0, 3.2, True, 4), (9, 6, 0.4, True, 4),  # надбавка — не больше 1 балла
    (12, 0, None, False, 1), (6, 6, 0.4, True, 5),
])
def test_complexity_with_roughness_and_form_tolerances(grade, threads, ra, form, expected):
    assert estimate_complexity(grade, threads, ra_min=ra, form_tolerances=form) == expected


def test_same_part_in_journal_is_analog_by_designation():
    from app.domain.analog import same_part
    journal = [make_entry("КПУ.741645.001 Корпус", dims=(84, 76, 26), complexity=3)]
    choice = same_part(journal, "КПУ.741645.001 Корпус (1)", "КПУ.741645.001")
    assert choice.entry.part_code == "КПУ.741645.001 Корпус" and not choice.approximate
    assert choice.route == journal[0].stages
    assert same_part(journal, "Другая деталь", "АБВГ.000.001") is None


def test_aw_alloys_are_aluminium():
    assert material_group("AW 6082 T6") == "aluminium" and material_group("EN AW-5083") == "aluminium"
