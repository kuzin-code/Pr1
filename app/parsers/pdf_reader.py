"""Чтение материала, квалитетов и допусков, резьб из чертежа PDF (ФТ-04).

Непрочитанные поля технолог вводит вручную; сканы без текстового слоя дают пустой результат.
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.parsers.eskd import EskdData, parse_eskd

logging.getLogger("pypdf").setLevel(logging.ERROR)

_LETTERS = "A-Za-zА-Яа-яЁё"
_MATERIAL_LABEL = re.compile(r"(?:Материал|Material)\s*[:\-]?\s*([^\n]+)", re.I)
_MATERIAL_CUT = re.compile(r"\s+(?:ГОСТ|ТУ|GOST|OCT|ОСТ)\b|[,;(]")
_MATERIAL_PATTERNS = [
    re.compile(r"Сталь\s*\d+[А-ЯA-Z]*"),
    re.compile(r"(?<![\w])\d{1,2}Х\d*[А-Я0-9]*"),
    re.compile(r"АМг\d"),
    re.compile(r"(?<![\w])Д16Т?(?![\w])"),
    re.compile(r"(?<![\w])Ст\d(?![\w])"),
    re.compile(r"AISI\s?\d{3}[A-Z]?"),
    re.compile(r"(?<![\w])ВТ\d+(?:-\d+)?"),
    # пластики
    re.compile(r"(?:Полиацеталь|Капролон|Фторопласт|Полиамид|Полипропилен|Полиэтилен|Поликарбонат|"
               r"Текстолит|Оргстекло|ПОМ|PEEK|ПЭЭК|ПММА|ABS|АБС)[^\n,;]*", re.I),
]
# Основная надпись ЕСКД: материал стоит строками после «Лист … Листов» до масштаба, слово «Материал» не пишется.
_SHEETS = re.compile(r"Листов\s*$")
_SCALE = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?\s*$")
_NUMBER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
_THREAD = re.compile(
    rf"(?<![{_LETTERS}])[MМ](\d+(?:[.,]\d+)?)(?:\s*[x×хX]\s*(\d+(?:[.,]\d+)?))?(?:-(\d[{_LETTERS}]{{1,2}}))?"
    rf"(?![{_LETTERS}\d])")
_FIT = re.compile(r"(?<![A-Za-zА-Яа-я])(js|JS|[HhKkNnPpGgFfEeDdSsUu])(\d{1,2})(?![0-9A-Za-zА-Яа-я])")
_IT = re.compile(r"\bIT\s?(\d{1,2})\b")
_TOLERANCE = re.compile(r"±\s*\d+(?:[.,]\d+)?")
_CYR_TO_LAT = str.maketrans({"Н": "H", "н": "h", "Е": "E", "е": "e", "М": "M"})


@dataclass
class DrawingData:
    material: str | None = None
    tolerance_grade: int | None = None
    fits: list[str] = field(default_factory=list)
    tolerances: list[str] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)
    mass_kg: float | None = None
    eskd: EskdData | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def unread_fields(self) -> list[str]:
        return [name for name in ("material", "tolerance_grade") if getattr(self, name) is None]


def _find_material(text: str, known: list[str]) -> str | None:
    label = _MATERIAL_LABEL.search(text)
    if label:
        value = _MATERIAL_CUT.split(label.group(1), maxsplit=1)[0].strip()
        if value:
            return value
    material = parse_eskd(text).material  # уже без стандарта
    if material:
        return material
    for name in sorted(known, key=len, reverse=True):
        if name and name.casefold() in text.casefold():
            return name
    for pattern in _MATERIAL_PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(0).strip()
    return None


def _normalize_thread(m: re.Match) -> str:
    result = "M" + m.group(1).replace(",", ".")
    if m.group(2):
        result += "x" + m.group(2).replace(",", ".")
    if m.group(3):
        result += "-" + m.group(3).translate(_CYR_TO_LAT)
    return result


def parse_drawing_text(text: str, known_materials: list[str] | tuple = (), file_stem: str | None = None
                       ) -> DrawingData:
    data = DrawingData()
    data.eskd = parse_eskd(text, file_stem)
    data.material = _find_material(text, list(known_materials))
    data.mass_kg = data.eskd.mass_kg
    data.threads = data.eskd.threads or list(dict.fromkeys(_normalize_thread(m) for m in _THREAD.finditer(text)))
    # Посадки и квалитет — по ЕСКД: без допусков резьбы «6H/6g», трубной резьбы «G1/4» и марки материала.
    data.fits, data.tolerance_grade = list(data.eskd.fits), data.eskd.tolerance_grade
    data.tolerances = [t.replace(" ", "") for t in _TOLERANCE.findall(text)]
    return data


_MOJIBAKE = re.compile("[¨¸¹À-ÿ]")  # байты 1251, прочитанные как Latin-1
_MOJIBAKE_WORD = re.compile("[À-ÿ]{3,}")


def repair_text(text: str) -> str:
    """Русский текст, записанный в PDF в кодировке 1251 без таблицы символов: «Ñïëàâ» → «Сплав»."""
    if len(_MOJIBAKE_WORD.findall(text)) < 3:
        return text
    return _MOJIBAKE.sub(lambda m: m.group(0).encode("latin-1").decode("cp1251"), text)


def pdf_text(path: Path | str) -> str:
    """Текстовый слой PDF; пустая строка, если файл не читается или это скан."""
    try:
        from pypdf import PdfReader

        return repair_text("\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))
    except Exception:  # noqa: BLE001
        return ""


def read_pdf(path: Path | str, known_materials: list[str] | tuple = ()) -> DrawingData:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = repair_text("\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception:  # noqa: BLE001 — любой сбой чтения: ручной ввод (О-1)
        return DrawingData(errors=["Файл PDF не читается или повреждён: введите данные вручную"])
    if not text.strip():
        return DrawingData(errors=["В PDF нет текстового слоя (скан?): введите данные вручную"])
    return parse_drawing_text(text, known_materials, Path(path).stem)
