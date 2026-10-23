"""Выгрузка в 1С: колонки из одной константы, CSV UTF-8 с BOM и «;», пустой период — только заголовок."""
import csv
from datetime import date

from app.domain import accounting_export as ax


def row(**kw):
    base = dict(number="З-1", created=date(2026, 10, 1), customer="ООО «Ромашка»", positions="А-1; Б-2",
                position_count=2, quantity=15, proposal_total=12345.5, planned_cost=8000.0, actual_cost=None,
                ready=date(2026, 10, 20), ready_kind="по плану")
    base.update(kw)
    return ax.OrderExport(**base)


def test_columns_constant_drives_header():
    assert ax.headers()[0] == "Номер заказа"
    assert [k for k, _ in ax.COLUMNS_1C] == list(ax.OrderExport.__dataclass_fields__)


def test_csv_bom_delimiter_and_formats():
    data = ax.to_csv([row()])
    assert data.startswith(b"\xef\xbb\xbf")
    lines = data.decode("utf-8-sig").split("\r\n")
    assert lines[0].split(";") == ax.headers()
    assert lines[1] == "З-1;01.10.2026;ООО «Ромашка»;А-1; Б-2;2;15;12345,50;8000,00;;20.10.2026;по плану" \
        or lines[1].split(";")[0] == "З-1"
    values = next(__import__("csv").reader([lines[1]], delimiter=";"))
    assert values == ["З-1", "01.10.2026", "ООО «Ромашка»", "А-1; Б-2", "2", "15", "12345,50", "8000,00", "",
                      "20.10.2026", "по плану"]


def test_empty_period_header_only():
    text = ax.to_csv([]).decode("utf-8-sig")
    assert text == ";".join(ax.headers()) + "\r\n"
