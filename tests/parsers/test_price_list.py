"""Разбор прайса prutki.ru: шапка, дата, НДС, позиции, мусорные строки, понятная ошибка формата."""
import pytest

from app.parsers.price_list import PriceFormatError, parse_price_list
from tests.fixtures.price_list import price_xlsx


def test_header_date_vat_and_items():
    price, skipped = parse_price_list(price_xlsx(), "price.xlsx")
    assert price.date == "2026-10-26" and price.vat_percent == 22
    assert len(price.items) == 10 and skipped == 3  # анод, раздел, строка без цены
    plate = price.items[0]
    assert (plate.code, plate.form, plate.grade, plate.dims, plate.price, plate.stock) == (
        "114102", "плита", "Д16Т", (50, 100, 200), 3290, 15)
    assert plate.title == "плита алюминиевая Д16Т 50×100×200 мм"
    by_code = {i.code: i for i in price.items}
    assert by_code["105114"].grade == "AISI 321 (12Х18Н10Т)" and by_code["105114"].dims == (60, 100)
    assert by_code["122001"].form == "шестигранник" and by_code["131001"].grade == "Ф-4"


def test_header_without_vat_and_date():
    price, _ = parse_price_list(price_xlsx(head=["Прайс"]), "price.xlsx")
    assert price.date is None and price.vat_percent is None and price.items


@pytest.mark.parametrize("content,name,message", [
    (price_xlsx(title=("№", "Наименование", "Сумма")), "price.xlsx", "заголовок таблицы"),
    (price_xlsx(rows=[(1, "Болт М6", 10, 1)]), "price.xlsx", "ни одной строки"),
    (b"not an excel file", "price.xls", "не читается"),
    (b"<html></html>", "price.html", "файл Excel"),
])
def test_unknown_format_gives_clear_error(content, name, message):
    with pytest.raises(PriceFormatError, match=message):
        parse_price_list(content, name)


def test_grade_with_letter_x_and_without_grade():
    rows = [(1, "Заготовка круг стальной 40Х 20 х 100 мм", 50, 1),
            (2, "Заготовка стержень текстолитовый 10 х 50 мм", 30, 1),
            (3, "Заготовка круг стальной 95Х18 10 х 100 мм", 40, 1)]
    price, _ = parse_price_list(price_xlsx(rows=rows), "price.xlsx")
    assert [(i.grade, i.dims) for i in price.items] == [("40Х", (20, 100)), ("", (10, 50)), ("95Х18", (10, 100))]
