"""Приписки копий браузера «(1)», «(2)» не мешают паре STEP+PDF и не попадают в обозначение."""
from app.domain.order_intake import designation, pair_files


def test_copy_suffix_removed_and_files_paired():
    assert designation("МБЦР.107.000.162-01 Основание корпуса (1).STEP") == "МБЦР.107.000.162-01 Основание корпуса"
    result = pair_files(["Деталь (1).STEP", "Деталь (2).pdf"])
    assert list(result.pairs) == ["Деталь"] and not result.unpaired
