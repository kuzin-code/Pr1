"""Диаграмма Ганта загрузки станков с ЧПУ: раскладка шкалы дней и полос работ в пикселях."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.domain.scheduling import MachineSlot
from app.domain.work_calendar import MACHINE_CALENDAR

MIN_BAR_PX = 6
DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


@dataclass(frozen=True)
class GanttDay:
    day: date
    weekend: bool

    @property
    def label(self) -> str:
        return f"{self.day:%d.%m}"

    @property
    def weekday(self) -> str:
        return DAY_NAMES[self.day.weekday()]


@dataclass(frozen=True)
class GanttBar:
    position_id: int
    left: float
    width: float
    label: str
    title: str
    kind: str  # approved / in_progress / done / preliminary


@dataclass
class GanttRow:
    code: str
    name: str
    bars: list[GanttBar] = field(default_factory=list)
    hours: float = 0.0


@dataclass
class Gantt:
    days: list[GanttDay]
    rows: list[GanttRow]
    day_px: int
    today: float | None

    @property
    def width(self) -> int:
        return len(self.days) * self.day_px


def build_gantt(slots: list[MachineSlot], machines: list[tuple[str, str]], now: datetime,
                info: dict[int, dict] | None = None, day_px: int = 48, min_days: int = 14) -> Gantt:
    """Строка — станок с ЧПУ; полоса — работа позиции на станке (наладка и обработка) по плану."""
    info = info or {}
    codes = {code for code, _ in machines}
    own = [s for s in slots if s.machine_code in codes]
    first = min([s.start.date() for s in own] + [now.date()])
    last = max([s.end.date() for s in own] + [first + timedelta(days=min_days - 1)])
    days = [GanttDay(first + timedelta(days=i), (first + timedelta(days=i)).weekday() >= 5)
            for i in range((last - first).days + 1)]
    origin = datetime.combine(first, datetime.min.time())

    def px(moment: datetime) -> float:
        return (moment - origin).total_seconds() / 86400 * day_px

    rows = {code: GanttRow(code, name) for code, name in machines}
    for s in sorted(own, key=lambda s: s.start):
        meta = info.get(s.position_id, {})
        left = px(s.start)
        rows[s.machine_code].bars.append(GanttBar(
            s.position_id, left, max(px(s.end) - left, MIN_BAR_PX), meta.get("label", f"№{s.position_id}"),
            meta.get("title", ""), meta.get("kind", "preliminary" if s.preliminary else "approved")))
        rows[s.machine_code].hours += MACHINE_CALENDAR.hours_between(s.start, s.end)
    today = px(now) if first <= now.date() <= last else None
    return Gantt(days, list(rows.values()), day_px, today)
