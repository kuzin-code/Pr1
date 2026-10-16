"""Группы материалов и обрабатываемость (относительно алюминиевого сплава Д16Т = 1.0).

Коэффициенты — исходные допущения для оценки времени по аналогу другого материала;
уточняются коэффициентом k по факту.
"""
import re

_GROUPS = [
    ("stainless", r"\d{2}[хx]1[78]н|aisi\s?3\d\d|нерж"),
    ("titanium", r"(?<![а-яa-z])вт\d|титан"),
    ("plastic", r"полиацетал|poli?acetal|polyacetal|(?<![a-z])pom(?![a-z])|(?<![а-яa-z])пом(?![а-яa-z])|капролон|фторопласт|полиамид|(?<![а-яa-z])па\s?\d"
                r"|полипроп|полиэтил|поликарб|текстолит|оргстекл|peek|пээк|пмма|(?<![а-яa-z])abs|(?<![а-яa-z])абс"),
    ("brass", r"(?<![а-яa-z])лс\s?\d|(?<![а-яa-z])л\d{2}|латун|бронз|(?<![а-яa-z])бр[а-я]{1,3}\d|медь"),
    ("aluminium", r"д16|d16|амг|amg|ад31|ad31|в95|алюм|\baw[- ]?\d{4}|(?<!\d)60[68]1|(?<!\d)7075"),
    ("steel", r"сталь|(?<![а-яa-z])ст\s?\d|(?<![а-яa-z\d])\d{2}[хx]|(?<![а-яa-z\d])45(?!\d)"),
]

MACHINABILITY = {"plastic": 0.6, "brass": 0.9, "aluminium": 1.0, "steel": 1.6, "stainless": 2.2, "titanium": 3.0, "other": 1.0}

GROUP_LABELS = {"plastic": "пластик", "brass": "латунь, бронза", "aluminium": "алюминиевый сплав", "steel": "сталь",
                "stainless": "нержавеющая сталь", "titanium": "титановый сплав", "other": "прочее"}


def material_group(name: str | None) -> str:
    text = (name or "").casefold()
    for group, pattern in _GROUPS:
        if re.search(pattern, text):
            return group
    return "other"


def machinability(name: str | None) -> float:
    """Во сколько раз обработка дольше, чем у Д16Т."""
    return MACHINABILITY[material_group(name)]
