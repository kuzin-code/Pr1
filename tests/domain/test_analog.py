"""ФТ-07: подбор аналога по сложности, квалитету, габаритам ±10 мм и материалу."""
from app.domain.analog import find_analogs
from app.domain.models import Dimensions, PartFeatures
from tests.domain.conftest import make_entry


def features(dims=(100, 60, 20), material="Д16Т", grade=7, complexity=3, technology="milling"):
    return PartFeatures(Dimensions(*dims), material, grade, complexity, technology)


def test_exact_match_found_with_similarity_details():
    matches = find_analogs(features(), [make_entry()])
    assert len(matches) == 1
    m = matches[0]
    assert m.entry.part_code == "P-1"
    assert m.max_dim_deviation == 0
    assert m.reasons  # признаки сходства для показа технологу


def test_each_dimension_within_10_mm():
    journal = [make_entry("ok", dims=(110, 50, 30)), make_entry("bad", dims=(111, 60, 20))]
    codes = [m.entry.part_code for m in find_analogs(features(), journal)]
    assert codes == ["ok"]


def test_orientation_does_not_matter():
    assert find_analogs(features(dims=(20, 100, 60)), [make_entry()])


def test_material_must_match_ignoring_case_and_spaces():
    journal = [make_entry("a", material=" д16т "), make_entry("b", material="12Х18Н10Т")]
    assert [m.entry.part_code for m in find_analogs(features(), journal)] == ["a"]


def test_complexity_must_match():
    assert find_analogs(features(complexity=4), [make_entry(complexity=3)]) == []


def test_tolerance_grade_similar_within_one():
    journal = [make_entry("g6", grade=6), make_entry("g8", grade=8), make_entry("g9", grade=9)]
    codes = {m.entry.part_code for m in find_analogs(features(grade=7), journal)}
    assert codes == {"g6", "g8"}


def test_technology_must_match():
    assert find_analogs(features(technology="turning"), [make_entry()]) == []


def test_sorted_by_closeness():
    journal = [make_entry("far", dims=(109, 60, 20)), make_entry("near", dims=(101, 60, 20)),
               make_entry("exact_grade_far", dims=(105, 60, 20), grade=7)]
    codes = [m.entry.part_code for m in find_analogs(features(), journal)]
    assert codes[0] == "near"
    assert codes[-1] == "far"


def test_nine_part_journal_suggestion_is_within_conditions():
    journal = [make_entry(f"J{i}", dims=(80 + i * 5, 60, 20)) for i in range(9)]
    matches = find_analogs(features(dims=(100, 60, 20)), journal)
    assert matches[0].entry.part_code == "J4"
    assert all(m.max_dim_deviation <= 10 for m in matches)
