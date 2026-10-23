"""Причина отклонения факта от плана при «Закончил» (простои и потери времени).

Правило:
- если фактическая длительность операции больше плана × OVERRUN_FACTOR, специалист обязан выбрать причину
  из закрытого справочника; «другое» — только с комментарием;
- замеры с причинами «ремонт», «ждал заготовку», «брак», «другое» — не работа по детали, а потери:
  в коэффициент калибровки k они не идут (остаются в журнале с пометкой);
- «наладка», «инструмент», «программа» — особенности самой детали: в k идут, но помечаются причиной.

Порог OVERRUN_FACTOR меньше IDLE_CAP_FACTOR (machine_work.py): сначала требуется причина, а сверх
IDLE_CAP_FACTOR операция в загрузке станка уже считается простоем.
"""
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum


class DowntimeError(ValueError):
    pass


OVERRUN_FACTOR = 1.2


class Reason(str, Enum):
    SETUP = "setup"
    TOOL = "tool"
    BLANK = "blank"
    DEFECT = "defect"
    PROGRAM = "program"
    REPAIR = "repair"
    OTHER = "other"

    @property
    def label(self) -> str:
        return REASON_LABELS[self]


REASON_LABELS = {Reason.SETUP: "наладка", Reason.TOOL: "инструмент", Reason.BLANK: "ждал заготовку",
                 Reason.DEFECT: "брак", Reason.PROGRAM: "программа", Reason.REPAIR: "ремонт",
                 Reason.OTHER: "другое"}
# Потери, не связанные с трудоёмкостью детали: замер не учитывается в k.
NOT_IN_K = frozenset({Reason.REPAIR, Reason.BLANK, Reason.DEFECT, Reason.OTHER})
NO_REASON_LABEL = "не указана"


def needs_reason(planned_hours: float, actual_hours: float) -> bool:
    """Факт больше плана × порог — причина обязательна (у операции без плана — не требуется)."""
    return planned_hours > 0 and actual_hours > planned_hours * OVERRUN_FACTOR


def parse_reason(value: str | None) -> Reason | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return Reason(text)
    except ValueError:
        raise DowntimeError(f"Неизвестная причина отклонения: «{text}»") from None


def check_reason(planned_hours: float, actual_hours: float, reason: str | None,
                 note: str | None = "") -> tuple[Reason | None, str]:
    """Проверенная причина и комментарий; ошибка — если причина обязательна и не выбрана."""
    parsed, note = parse_reason(reason), (note or "").strip()
    if parsed is None and needs_reason(planned_hours, actual_hours):
        raise DowntimeError(f"Факт {actual_hours:.2f} ч больше плана {planned_hours:.2f} ч более чем в "
                            f"{OVERRUN_FACTOR:g} раза: выберите причину отклонения")
    if parsed is Reason.OTHER and not note:
        raise DowntimeError("Для причины «другое» напишите комментарий")
    return parsed, note


def counts_in_k(reason: str | Reason | None) -> bool:
    return reason is None or Reason(reason) not in NOT_IN_K


def group_reason(reasons) -> Reason | None:
    """Причина замера группы операций: потеря (не в k) важнее, затем первая указанная."""
    parsed = [Reason(r) for r in reasons if r]
    return next((r for r in parsed if r in NOT_IN_K), parsed[0] if parsed else None)


def reason_label(reason: str | Reason | None) -> str:
    return Reason(reason).label if reason else NO_REASON_LABEL


@dataclass(frozen=True)
class Overrun:
    """Выполненная операция: сколько часов сверх плана и по какой причине."""
    machine: str
    reason: str | None
    planned_hours: float
    actual_hours: float

    @property
    def over_hours(self) -> float:
        return max(self.actual_hours - self.planned_hours, 0.0)


@dataclass(frozen=True)
class OverrunTable:
    """Часы сверх плана: строки — причины, столбцы — станки."""
    machines: list[str]
    rows: list[tuple[str, dict[str, float], float]]  # (причина, часы по станкам, итого)
    totals: dict[str, float]
    total: float


def overrun_table(items: list[Overrun]) -> OverrunTable:
    cells: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for item in items:
        if item.over_hours > 0:
            cells[reason_label(item.reason)][item.machine or "рабочее место"] += item.over_hours
    machines = sorted({m for row in cells.values() for m in row})
    order = [r.label for r in Reason] + [NO_REASON_LABEL]
    rows = [(label, dict(cells[label]), sum(cells[label].values())) for label in order if label in cells]
    totals = {m: sum(row.get(m, 0.0) for _, row, _ in rows) for m in machines}
    return OverrunTable(machines, rows, totals, sum(totals.values()))
