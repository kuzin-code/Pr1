"""Заготовка для заготовительной операции: сортамент, размеры, количество — строка для заготовителя.

Система предлагает заготовку сама (пометка «предложено системой»), технолог исправляет («задано технологом»):
- сортамент — из чертежа (графа 3, прокат дробью, ФТ-04); если не прочитан — по технологии:
  токарная — круг, фрезерная — лист/плита;
- размеры — от габаритов детали из STEP плюс припуски; толщина листа и диаметр круга округляются вверх
  до стандартного ряда. Припуски и ряды — параметр «blank_rules» (data/seed/parameters.json), не код;
- размер проката, указанный в чертеже («Круг 30 ГОСТ 2590»), важнее расчётного;
- нет габаритов (STEP не прочитан, несколько тел) или правил — «заготовка не определена, задаёт технолог».
Количество — по количеству позиции: одна заготовка на деталь.
"""
import math
import re
from dataclasses import dataclass, field

from app.domain.models import Dimensions

SYSTEM, TECHNOLOGIST = "system", "technologist"
SOURCE_LABELS = {SYSTEM: "предложено системой", TECHNOLOGIST: "задано технологом"}
UNDEFINED_TEXT = "Заготовка не определена, задаёт технолог"


@dataclass(frozen=True)
class BlankKind:
    label: str  # в строке для заготовителя
    fields: tuple[tuple[str, str], ...]  # (ключ размера, подпись в форме технолога)


# Сортамент и его размеры, мм. Порядок полей — порядок в форме технолога.
KINDS: dict[str, BlankKind] = {
    "sheet": BlankKind("лист", (("h", "Толщина h"), ("length", "Длина"), ("width", "Ширина"))),
    "round": BlankKind("круг", (("d", "Диаметр Ø"), ("length", "Длина L"))),
    "hex": BlankKind("шестигранник", (("s", "Размер под ключ S"), ("length", "Длина L"))),
    "square": BlankKind("квадрат", (("s", "Сторона"), ("length", "Длина L"))),
    "strip": BlankKind("полоса", (("h", "Толщина"), ("width", "Ширина"), ("length", "Длина L"))),
    "tube": BlankKind("труба", (("d", "Наружный Ø"), ("wall", "Стенка"), ("length", "Длина L"))),
}
KIND_TITLES = {"sheet": "лист / плита", "round": "круг", "hex": "шестигранник", "square": "квадрат",
               "strip": "полоса", "tube": "труба"}
# Поля формы технолога: (ключ, подпись, сортаменты, где поле есть).
FORM_FIELDS = (("h", "Толщина h", ("sheet", "strip")), ("d", "Диаметр Ø", ("round", "tube")),
               ("s", "Размер S / сторона", ("hex", "square")), ("wall", "Стенка", ("tube",)),
               ("length", "Длина L", tuple(KINDS)), ("width", "Ширина", ("sheet", "strip")))
# Вид проката из чертежа (EskdData.blank) → сортамент; уголок, лента, проволока — задаёт технолог.
DRAWING_KINDS = {"лист": "sheet", "плита": "sheet", "круг": "round", "пруток": "round", "шестигранник": "hex",
                 "квадрат": "square", "полоса": "strip", "труба": "tube"}
TECHNOLOGY_KINDS = {"turning": "round", "milling": "sheet"}
_DRAWING_SIZE = re.compile(r"^\S+\s+(\d+(?:[.,]\d+)?)(?![\d×xх*,.])", re.I)  # «Круг 30-В ГОСТ 2590» → 30


class BlankError(ValueError):
    pass


@dataclass(frozen=True)
class BlankRules:
    """Припуски, мм, и стандартные ряды (параметр «blank_rules»)."""

    sheet_side_allowance: float  # на сторону по контуру листа
    sheet_thickness_allowance: float  # к меньшему габариту
    bar_diameter_allowance: float  # к поперечному габариту прутка (круг)
    bar_length_allowance: float  # торцовка и ширина реза прутка
    size_step: float  # длина и размеры в плане округляются вверх до кратного, мм
    sheet_thicknesses: tuple[float, ...]
    round_diameters: tuple[float, ...]

    @classmethod
    def from_dict(cls, data: dict | None) -> "BlankRules | None":
        if not data:
            return None
        return cls(float(data["sheet_side_allowance_mm"]), float(data["sheet_thickness_allowance_mm"]),
                   float(data["bar_diameter_allowance_mm"]), float(data["bar_length_allowance_mm"]),
                   float(data["size_step_mm"]), tuple(sorted(float(v) for v in data["sheet_thicknesses_mm"])),
                   tuple(sorted(float(v) for v in data["round_diameters_mm"])))

    def to_dict(self) -> dict:
        return {"sheet_side_allowance_mm": self.sheet_side_allowance,
                "sheet_thickness_allowance_mm": self.sheet_thickness_allowance,
                "bar_diameter_allowance_mm": self.bar_diameter_allowance,
                "bar_length_allowance_mm": self.bar_length_allowance, "size_step_mm": self.size_step,
                "sheet_thicknesses_mm": list(self.sheet_thicknesses), "round_diameters_mm": list(self.round_diameters)}


@dataclass(frozen=True)
class Blank:
    material: str
    kind: str | None
    sizes: dict = field(default_factory=dict)
    quantity: int = 1
    source: str = SYSTEM
    note: str = ""
    remarks: tuple[str, ...] = ()  # откуда взято, что проверить

    @property
    def defined(self) -> bool:
        if self.kind not in KINDS:
            return False
        return all((self.sizes.get(key) or 0) > 0 for key, _ in KINDS[self.kind].fields)

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS[self.source]

    @property
    def text(self) -> str:
        """Строка для заготовителя: «Д16Т, лист h25 мм, 100×100 мм — 25 шт.»."""
        if not self.defined:
            return UNDEFINED_TEXT
        s = {k: fmt(v) for k, v in self.sizes.items()}
        sizes = {
            "sheet": f"h{s.get('h')} мм, {s.get('length')}×{s.get('width')} мм",
            "round": f"Ø{s.get('d')} мм, L {s.get('length')} мм",
            "hex": f"S{s.get('s')} мм, L {s.get('length')} мм",
            "square": f"{s.get('s')}×{s.get('s')} мм, L {s.get('length')} мм",
            "strip": f"{s.get('h')}×{s.get('width')} мм, L {s.get('length')} мм",
            "tube": f"Ø{s.get('d')}×{s.get('wall')} мм, L {s.get('length')} мм",
        }[self.kind]
        return f"{self.material or 'материал не указан'}, {KINDS[self.kind].label} {sizes} — {self.quantity} шт."

    def to_dict(self) -> dict:
        """Для хранения в данных позиции (материал — всегда текущий материал позиции)."""
        return {"kind": self.kind, "sizes": dict(self.sizes), "quantity": self.quantity, "source": self.source,
                "note": self.note}

    @classmethod
    def from_dict(cls, data: dict, material: str) -> "Blank":
        return cls(material, data.get("kind"), {k: float(v) for k, v in (data.get("sizes") or {}).items()},
                   int(data.get("quantity") or 1), data.get("source") or TECHNOLOGIST, data.get("note") or "")


def fmt(value: float) -> str:
    """25.0 → «25», 1.5 → «1,5»."""
    return f"{value:g}".replace(".", ",")


def round_up(value: float, series: tuple[float, ...]) -> float | None:
    """Ближайшее значение ряда не меньше value; больше ряда — None (размер задаёт технолог)."""
    return next((v for v in sorted(series) if v >= value - 1e-9), None)


def _ceil(value: float, step: float) -> float:
    if step <= 0:
        return round(value, 2)
    return round(math.ceil(round(value / step, 6)) * step, 6)


def drawing_size(sortament: str | None) -> float | None:
    """Размер проката сразу после вида в графе 3: «Круг 30-В ГОСТ 2590-2006» → 30, «Лист 25 ГОСТ…» → 25."""
    match = _DRAWING_SIZE.match((sortament or "").strip())
    return float(match.group(1).replace(",", ".")) if match else None


def _bar_axis(dims: Dimensions) -> tuple[float, float]:
    """(поперечный габарит, длина) прутка: два близких габарита — сечение, третий — ось детали."""
    a, b, c = dims.sorted()
    if a - b < b - c:
        return a, c  # диск: сечение a×b, ось — меньший габарит
    return b, a  # вал: ось — больший габарит


def suggest_blank(dims: Dimensions | None, material: str, technology: str, quantity: int, rules: BlankRules | None,
                  drawing_blank: str | None = None, drawing_sortament: str | None = None) -> Blank:
    """Заготовка, предложенная системой (см. описание модуля)."""
    remarks: list[str] = []
    word = (drawing_blank or "").casefold() or None
    if word:
        kind = DRAWING_KINDS.get(word)
        remarks.append(f"сортамент по чертежу: {drawing_sortament or word}")
        if kind is None:
            remarks.append(f"вид проката «{word}» система не размечает: заготовку задаёт технолог")
            return Blank(material, None, {}, quantity, SYSTEM, "", tuple(remarks))
    else:
        kind = TECHNOLOGY_KINDS.get(technology or "milling", "sheet")
        remarks.append(f"сортамент по технологии: {'токарная' if kind == 'round' else 'фрезерная'}")
    if dims is None:
        remarks.append("нет габаритов детали из STEP: размеры задаёт технолог")
        return Blank(material, kind, {}, quantity, SYSTEM, "", tuple(remarks))
    if rules is None:
        remarks.append("не заданы припуски заготовки в справочнике: размеры задаёт технолог")
        return Blank(material, kind, {}, quantity, SYSTEM, "", tuple(remarks))
    from_drawing = drawing_size(drawing_sortament) if word else None
    sizes: dict[str, float] = {}
    if kind == "sheet":
        length, width, thick = dims.sorted()
        need = thick + rules.sheet_thickness_allowance
        h = from_drawing or round_up(need, rules.sheet_thicknesses)
        if from_drawing:
            remarks.append(f"толщина по чертежу: {fmt(from_drawing)} мм")
            if from_drawing < thick:
                remarks.append(f"толщина по чертежу меньше габарита детали {fmt(thick)} мм — проверьте")
        elif h is None:
            remarks.append(f"толщина {fmt(need)} мм больше стандартного ряда: задаёт технолог")
        if h:
            sizes = {"h": h, "length": _ceil(length + 2 * rules.sheet_side_allowance, rules.size_step),
                     "width": _ceil(width + 2 * rules.sheet_side_allowance, rules.size_step)}
    elif kind in ("round", "hex", "square"):
        cross, axis = _bar_axis(dims)
        length = _ceil(axis + rules.bar_length_allowance, rules.size_step)
        key = "d" if kind == "round" else "s"
        size = from_drawing
        if from_drawing:
            remarks.append(f"размер проката по чертежу: {fmt(from_drawing)} мм")
            if kind == "round" and from_drawing < cross:
                remarks.append(f"диаметр по чертежу меньше габарита детали {fmt(cross)} мм — проверьте")
        elif kind == "round":
            need = cross + rules.bar_diameter_allowance
            size = round_up(need, rules.round_diameters)
            if size is None:
                remarks.append(f"диаметр {fmt(need)} мм больше стандартного ряда: задаёт технолог")
        else:
            remarks.append("размер сечения по габаритам не определить: задаёт технолог")
        if size:
            sizes = {key: size, "length": length}
    else:
        remarks.append("размеры сечения по габаритам не определить: задаёт технолог")
    return Blank(material, kind, sizes, quantity, SYSTEM, "", tuple(remarks))


def manual_blank(material: str, kind: str, sizes: dict, quantity, note: str = "") -> Blank:
    """Заготовка, заданная технологом: сортамент из перечня, все размеры сортамента положительные."""
    if kind not in KINDS:
        raise BlankError("Выберите сортамент заготовки")
    clean = {}
    for key, label in KINDS[kind].fields:
        raw = str(sizes.get(key) if sizes.get(key) is not None else "").replace(",", ".").strip()
        try:
            value = float(raw)
        except ValueError:
            raise BlankError(f"{label}: укажите размер, мм") from None
        if not math.isfinite(value) or value <= 0:
            raise BlankError(f"{label}: размер должен быть больше нуля")
        clean[key] = value
    try:
        count = int(str(quantity).strip())
    except ValueError:
        raise BlankError("Количество заготовок — целое число") from None
    if count < 1:
        raise BlankError("Количество заготовок — не меньше 1")
    return Blank(material, kind, clean, count, TECHNOLOGIST, (note or "").strip())
