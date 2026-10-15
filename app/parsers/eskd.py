"""Чтение чертежа детали по ЕСКД из текстового слоя PDF.

ГОСТ 2.104 — основная надпись (форма 1): графа 1 наименование, 2 обозначение, 3 материал
(для проката — дробью: сортамент / марка), 5 масса, 6 масштаб, 7–8 лист / листов.
ГОСТ 2.316 — технические требования: пронумерованные пункты над основной надписью.
ГОСТ 25346/25347 — квалитеты и поля допусков; ГОСТ 30893.1 — общие допуски (классы f, m, c, v).
ГОСТ 2.309 — шероховатость Ra; ГОСТ 2.308 — допуски формы и расположения; ГОСТ 2.311 — резьбы.
ГОСТ 9.306 / 9.032 — покрытия; термообработка — по ТТ (HRC, HB, закалка, цементация…).
В PDF из КОМПАС знак диаметра «Ø» приходит буквой «n».
"""
import re
from dataclasses import dataclass, field

_LET = "A-Za-zА-Яа-яЁё"
_SHEETS = re.compile(r"Листов\s*$")
_SCALE = re.compile(r"^\s*(\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?)\s*$")
_NUMBER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
_FORMAT = re.compile(r"Формат\s*([АA]\d)")
_REQUIREMENT = re.compile(r"^\s*(\d{1,2})\.\s+(\S.*?)\s*$")
_SORTAMENT = re.compile(r"^(Круг|Пруток|Шестигранник|Квадрат|Полоса|Лист|Плита|Труба|Лента|Уголок|Проволока)\b", re.I)
_FIT = re.compile(rf"(?<![{_LET}])(js|JS|[HhKkNnPpGgFfEeDdSsUu])(\d{{1,2}})(?![0-9{_LET}])")
_IT = re.compile(r"\bIT\s?(\d{1,2})\b")
_GENERAL_CLASS = re.compile(r"30893\.1\s*[-–—]\s*([fmcv])\b", re.I)
GENERAL_CLASS_GRADE = {"f": 12, "m": 14, "c": 16, "v": 17}  # приближённое соответствие классов ГОСТ 30893.1
_RA = re.compile(r"\bRa\s*(\d+(?:[.,]\d+)?)")
_THREAD = re.compile(
    rf"(?:(\d+)\s*отв\.?\s*)?(?<![{_LET}])[MМ](\d+(?:[.,]\d+)?)(?:\s*[x×хX]\s*(\d+(?:[.,]\d+)?))?"
    rf"(?:-(\d[{_LET}]{{1,2}}))?(?=$|[MМ]\d|[^{_LET}\d])")  # «M6M6» — две склеенные выноски КОМПАС
_HOLES = re.compile(r"(\d+)\s*отв\b", re.I)
_GDT_SYMBOLS = re.compile("[⏤⏥⌓⌒⊥∥∠⌯⌖◎○⌭↗⟂]")
_GDT_TEXT = re.compile(rf"^\s*[{_LET}]?\s*\d+[.,]\d+\s+[А-ЯA-Z]{{1,2}}\s*$")
_COATING = re.compile(r"Покрытие\s*[:\-]?\s*(.+)", re.I)
_PAINT = re.compile(r"\b(Эмаль|Краска|Грунт|Лак)\b", re.I)
_HEAT = re.compile(r"\bHRC|\bHB\b|\bHV\b|Закал|Цементир|Азотир|Нормализ|Отпуст|Улучш|Термообр", re.I)
_CYR_TO_LAT = str.maketrans({"Н": "H", "н": "h", "Е": "E", "е": "e", "М": "M"})


@dataclass
class EskdData:
    designation: str | None = None  # графа 2
    name: str | None = None  # графа 1
    material: str | None = None  # графа 3 (марка)
    sortament: str | None = None  # графа 3, числитель дроби (вид проката)
    mass_kg: float | None = None  # графа 5
    scale: str | None = None  # графа 6
    sheet_format: str | None = None
    requirements: list[str] = field(default_factory=list)
    fits: list[str] = field(default_factory=list)
    tolerance_grade: int | None = None
    general_grade: int | None = None
    general_tolerance_class: str | None = None
    ra_min: float | None = None
    threads: list[str] = field(default_factory=list)
    thread_count: int = 0
    hole_count: int = 0
    has_form_tolerances: bool = False
    coating: str = ""
    heat_treatment: str = ""

    @property
    def blank(self) -> str | None:
        """Вид заготовки по сортаменту: круг, лист, шестигранник…"""
        return self.sortament.split()[0].casefold() if self.sortament else None

    @property
    def external_operations(self) -> list[str]:
        """Внешние (кооперационные) операции по техническим требованиям."""
        ops = []
        if self.heat_treatment:
            ops.append(f"термообработка: {self.heat_treatment}")
        if self.coating:
            ops.append(f"покрытие: {self.coating}")
        return ops


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _title_block(lines: list[str], data: EskdData) -> None:
    for i, line in enumerate(lines):
        if not _SHEETS.search(line):
            continue
        material, j = [], i + 1
        while j < len(lines) and j < i + 6 and not _SCALE.match(lines[j]):
            if lines[j].strip():
                material.append(" ".join(lines[j].split()))
            j += 1
        if j >= len(lines) or not _SCALE.match(lines[j]):
            return
        data.scale = _SCALE.match(lines[j]).group(1).replace(" ", "")
        rest = [x.strip() for x in lines[j + 1:j + 8] if x.strip()]
        if rest and _NUMBER.match(rest[0]):
            data.mass_kg = _num(_NUMBER.match(rest[0]).group(1))
            rest = rest[1:]
        if rest and not _REQUIREMENT.match(rest[0]):
            data.designation = rest[0]
            if len(rest) > 1 and not _REQUIREMENT.match(rest[1]):
                data.name = rest[1]
        material = [m for m in material if re.search(f"[{_LET}]", m)]
        if len(material) >= 2 and _SORTAMENT.match(material[0]):
            data.sortament, data.material = material[0], " ".join(material[1:])
        elif material:
            data.material = " ".join(material)
        return


def _requirements(lines: list[str], data: EskdData) -> None:
    for line in lines:
        m = _REQUIREMENT.match(line)
        if m and re.search(f"[{_LET}]", m.group(2)):
            data.requirements.append(m.group(2))
    for req in data.requirements:
        clean = req.rstrip(".").strip()
        coating = _COATING.search(req)
        if coating and not data.coating:
            data.coating = coating.group(1).rstrip(".").strip()
        elif _PAINT.search(req) and not data.coating:
            data.coating = clean
        if _HEAT.search(req) and not data.heat_treatment:
            data.heat_treatment = clean
        if re.search(r"неуказ|общие допуски", req, re.I):
            general = _GENERAL_CLASS.search(req)
            grades = [int(g) for _, g in _FIT.findall(req)] + [int(g) for g in _IT.findall(req)]
            if general:
                data.general_tolerance_class = general.group(1).lower()
                data.general_grade = GENERAL_CLASS_GRADE[data.general_tolerance_class]
            elif grades:
                data.general_grade = min(grades)


def parse_eskd(text: str) -> EskdData:
    data = EskdData()
    lines = text.splitlines()
    _title_block(lines, data)
    _requirements(lines, data)
    fmt = _FORMAT.search(text)
    data.sheet_format = fmt.group(1).replace("A", "А") if fmt else None

    threads, count = [], 0
    for m in _THREAD.finditer(text):
        value = "M" + m.group(2).replace(",", ".")
        if m.group(3):
            value += "x" + m.group(3).replace(",", ".")
        if m.group(4):
            value += "-" + m.group(4).translate(_CYR_TO_LAT)
        threads.append(value)
        count += int(m.group(1)) if m.group(1) else 1
    data.threads, data.thread_count = list(dict.fromkeys(threads)), count
    data.hole_count = sum(int(n) for n in _HOLES.findall(text))

    rest = _THREAD.sub(" ", text)
    for value in filter(None, (data.material, data.sortament)):
        rest = rest.replace(value, " ")
    data.fits = list(dict.fromkeys(f"{a}{b}" for a, b in _FIT.findall(rest) if 1 <= int(b) <= 18))
    grades = [int(b) for _, b in _FIT.findall(rest) if 1 <= int(b) <= 18] + [int(g) for g in _IT.findall(rest)]
    data.tolerance_grade = min(grades) if grades else data.general_grade
    roughness = [_num(v) for v in _RA.findall(text)]
    data.ra_min = min(roughness) if roughness else None
    data.has_form_tolerances = bool(_GDT_SYMBOLS.search(text)) or any(_GDT_TEXT.match(x) for x in lines)
    return data
