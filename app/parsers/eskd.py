"""Чтение чертежа детали по ЕСКД из текстового слоя PDF (КОМПАС, SolidWorks).

ГОСТ 2.104 — основная надпись (форма 1): графа 1 наименование, 2 обозначение, 3 материал
(для проката — дробью: сортамент / марка), 5 масса, 6 масштаб, 7–8 лист / листов.
ГОСТ 2.201 — обозначение изделия (децимальный номер): КПУ.741645.001, XWS.65.A.1.P000089.
ГОСТ 2.316 — технические требования: в КОМПАС пункты «1. …», в SolidWorks номер приклеен в конце строки.
ГОСТ 25346/25347 — квалитеты и поля допусков; ГОСТ 30893.1 — общие допуски (классы f, m, c, v).
ГОСТ 2.309 — шероховатость Ra; ГОСТ 2.308 — допуски формы и расположения; ГОСТ 2.311 — резьбы.
ГОСТ 9.306 / 9.032 — покрытия; термообработка — по ТТ (HRC, HB, закалка, цементация…).
В PDF из КОМПАС знак диаметра «Ø» приходит буквой «n».
"""
import re
from collections import Counter
from dataclasses import dataclass, field

_LET = "A-Za-zА-Яа-яЁё"
_SHEETS = re.compile(r"Листов\s*$")
_SCALE = re.compile(r"^\s*(\d+(?:[.,]\d+)?\s*:\s*\d+(?:[.,]\d+)?)\s*$")
_NUMBER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
_MASS = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(кг|г)\s*$")
_FORMAT = re.compile(r"Формат\s*([АA]\d)")
_REQUIREMENT = re.compile(r"^\s*(\d{1,2})\.\s+(\S.*?)\s*$")
_TT_WORDS = re.compile(r"допуск|отклонен|кромк|размер|покрыт|обработк|маркир|притуп|заусен|HRC|резьб|шероховат"
                       r"|термообр|закал", re.I)
_TRAILING_ITEM = re.compile(rf"^(.*?(?:[{_LET}.)*]|IT\d{{1,2}}/2))(\d{{1,2}})\.?$")
_SORTAMENT = re.compile(r"^(Круг|Пруток|Шестигранник|Квадрат|Полоса|Лист|Плита|Труба|Лента|Уголок|Проволока)\b", re.I)
_DESIGNATION = re.compile(r"(?<![\w.])([A-ZА-ЯЁ]{2,6}(?:\.[A-ZА-ЯЁ0-9]{1,10}){2,}(?:-\d{1,3})?)(?![\w.])")
_GOST_MATERIAL = re.compile(r"^[A-ZА-ЯЁ][^:;]{0,40}?\s(?:ГОСТ|ТУ|OCT|ОСТ)\s?[\d.\-–]+")
_BRAND = re.compile(r"\bAW[- ]?\d{4}(?:\s?T\d+)?|\bAISI\s?\d{3}[A-Z]?|\bEN\s?AW[- ]?\d{4}")
_NAME = re.compile(rf"^[A-ZА-ЯЁ][а-яёa-z]+(?:[ \-][{_LET}]+){{0,4}}$")
_STAMP_WORDS = {"копировал", "формат", "масштаб", "масса", "лист", "листов", "шифр", "файл", "версия", "чертил",
                "лицевая", "изм", "дата", "подп", "подпись", "утв", "лит", "справ", "взам", "перв", "инв", "разраб", "пров"}
_FIT = re.compile(rf"(?<![{_LET}])(js|JS|[HhKkNnPpGgFfEeDdSsUu])(\d{{1,2}})(?![0-9{_LET}])")
_PIPE_THREAD = re.compile(r"\bG\s?\d+(?:/\d+)?(?:\s?[AB])?\b")
_IT = re.compile(r"\bIT\s?(\d{1,2})\b")
_GENERAL_CLASS = re.compile(r"30893\.1\s*[-–—]\s*([fmcv])\b", re.I)
GENERAL_CLASS_GRADE = {"f": 12, "m": 14, "c": 16, "v": 17}  # приближённое соответствие классов ГОСТ 30893.1
_RA = re.compile(r"\bRa\s*(\d+(?:[.,]\d+)?)")
_COUNT = r"(?<![\d.,хx/])(\d{1,2})\s*отв"  # «1х453 отв.» — склейка «1х45» и «3 отв.», не 453
_THREAD = re.compile(
    rf"(?:{_COUNT}\.?\s*)?(?<![{_LET}])[MМ](\d+(?:[.,]\d+)?)(?:\s*[x×хX]\s*(\d+(?:[.,]\d+)?))?"
    rf"(?:-(\d[{_LET}]{{1,2}}))?(?=$|[MМ]\d|[^{_LET}\d])")  # «M6M6» — две склеенные выноски КОМПАС
_HOLES = re.compile(_COUNT + r"\b", re.I)
_GDT_SYMBOLS = re.compile("[⏤⏥⌓⌒⊥∥∠⌯⌖◎○⌭↗⟂]")
_GDT_TEXT = re.compile(rf"^\s*[{_LET}]?\s*\d+[.,]\d+\s+[А-ЯA-Z]{{1,2}}\s*$")
_COATING = re.compile(r"Покрытие\s*[:\-]?\s*(.*)", re.I)
_STANDARD = re.compile(r"\s+(?:ГОСТ|ТУ|OCT|ОСТ|GOST|DIN|EN|ISO)(?![A-ZА-ЯЁ]).*$")
# ГОСТ 2.302: масштабы уменьшения, натуральный и увеличения.
STANDARD_SCALES = {"1:1", "1:2", "1:2.5", "1:4", "1:5", "1:10", "1:15", "1:20", "1:25", "1:40", "1:50", "1:75",
                   "1:100", "1:200", "1:400", "1:500", "1:800", "1:1000", "2:1", "2.5:1", "4:1", "5:1", "10:1",
                   "20:1", "40:1", "50:1", "100:1"}
_PAINT = re.compile(r"\b(Эмаль|Краска|Грунт|Лак)\b", re.I)
_HEAT = re.compile(r"\bHRC|\bHB\b|\bHV\b|Закал|Цементир|Азотир|Нормализ|Отпуст|Улучш|Термообр", re.I)
_CYR_TO_LAT = str.maketrans({"Н": "H", "н": "h", "Е": "E", "е": "e", "М": "M"})
_TOLERANCE_TYPOS = str.maketrans({"Н": "H", "Т": "T", "І": "I"})
_LAT_TO_CYR = str.maketrans("ACEHKMOPTXBacepox", "АСЕНКМОРТХВасерох")


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


def _brand(material: str) -> str:
    """Графа 3 — только марка: «ЛС 59-1 ГОСТ 15527-2004» → «ЛС 59-1» (стандарт не читаем)."""
    return _STANDARD.sub("", " ".join(material.split())).strip()


def _scale(text: str) -> str | None:
    value = text.replace(" ", "").replace(",", ".")
    return value if value in STANDARD_SCALES else None  # «1:14.50» — склейка масштаба и массы


def _fix_scripts(text: str) -> str:
    """«ЛC 59-1» (латинская C в русской марке) → «ЛС 59-1»."""
    words = []
    for word in text.split():
        mixed = re.search("[А-Яа-яЁё]", word) and re.search("[A-Za-z]", word)
        words.append(word.translate(_LAT_TO_CYR) if mixed else word)
    return " ".join(words)


def _title_block_kompas(lines: list[str], data: EskdData) -> None:
    """КОМПАС: после «Лист … Листов» — материал, масштаб, масса, обозначение, наименование."""
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
        data.scale = _scale(_SCALE.match(lines[j]).group(1))
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
            data.sortament, data.material = material[0], _brand(" ".join(material[1:]))
        elif material:
            data.material = _brand(" ".join(material))
        return


def _title_block_any(lines: list[str], data: EskdData, file_stem: str | None) -> None:
    """Другие системы (SolidWorks): поля основной надписи узнаются по виду, а не по порядку."""
    text = "\n".join(lines)
    numbers = Counter(m.group(1) for m in _DESIGNATION.finditer(text))
    if not data.designation and numbers:
        data.designation = numbers.most_common(1)[0][0]
    if not data.name:
        stem = " ".join((file_stem or "").split())
        if data.designation and stem.casefold().startswith(data.designation.casefold() + " "):
            data.name = re.sub(r"\s*\(\d+\)$", "", stem[len(data.designation):]).strip() or None
    if not data.name:
        names = [x.strip() for x in lines if _NAME.match(x.strip())
                 and x.strip().split()[0].casefold().rstrip(".") not in _STAMP_WORDS]
        file_line = next((x.casefold() for x in lines if x.strip().startswith("Файл")), "")
        stamp = next((i for i, x in enumerate(lines) if x.strip().startswith("Утв")), 0)
        after = [x for i, x in enumerate(lines) if i > stamp and x.strip() in names]
        data.name = next((n for n in names if n.casefold() in file_line), None) or (after or names or [None])[0]
        data.name = data.name.strip() if data.name else None
    if not data.material:
        candidates = [x.strip() for x in lines if _GOST_MATERIAL.match(x.strip()) and not _TT_WORDS.search(x)]
        brand = _BRAND.search(text)
        material = candidates[0] if candidates else (brand.group(0) if brand else None)
        data.material = _brand(_fix_scripts(material)) if material else None
    if data.mass_kg is None:
        mass = next((_MASS.match(x) for x in lines if _MASS.match(x)), None)
        if mass:
            data.mass_kg = round(_num(mass.group(1)) / (1000 if mass.group(2) == "г" else 1), 6)
    if not data.scale:
        data.scale = next((_scale(m.group(1)) for m in map(_SCALE.match, lines) if m and _scale(m.group(1))), None)


def _clean_requirement(line: str) -> str:
    """«Острые кромки притупить5» → «Острые кромки притупить» (номер пункта SolidWorks приклеен в конце)."""
    line = " ".join(line.split())
    m = _TRAILING_ITEM.match(line.translate(_TOLERANCE_TYPOS))
    return line[:len(m.group(1))].rstrip() if m else line


def _coating(lines: list[str]) -> str:
    """«Покрытие: ан. окс.7.» — номер пункта отрезается; «Покрытие: 7.» — текст на следующей строке."""
    for i, line in enumerate(lines):
        m = _COATING.search(line)
        if not m:
            continue
        value = re.sub(r"\.?\s*\d{1,2}\.?$", "", m.group(1).strip()).rstrip(". ")
        if len(re.findall(f"[{_LET}]", value)) < 3:
            following = [x.strip() for x in lines[i + 1:i + 4] if x.strip()]
            value = following[0] if following else ""
            if len(following) > 1 and re.fullmatch(r"(?:дм|см|м|мм)[²2]", following[1]):
                value += " " + following[1]  # «площадь покрытия 7.7» + «дм²»
            value = re.sub(r",\s*,", ",", " ".join(value.split())).rstrip(". ")
        if len(re.findall(f"[{_LET}]", value)) >= 3:
            return value
    return ""


def _requirements(lines: list[str], data: EskdData) -> None:
    data.coating = _coating(lines)
    for line in lines:
        m = _REQUIREMENT.match(line)
        if m and re.search(f"[{_LET}]", m.group(2)):
            data.requirements.append(m.group(2))
        elif not m and _TT_WORDS.search(line) and len(line.strip()) >= 12 and not line.strip()[0].isdigit():
            data.requirements.append(_clean_requirement(line))
    data.requirements = list(dict.fromkeys(data.requirements))
    for req in data.requirements:
        clean = req.rstrip(".").strip()
        if _PAINT.search(req) and not data.coating and not _COATING.search(req):
            data.coating = clean
        if _HEAT.search(req) and not data.heat_treatment:
            data.heat_treatment = clean
        if re.search(r"неуказ|общие допуски", req, re.I) and not re.search(r"резьб|формы", req, re.I):
            norm = re.sub(r"([HhJjSs])l(\d)", r"\g<1>1\2", req.translate(_TOLERANCE_TYPOS))  # «hl4» → «h14»
            norm = re.sub(r"\b1T(\d)", r"IT\1", norm)  # «1Т14» → «IT14»
            general = _GENERAL_CLASS.search(norm)
            grades = [int(g) for _, g in _FIT.findall(norm)] + [int(g) for g in _IT.findall(norm)]
            if general:
                data.general_tolerance_class = general.group(1).lower()
                data.general_grade = GENERAL_CLASS_GRADE[data.general_tolerance_class]
            elif grades:
                data.general_grade = min(grades)


def parse_eskd(text: str, file_stem: str | None = None) -> EskdData:
    data = EskdData()
    lines = text.splitlines()
    _title_block_kompas(lines, data)
    _title_block_any(lines, data, file_stem)
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

    # Посадки — без резьб (допуски резьбы «6H/6g»), трубных резьб «G1/4» и марки материала.
    rest = "\n".join(x for x in lines if not re.search("резьб", x, re.I))
    rest = _PIPE_THREAD.sub(" ", _THREAD.sub(" ", rest))
    for value in filter(None, (data.material, data.sortament)):
        rest = rest.replace(value, " ")
    data.fits = list(dict.fromkeys(f"{a}{b}" for a, b in _FIT.findall(rest) if 1 <= int(b) <= 18))
    grades = [int(b) for _, b in _FIT.findall(rest) if 1 <= int(b) <= 18] + [int(g) for g in _IT.findall(rest)]
    data.tolerance_grade = min(grades) if grades else data.general_grade
    roughness = [_num(v) for v in _RA.findall(text)]
    data.ra_min = min(roughness) if roughness else None
    data.has_form_tolerances = bool(_GDT_SYMBOLS.search(text)) or any(_GDT_TEXT.match(x) for x in lines)
    return data
