"""Заготовка для заготовительной операции: сортамент, размеры, количество — строка для заготовителя.

Система предлагает заготовку сама (пометка «предложено системой»), технолог исправляет («задано технологом»):
- сортамент — из чертежа (графа 3, прокат дробью, ФТ-04); если не прочитан — по технологии:
  токарная — круг, фрезерная — лист/плита;
- размеры — от габаритов детали из STEP плюс припуск на сторону (×2): лист — контур и толщина, круг — Ø и длина;
  толщина листа и диаметр круга округляются вверх до стандартного ряда (ряд диаметров — по группе материала:
  сталь, алюминиевые сплавы, латунь). Припуски и ряды — параметр «blank_rules» (data/seed/parameters.json), не код;
- размер проката, указанный в чертеже («Круг 30 ГОСТ 2590»), важнее расчётного;
- заготовку система определяет сама по этому правилу, технолог лишь правит (решение пользователя 28.10.2026):
  спорное место (пластик, нет своего ряда диаметров — берётся ряд стали, размер вне ряда, размер проката из чертежа
  меньше детали, шестигранник / квадрат / полоса без размера в чертеже) идёт в замечание «проверьте»;
- «не определена, задаёт технолог» — только без габаритов из STEP, без правил, труба и вид проката, который
  система не размечает (уголок, лента, проволока).
Количество — по количеству позиции: одна заготовка на деталь.
"""
import math
import re
from dataclasses import dataclass, field

from app.domain.materials import material_group
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
# Группы материала с рядом диаметров круга (materials.py); бронза и медь в группе «латунь» ряда не имеют.
ROUND_GROUPS = {"steel": "сталь", "stainless": "нержавеющая сталь", "aluminium": "алюминиевые сплавы",
                "brass": "латунь"}
_BRASS = re.compile(r"латун|(?<![а-яa-z])лс\s?\d|(?<![а-яa-z])л\d{2}", re.I)
_DRAWING_SIZE = re.compile(r"^\S+\s+(\d+(?:[.,]\d+)?)(?![\d×xх*,.])", re.I)  # «Круг 30-В ГОСТ 2590» → 30


class BlankError(ValueError):
    pass


@dataclass(frozen=True)
class BlankRules:
    """Припуски на сторону, мм (к размеру прибавляются дважды), и стандартные ряды (параметр «blank_rules»)."""

    sheet_side_allowance: float  # лист: на сторону по контуру
    sheet_thickness_allowance: float  # лист: на сторону по толщине
    bar_diameter_allowance: float  # круг: на сторону по диаметру
    bar_length_allowance: float  # круг: на торец по длине
    size_step: float  # длина и размеры в плане округляются вверх до кратного, мм
    sheet_thicknesses: tuple[float, ...]
    round_diameters: dict  # группа материала → ряд диаметров; пустой ряд — задаёт технолог

    @classmethod
    def from_dict(cls, data: dict | None) -> "BlankRules | None":
        """Параметр прежнего вида (припуски не на сторону, один ряд диаметров) не читается — None."""
        try:
            series = data["round_diameters_mm"]
            if not isinstance(series, dict):
                return None
            return cls(float(data["sheet_side_allowance_mm"]), float(data["sheet_thickness_side_allowance_mm"]),
                       float(data["bar_diameter_side_allowance_mm"]), float(data["bar_length_side_allowance_mm"]),
                       float(data["size_step_mm"]), tuple(sorted(float(v) for v in data["sheet_thicknesses_mm"])),
                       {g: tuple(sorted(float(v) for v in series.get(g) or [])) for g in ROUND_GROUPS})
        except (TypeError, KeyError, ValueError):
            return None

    def to_dict(self) -> dict:
        return {"sheet_side_allowance_mm": self.sheet_side_allowance,
                "sheet_thickness_side_allowance_mm": self.sheet_thickness_allowance,
                "bar_diameter_side_allowance_mm": self.bar_diameter_allowance,
                "bar_length_side_allowance_mm": self.bar_length_allowance, "size_step_mm": self.size_step,
                "sheet_thicknesses_mm": list(self.sheet_thicknesses),
                "round_diameters_mm": {g: list(v) for g, v in self.round_diameters.items()}}


def round_group(material: str) -> str:
    """Группа материала для ряда диаметров: в группе «латунь» — только латунь (не бронза и не медь)."""
    group = material_group(material)
    if group == "brass" and not _BRASS.search(material or ""):
        return "bronze"
    return group


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


def _bar_axis(dims: Dimensions) -> tuple[float, float, float]:
    """(больший и меньший поперечные габариты, длина) прутка: два близких габарита — сечение, третий — ось."""
    a, b, c = dims.sorted()
    if a - b < b - c:
        return a, b, c  # диск: сечение a×b, ось — меньший габарит
    return b, c, a  # вал: ось — больший габарит


def suggest_blank(dims: Dimensions | None, material: str, technology: str, quantity: int, rules: BlankRules | None,
                  drawing_blank: str | None = None, drawing_sortament: str | None = None) -> Blank:
    """Заготовка, предложенная системой по правилу (см. описание модуля). Спорное место не останавливает
    предложение — оно идёт в замечания «проверьте», технолог правит при необходимости. Не определена — только без
    габаритов, без правил или для вида проката, который система не размечает."""
    remarks: list[str] = []

    def undefined(kind: str | None, why: str) -> Blank:
        return Blank(material, kind, {}, quantity, SYSTEM, "", tuple(remarks + [why]))

    def made(kind: str, sizes: dict) -> Blank:
        return Blank(material, kind, sizes, quantity, SYSTEM, "", tuple(remarks))

    word = (drawing_blank or "").casefold() or None
    if word:
        kind = DRAWING_KINDS.get(word)
        remarks.append(f"сортамент по чертежу: {drawing_sortament or word}")
        if kind is None:
            return undefined(None, f"вид проката «{word}» — задаёт технолог")
    else:
        kind = TECHNOLOGY_KINDS.get(technology or "milling", "sheet")
        remarks.append(f"сортамент по технологии: {'токарная' if kind == 'round' else 'фрезерная'}")
    if dims is None:
        return undefined(kind, "нет габаритов детали из STEP — задаёт технолог")
    if rules is None:
        return undefined(kind, "не заданы припуски заготовки в справочнике — задаёт технолог")
    if not (material or "").strip():
        remarks.append("материал не указан — проверьте")
    group = round_group(material)
    if group == "plastic":
        remarks.append("пластик — проверьте наличие материала нужной толщины")
    from_drawing = drawing_size(drawing_sortament) if word else None
    step = rules.size_step

    def by_series(need: float, series: tuple[float, ...], what: str) -> float:
        value = round_up(need, series) if series else None
        if value is None:
            remarks.append(f"{what} {fmt(need)} мм вне стандартного ряда — проверьте")
            return _ceil(need, step)
        return value

    if kind == "sheet":
        length, width, thick = dims.sorted()
        need = thick + 2 * rules.sheet_thickness_allowance
        if from_drawing and from_drawing >= thick:
            remarks.append(f"толщина по чертежу: {fmt(from_drawing)} мм")
            h = from_drawing
        else:
            if from_drawing:
                remarks.append(f"толщина по чертежу {fmt(from_drawing)} мм меньше детали — взята расчётная, проверьте")
            h = by_series(need, rules.sheet_thicknesses, "толщина")
        return made(kind, {"h": h, "length": _ceil(length + 2 * rules.sheet_side_allowance, step),
                           "width": _ceil(width + 2 * rules.sheet_side_allowance, step)})
    if kind == "tube":
        return undefined(kind, "труба: стенку по габаритам не определить — задаёт технолог")
    cross, flats, axis = _bar_axis(dims)
    length = _ceil(axis + 2 * rules.bar_length_allowance, step)
    if kind == "strip":
        _, width, h = dims.sorted()
        remarks.append("размеры полосы по габаритам детали — проверьте")
        return made(kind, {"h": _ceil(h + 2 * rules.sheet_thickness_allowance, step),
                           "width": _ceil(width + 2 * rules.sheet_side_allowance, step),
                           "length": _ceil(dims.sorted()[0] + 2 * rules.bar_length_allowance, step)})
    least = cross if kind == "round" else flats  # шестигранник и квадрат: в габарите — размер по углам / сторона
    key = "d" if kind == "round" else "s"
    if from_drawing and from_drawing >= least:
        remarks.append(f"размер проката по чертежу: {fmt(from_drawing)} мм")
        return made(kind, {key: from_drawing, "length": length})
    if from_drawing:
        remarks.append(f"размер по чертежу {fmt(from_drawing)} мм меньше детали — взят расчётный, проверьте")
    need = least + 2 * rules.bar_diameter_allowance
    if kind != "round":
        remarks.append(f"{KINDS[kind].label}: размер по габаритам детали — проверьте")
        return made(kind, {key: _ceil(need, step), "length": length})
    series = rules.round_diameters.get(group) or ()
    if series:
        remarks.append(f"ряд диаметров: {ROUND_GROUPS[group]}")
    else:
        series = rules.round_diameters.get("steel") or ()
        remarks.append("ряд диаметров стали (ГОСТ 2590): своего ряда для материала нет — проверьте")
    return made(kind, {"d": by_series(need, series, "диаметр"), "length": length})


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
