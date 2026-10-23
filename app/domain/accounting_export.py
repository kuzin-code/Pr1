"""Выгрузка заказов за период в 1С файлом (CSV UTF-8 с BOM, разделитель «;», или Excel).

Формат колонок — одна константа COLUMNS_1C: его ещё согласуют с бухгалтерией (порядок, названия, состав
меняются только здесь). Числа — с десятичной запятой, даты — ДД.ММ.ГГГГ; пустое значение — пустая ячейка.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

# (ключ поля OrderExport, заголовок колонки в файле)
COLUMNS_1C: tuple[tuple[str, str], ...] = (
    ("number", "Номер заказа"),
    ("created", "Дата заказа"),
    ("customer", "Заказчик"),
    ("positions", "Позиции"),
    ("position_count", "Количество позиций"),
    ("quantity", "Количество, шт."),
    ("proposal_total", "Сумма КП, руб."),
    ("planned_cost", "Плановая себестоимость, руб."),
    ("actual_cost", "Фактическая себестоимость, руб."),
    ("ready", "Дата готовности"),
    ("ready_kind", "Готовность"),
)
CSV_DELIMITER = ";"
DECIMAL_SEPARATOR = ","


@dataclass(frozen=True)
class OrderExport:
    number: str
    created: date
    customer: str
    positions: str  # обозначения позиций через «; »
    position_count: int
    quantity: int
    proposal_total: float | None  # последнее выставленное КП по заказу
    planned_cost: float | None  # по расчёту позиций (утверждённому, иначе текущему)
    actual_cost: float | None  # по фактическому времени (только позиции с фактом)
    ready: date | None
    ready_kind: str  # «фактически» / «по плану» / ""


def format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", DECIMAL_SEPARATOR)
    return str(value)


def headers() -> list[str]:
    return [title for _, title in COLUMNS_1C]


def table(rows: list[OrderExport]) -> list[list]:
    """Строки значений в порядке COLUMNS_1C (без форматирования — для Excel)."""
    return [[getattr(row, key) for key, _ in COLUMNS_1C] for row in rows]


def to_csv(rows: list[OrderExport]) -> bytes:
    """CSV для 1С: UTF-8 с BOM, «;», заголовок всегда (пустой период — файл только с заголовком)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(headers())
    for values in table(rows):
        writer.writerow([format_value(v) for v in values])
    return buffer.getvalue().encode("utf-8-sig")
