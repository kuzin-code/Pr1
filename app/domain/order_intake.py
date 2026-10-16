"""Приём заказа: форматы файлов, лимит позиций, связывание STEP+PDF по имени (ФТ-01, ФТ-02)."""
import re
from dataclasses import dataclass, field
from pathlib import PurePath

MAX_POSITIONS = 50
_EXTENSIONS = {".step": "step", ".stp": "step", ".pdf": "pdf"}


class IntakeError(ValueError):
    pass


class FileFormatError(IntakeError):
    pass


class OrderTooLargeError(IntakeError):
    pass


def file_kind(filename: str) -> str:
    """'step' или 'pdf'; иначе FileFormatError с сообщением пользователю."""
    suffix = PurePath(filename).suffix.lower()
    if suffix not in _EXTENSIONS:
        raise FileFormatError(
            f"Файл «{filename}» отклонён: принимаются только форматы STEP (.step, .stp) и PDF (.pdf)"
        )
    return _EXTENSIONS[suffix]


def designation(filename: str) -> str:
    """Обозначение детали — имя файла без расширения и без приписки копии браузера «(1)», «(2)»."""
    return re.sub(r"\s*\(\d+\)$", "", PurePath(filename).stem.strip())


def validate_position_count(count: int) -> None:
    if count < 1:
        raise OrderTooLargeError("Заказ должен содержать хотя бы одну позицию")
    if count > MAX_POSITIONS:
        raise OrderTooLargeError(
            f"В заказе {count} позиций: допускается не более {MAX_POSITIONS}. Заказ не принят"
        )


@dataclass(frozen=True)
class UnpairedFile:
    designation: str
    kind: str
    filename: str


@dataclass
class PairingResult:
    pairs: dict[str, dict[str, str]] = field(default_factory=dict)
    unpaired: list[UnpairedFile] = field(default_factory=list)

    @property
    def position_count(self) -> int:
        return len(self.pairs) + len(self.unpaired)


def pair_files(filenames: list[str]) -> PairingResult:
    """Связывает STEP и PDF по совпадающему имени (без учёта регистра)."""
    groups: dict[str, dict[str, str]] = {}
    display: dict[str, str] = {}
    for name in filenames:
        kind = file_kind(name)
        key = designation(name).casefold()
        files = groups.setdefault(key, {})
        if kind in files:
            raise FileFormatError(f"Повторяющийся файл {kind.upper()} для детали «{designation(name)}»")
        files[kind] = name
        display.setdefault(key, designation(name))

    result = PairingResult()
    for key, files in groups.items():
        if len(files) == 2:
            result.pairs[display[key]] = files
        else:
            (kind, name), = files.items()
            result.unpaired.append(UnpairedFile(designation(name), kind, name))
    return result


def match_files(designations: list[str], filenames: list[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Привязка файлов к позициям списка по обозначению: имя файла равно обозначению или начинается
    с него и пробела / подчёркивания (MBIS.AI.11000.19 не забирает файл MBIS.AI.11000.19-01)."""
    matched: dict[str, dict[str, str]] = {}
    unmatched = []
    ordered = sorted(designations, key=len, reverse=True)
    for name in filenames:
        kind = file_kind(name)
        stem = designation(name).casefold()
        target = next((d for d in ordered if stem == d.casefold()
                       or (stem.startswith(d.casefold()) and stem[len(d)] in " _")), None)
        if target is None or kind in matched.get(target, {}):
            unmatched.append(name)
            continue
        matched.setdefault(target, {})[kind] = name
    return matched, unmatched
