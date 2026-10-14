"""ФТ-01, ФТ-02, О-2, О-7."""
import pytest

from app.domain.order_intake import (
    MAX_POSITIONS,
    FileFormatError,
    OrderTooLargeError,
    file_kind,
    pair_files,
    validate_position_count,
)


@pytest.mark.parametrize("name,kind", [
    ("Корпус-01.step", "step"), ("A.STP", "step"), ("a.Step", "step"), ("Корпус-01.pdf", "pdf"),
])
def test_file_kind_accepts_step_and_pdf(name, kind):
    assert file_kind(name) == kind


@pytest.mark.parametrize("name", ["model.sldprt", "drawing.dwg", "archive.zip", "noext"])
def test_other_formats_rejected_with_message(name):
    with pytest.raises(FileFormatError) as err:
        file_kind(name)
    assert "STEP" in str(err.value) and "PDF" in str(err.value)


def test_order_of_50_positions_accepted_51_rejected():
    validate_position_count(MAX_POSITIONS)
    with pytest.raises(OrderTooLargeError):
        validate_position_count(MAX_POSITIONS + 1)


def test_zero_positions_rejected():
    with pytest.raises(OrderTooLargeError):
        validate_position_count(0)


def test_pairs_by_matching_names_case_insensitive():
    result = pair_files(["Вал-3.STEP", "вал-3.pdf", "Корпус.stp", "Корпус.pdf"])
    assert set(result.pairs) == {"Вал-3", "Корпус"}
    assert result.pairs["Корпус"] == {"step": "Корпус.stp", "pdf": "Корпус.pdf"}
    assert result.unpaired == []


def test_unmatched_names_and_missing_pair_reported():
    result = pair_files(["Вал-3.step", "Вал-03.pdf", "Втулка.step"])
    assert result.pairs == {}
    assert {u.filename for u in result.unpaired} == {"Втулка.step", "Вал-03.pdf", "Вал-3.step"}
    assert {u.kind for u in result.unpaired} == {"step", "pdf"}


def test_duplicate_file_rejected():
    with pytest.raises(FileFormatError):
        pair_files(["A.step", "a.stp"])
