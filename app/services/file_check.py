"""Проверка файлов детали: есть ли STEP и PDF, читаются ли и относятся ли к этой детали."""
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

from app.parsers.pdf_reader import pdf_text, read_pdf
from app.parsers.step_reader import read_step

DECIMAL_NUMBER = re.compile(r"\d{6}\.\d{3}")  # цифровая часть децимального номера ЕСКД: 741645.001
LABELS = {"pdf": "чертежа PDF", "step": "модели STEP"}


@dataclass(frozen=True)
class FileStatus:
    kind: str
    ok: bool  # файл есть и открывается
    message: str
    warning: str = ""  # файл открывается, но, возможно, от другой детали


def journal_code(designation: str) -> str:
    """Обозначение без суффикса копии файла «(1)», который добавляет браузер."""
    return re.sub(r"\s*\(\d+\)$", "", designation).strip()


def unreadable_reason(kind: str, path: str) -> str | None:
    """Текст ошибки, если файл не открывается; многотельный STEP и скан PDF открываются."""
    errors = (read_step(path) if kind == "step" else read_pdf(path)).errors
    if errors and ("не читается" in errors[0] or "не найден" in errors[0]):
        return errors[0]
    return None


def name_warning(filename: str, part_code: str) -> str:
    if journal_code(PurePath(filename).stem).casefold() != journal_code(part_code).casefold():
        return f"имя файла «{filename}» не совпадает с обозначением детали «{part_code}»"
    return ""


def stamp_warning(path: str, part_code: str) -> str:
    number = DECIMAL_NUMBER.search(part_code)
    if not number:
        return ""
    text = pdf_text(path)
    if text and number.group(0) not in text:
        return f"в штампе чертежа не найдено обозначение {number.group(0)}"
    return ""


def check_file(kind: str, path: str | None, name: str | None, part_code: str) -> FileStatus:
    if not path:
        return FileStatus(kind, False, f"нет {LABELS[kind]}")
    if not Path(path).exists():
        return FileStatus(kind, False, f"файл «{name}» не найден на диске — прикрепите заново")
    reason = unreadable_reason(kind, path)
    if reason:
        return FileStatus(kind, False, reason)
    warnings = [w for w in (name_warning(name or PurePath(path).name, part_code),
                            stamp_warning(path, part_code) if kind == "pdf" else "") if w]
    return FileStatus(kind, True, "открывается", "; ".join(warnings))


def check_entry_files(row) -> list[FileStatus]:
    """Статус чертежа и модели детали журнала (или позиции заказа)."""
    code = getattr(row, "part_code", None) or row.designation
    return [check_file("pdf", row.pdf_path, row.pdf_name, code),
            check_file("step", row.step_path, row.step_name, code)]
