"""Цена заготовки по прайсу поставщика (prutki.ru, решение пользователя 29.10.2026).

Прайс — цена за один кусок-заготовку с НДС (ставка — из шапки файла) и остаток на складе. Прайс загружают
вручную в «Справочниках»: приложение само в интернет не ходит (НФТ-01).
- Марка детали → марка прайса — таблица соответствия (параметр «blank_price_rules», правится в справочнике):
  6061 / 6082 → Д16Т; AISI 304 / 12Х18Н10Т: круг — 12Х18Н10Т, лист и прочее — AISI 304 и т. д.
  Чего нет в таблице (поликарбонат, AISI 316, бронза, титан) — «марки нет в прайсе», цену задаёт технолог.
- Сортамент заготовки → формы прайса: лист — лист и плита; круг — пруток, круг, стержень; шестигранник,
  квадрат, полоса — одноимённые. Труба не подбирается.
- Раскрой куска (дополнение пользователя 29.10.2026: «кусок 8×100×300 — не больше 22 заготовок»): лист и плита —
  толщина не меньше, в плане простая сетка max(⌊W/a⌋·⌊L/b⌋, ⌊W/b⌋·⌊L/a⌋) с резом между заготовками (параметр,
  по умолчанию 0); круг, шестигранник, квадрат — сечение не меньше, ⌊длина куска / длина заготовки⌋.
- Нужно кусков = ⌈(количество заготовок + запас на брак, по умолчанию 3) / заготовок из куска⌉;
  стоимость = цена куска × кусков; из подходящих кусков (крупные тоже) — с наименьшей стоимостью.
  Остаток меньше нужного — предупреждение «на складе N шт.», не запрет. Доставка не считается.
"""
import re
from dataclasses import dataclass

from app.domain.blank import KINDS, Blank

SUPPLIER = "prutki.ru"
# Сортамент заготовки → формы прайса.
FORMS = {"sheet": ("лист", "плита"), "round": ("пруток", "круг", "стержень"), "hex": ("шестигранник",),
         "square": ("квадрат",), "strip": ("полоса",)}
KIND_BY_LABEL = {KINDS[k].label: k for k in FORMS}  # «круг» → round (в таблице марок)

# Таблица соответствия марок по умолчанию (решение пользователя 29.10.2026); D16T — латинское написание Д16Т.
DEFAULT_RULES = {
    "scrap_pieces": 3,  # запас заготовок на брак, шт. на позицию
    "price_vat_percent": 22,  # НДС в ценах прайса, если ставки нет в шапке файла
    "kerf_mm": 0,  # ширина реза между заготовками (пример пользователя 8×100×300 → 22 заготовки — без реза)
    "grades": [
        {"aliases": ["6061", "6082", "AW 6061 T6", "Сплав 6061", "AW 6082 T6", "Д16Т", "D16T"], "grade": "Д16Т"},
        {"aliases": ["АМг6"], "grade": "АМг6"},
        {"aliases": ["AISI 304", "12Х18Н10Т", "12Х18Н9Т", "AISI 321"], "grade": "AISI 304",
         "by_kind": {"round": "12Х18Н10Т"}},
        {"aliases": ["ЛС59-1", "ЛС 59-1"], "grade": "ЛС59-1"},
        {"aliases": ["М1"], "grade": "М1"},
        {"aliases": ["ПОМ-С", "POM-C", "Полиацеталь"], "grade": "ПОМ-С"},
        {"aliases": ["Ф-4", "Фторопласт-4"], "grade": "Ф-4"},
    ],
}

_LATIN = str.maketrans("abcehkmoptxy", "авсенкмортху")  # латиница, похожая на кириллицу: 12X18H10T → 12Х18Н10Т
_WORD = "0-9a-zа-яё"


class RulesError(ValueError):
    pass


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().translate(_LATIN).split())


def _has(material: str, alias: str) -> bool:
    """Марка в тексте материала отдельным словом: «Ф-4» не находится в «Ф-4К20», «М1» — в «АМг6»."""
    return re.search(rf"(?<![{_WORD}]){re.escape(_norm(alias))}(?![{_WORD}])", _norm(material)) is not None


@dataclass(frozen=True)
class GradeRule:
    aliases: tuple[str, ...]
    grade: str
    by_kind: tuple[tuple[str, str], ...] = ()  # (сортамент, марка прайса) — своя марка для сортамента

    def grade_for(self, kind: str) -> str:
        return dict(self.by_kind).get(kind, self.grade)

    def to_dict(self) -> dict:
        data = {"aliases": list(self.aliases), "grade": self.grade}
        return {**data, "by_kind": dict(self.by_kind)} if self.by_kind else data

    @classmethod
    def from_dict(cls, data: dict) -> "GradeRule":
        aliases = tuple(a.strip() for a in data.get("aliases") or [] if str(a).strip())
        grade = str(data.get("grade") or "").strip()
        by_kind = tuple(sorted((k, str(v).strip()) for k, v in (data.get("by_kind") or {}).items()))
        if not aliases or not grade or any(k not in FORMS or not v for k, v in by_kind):
            raise RulesError("Соответствие марок: укажите марки детали, марку прайса и сортамент из перечня")
        return cls(aliases, grade, by_kind)


@dataclass(frozen=True)
class PriceRules:
    grades: tuple[GradeRule, ...]
    scrap: int  # запас на брак, шт.
    default_vat: float  # НДС в ценах прайса, если ставки нет в шапке файла
    kerf: float = 0.0  # ширина реза между заготовками в куске, мм

    @classmethod
    def from_dict(cls, data: dict | None) -> "PriceRules":
        data = data or DEFAULT_RULES
        try:
            scrap, vat = int(data.get("scrap_pieces", 3)), float(data.get("price_vat_percent", 22))
            kerf = float(data.get("kerf_mm", 0))
        except (TypeError, ValueError):
            raise RulesError("Запас на брак — целое число, НДС и ширина реза — числа") from None
        if not 0 <= scrap <= 100 or not 0 <= vat < 100 or not 0 <= kerf <= 20:
            raise RulesError("Запас на брак — от 0 до 100 шт., НДС — от 0 до 100 %, ширина реза — от 0 до 20 мм")
        return cls(tuple(GradeRule.from_dict(g) for g in data.get("grades") or []), scrap, vat, kerf)

    def to_dict(self) -> dict:
        return {"scrap_pieces": self.scrap, "price_vat_percent": self.default_vat, "kerf_mm": self.kerf,
                "grades": [g.to_dict() for g in self.grades]}


def grades_text(rules: PriceRules) -> str:
    """Таблица марок для формы справочника: «6061, 6082 = Д16Т», своя марка сортамента — «; круг: 12Х18Н10Т»."""
    lines = []
    for g in rules.grades:
        extra = "".join(f"; {KINDS[k].label}: {v}" for k, v in g.by_kind)
        lines.append(f"{', '.join(g.aliases)} = {g.grade}{extra}")
    return "\n".join(lines)


def parse_grades(text: str) -> tuple[GradeRule, ...]:
    rules = []
    for n, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        left, sep, right = line.partition("=")
        parts = [p.strip() for p in right.split(";")]
        if not sep or not left.strip() or not parts[0]:
            raise RulesError(f"Соответствие марок, строка {n}: пишите «марки детали через запятую = марка прайса»")
        by_kind = {}
        for part in parts[1:]:
            label, colon, grade = (s.strip() for s in part.partition(":"))
            if not colon or label.casefold() not in KIND_BY_LABEL or not grade:
                raise RulesError(f"Соответствие марок, строка {n}: сортамент — лист, круг, шестигранник, квадрат "
                                 "или полоса, например «; круг: 12Х18Н10Т»")
            by_kind[KIND_BY_LABEL[label.casefold()]] = grade
        rules.append(GradeRule.from_dict({"aliases": left.split(","), "grade": parts[0], "by_kind": by_kind}))
    return tuple(rules)


def site_grade(material: str, kind: str, rules: PriceRules) -> str | None:
    """Марка прайса для материала детали; нет в таблице — None (цену задаёт технолог, не угадываем)."""
    for rule in rules.grades:
        if any(_has(material, a) for a in rule.aliases):
            return rule.grade_for(kind)
    return None


def _same_grade(price_grade: str, grade: str) -> bool:
    """«AISI 321 (12Х18Н10Т)» в прайсе — та же марка, что 12Х18Н10Т."""
    p, g = _norm(price_grade), _norm(grade)
    return p == g or p.endswith(f"({g})")


@dataclass(frozen=True)
class PriceItem:
    """Позиция прайса: кусок заданного размера (лист и плита — толщина × ширина × длина; пруток — Ø × длина)."""
    code: str
    name: str
    form: str
    grade: str
    dims: tuple[float, ...]
    price: float  # за кусок, с НДС
    stock: float | None = None

    @property
    def title(self) -> str:
        """«плита алюминиевая Д16Т 50×100×200 мм» — без слова «Заготовка» и с «×»."""
        return re.sub(r"\s+х\s+", "×", re.sub(r"^Заготовка\s+", "", self.name))


@dataclass(frozen=True)
class PriceList:
    date: str | None  # ISO-дата прайса из шапки
    vat_percent: float | None  # НДС в ценах, из шапки
    items: tuple[PriceItem, ...]


def _along(size: float, part: float, kerf: float) -> int:
    """Сколько заготовок длиной part помещается в size с резом kerf между ними."""
    return int((size + kerf) / (part + kerf) + 1e-9) if part > 0 else 0


def per_piece(blank: Blank, piece: PriceItem, kerf: float = 0.0) -> int:
    """Сколько заготовок выходит из куска прайса; 0 — не подходит. Лист и плита — простая сетка в плане
    (из двух поворотов — лучший, без смешанных раскладок), толщина не меньше; круг, шестигранник, квадрат —
    по длине куска, сечение не меньше."""
    s, d = blank.sizes, piece.dims
    if blank.kind in ("sheet", "strip"):
        if len(d) != 3 or d[0] < s["h"]:
            return 0
        a, b = s["length"], s["width"]
        return max(_along(d[1], a, kerf) * _along(d[2], b, kerf), _along(d[1], b, kerf) * _along(d[2], a, kerf))
    if blank.kind in ("round", "hex"):
        ok = len(d) == 2 and d[0] >= s["d" if blank.kind == "round" else "s"]
        return _along(d[1], s["length"], kerf) if ok else 0
    if blank.kind == "square":
        return _along(d[2], s["length"], kerf) if len(d) == 3 and min(d[:2]) >= s["s"] else 0
    return 0


def fits(blank: Blank, piece: PriceItem, kerf: float = 0.0) -> bool:
    return per_piece(blank, piece, kerf) > 0


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 кусок, 2 куска, 5 кусков."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    return f"{n} {few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many}"


def pieces_for(need: int, per: int) -> int:
    """⌈need / per⌉."""
    return -(-need // max(per, 1))


@dataclass(frozen=True)
class BlankCost:
    """Стоимость заготовок позиции: status — price (по прайсу), manual (задано технологом), none (не найдено)."""
    status: str
    count: int  # заготовок по позиции
    scrap: int  # запас на брак
    vat_percent: float
    per_piece: int = 1  # заготовок из одного куска (цена технолога — кусок на заготовку)
    price_date: str | None = None
    piece: PriceItem | None = None
    unit_price: float | None = None  # за кусок, с НДС
    reason: str = ""
    warning: str = ""
    note: str = ""

    @property
    def need(self) -> int:
        """Заготовок с запасом на брак."""
        return self.count + self.scrap

    @property
    def pieces(self) -> int:
        """Кусков прайса: ⌈(заготовок + запас на брак) / заготовок из куска⌉."""
        return pieces_for(self.need, self.per_piece)

    @property
    def cut_text(self) -> str:
        """«из куска 22 заготовки, нужно 2 куска на 40 + 3 шт.»"""
        return (f"из куска {plural(self.per_piece, 'заготовка', 'заготовки', 'заготовок')}, нужно "
                f"{plural(self.pieces, 'кусок', 'куска', 'кусков')} на {self.count} + {self.scrap} шт.")

    @property
    def total(self) -> float | None:
        """С НДС, как в прайсе."""
        return None if self.unit_price is None else self.unit_price * self.pieces

    @property
    def net_total(self) -> float | None:
        """Без НДС — для КП, где НДС начисляется сверху на итог (иначе НДС посчитается дважды)."""
        return None if self.total is None else self.total / (1 + self.vat_percent / 100)

    @property
    def net_unit(self) -> float | None:
        return None if self.unit_price is None else self.unit_price / (1 + self.vat_percent / 100)

    def to_dict(self) -> dict:
        return {"status": self.status, "count": self.count, "scrap": self.scrap, "pieces": self.pieces,
                "need": self.need, "per_piece": self.per_piece,
                "vat_percent": self.vat_percent, "price_date": self.price_date,
                "piece": self.piece.title if self.piece else None, "code": self.piece.code if self.piece else None,
                "unit_price": self.unit_price, "total": self.total, "net_total": self.net_total,
                "net_unit": self.net_unit, "reason": self.reason, "warning": self.warning, "note": self.note}


def blank_cost(blank: Blank, price: PriceList | None, rules: PriceRules, manual: dict | None = None) -> BlankCost:
    """Кусок прайса с наименьшей стоимостью: цена × ⌈(заготовок + запас на брак) / заготовок из куска⌉
    (крупный кусок тоже кандидат); manual — цена куска от технолога, кусок на одну заготовку."""
    vat = price.vat_percent if price and price.vat_percent is not None else rules.default_vat
    base = dict(count=blank.quantity, scrap=rules.scrap, vat_percent=vat, price_date=price.date if price else None)
    if manual and manual.get("price") is not None:
        return BlankCost("manual", unit_price=float(manual["price"]), note=manual.get("note") or "", **base)

    def none(why: str) -> BlankCost:
        return BlankCost("none", reason=f"{why} — цену задаёт технолог", **base)

    if not blank.defined:
        return none("заготовка не определена")
    if price is None:
        return none("прайс заготовок не загружен (Справочники)")
    label = KINDS[blank.kind].label
    if blank.kind not in FORMS:
        return none(f"{label}: по прайсу не подбирается")
    grade = site_grade(blank.material, blank.kind, rules)
    if grade is None:
        return none(f"«{blank.material or 'материал не указан'}»: марки нет в прайсе")
    pool = [i for i in price.items if i.form in FORMS[blank.kind] and _same_grade(i.grade, grade)]
    if not pool:
        return none(f"{grade}, {label}: такого сортамента нет в прайсе")
    ok = [(i, n) for i in pool if (n := per_piece(blank, i, rules.kerf)) > 0]
    if not ok:
        return none(f"{grade}, {label}: нет куска такого размера в прайсе")
    need = blank.quantity + rules.scrap
    # Сначала куски, которые есть на складе сайта в нужном количестве; нет таких — любой, с предупреждением
    in_stock = [(i, n) for i, n in ok if i.stock is None or i.stock >= pieces_for(need, n)]
    piece, per = min(in_stock or ok,
                     key=lambda x: (x[0].price * pieces_for(need, x[1]), x[0].price, x[0].dims[0], x[0].code))
    pieces = pieces_for(need, per)
    short = piece.stock is not None and piece.stock < pieces
    warning = f"на складе {piece.stock:g} шт., нужно {pieces}" if short else ""
    return BlankCost("price", piece=piece, unit_price=piece.price, warning=warning, per_piece=per, **base)
