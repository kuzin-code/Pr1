"""Перечисления предметной области (ТЗ 1.0, разделы 3.2, 4.2, 4.5)."""
from enum import Enum


class Role(str, Enum):
    TECHNOLOGIST = "technologist"
    MASTER = "master"
    OPERATOR = "operator"
    HEAD = "head"
    IT = "it"
    MANAGEMENT = "management"

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]


ROLE_LABELS = {
    Role.TECHNOLOGIST: "Технолог",
    Role.MASTER: "Мастер",
    Role.OPERATOR: "Оператор",
    Role.HEAD: "Начальник участка",
    Role.IT: "IT",
    Role.MANAGEMENT: "Руководство",
}


class StageGroup(str, Enum):
    """Группы этапов для коэффициента k (ФТ-11, ФТ-12)."""

    MACHINE = "machine"  # наладка и обработка на станке
    BENCH = "bench"  # слесарная и финишная
    OTHER = "other"  # резка, программирование, контроль: k не определяется


class Stage(str, Enum):
    CUTTING = "cutting"
    PROGRAMMING = "programming"
    SETUP = "setup"
    MACHINING = "machining"
    LOCKSMITH = "locksmith"
    FINISHING = "finishing"
    INSPECTION = "inspection"

    @property
    def label(self) -> str:
        return STAGE_LABELS[self]

    @property
    def group(self) -> StageGroup:
        if self in (Stage.SETUP, Stage.MACHINING):
            return StageGroup.MACHINE
        if self in (Stage.LOCKSMITH, Stage.FINISHING):
            return StageGroup.BENCH
        return StageGroup.OTHER


STAGE_LABELS = {
    Stage.CUTTING: "Резка заготовки",
    Stage.PROGRAMMING: "Программирование",
    Stage.SETUP: "Наладка",
    Stage.MACHINING: "Обработка на станке",
    Stage.LOCKSMITH: "Слесарная",
    Stage.FINISHING: "Финишная",
    Stage.INSPECTION: "Внутренний контроль",
}

# Порядок этапов маршрута (ФТ-09).
STAGE_ORDER = list(Stage)


class CountMode(str, Enum):
    """Способ счёта этапа в журнале (Д-4)."""

    PER_PIECE = "piece"
    PER_BATCH = "batch"
    ONCE = "once"

    @property
    def label(self) -> str:
        return {"piece": "на штуку", "batch": "на партию", "once": "разово"}[self.value]


class PositionStatus(str, Enum):
    """Статусы позиции в порядке прохождения (ФТ-18)."""

    CALCULATION = "calculation"
    TECH_REVIEW = "tech_review"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    MACHINING = "machining"
    BENCH = "bench"
    INSPECTION = "inspection"
    DONE = "done"

    @property
    def label(self) -> str:
        return STATUS_LABELS[self]


STATUS_LABELS = {
    PositionStatus.CALCULATION: "Расчёт",
    PositionStatus.TECH_REVIEW: "Проверка технолога",
    PositionStatus.AWAITING_APPROVAL: "Ожидает утверждения",
    PositionStatus.APPROVED: "Утверждена",
    PositionStatus.MACHINING: "Изготовление на станке",
    PositionStatus.BENCH: "Слесарно-финишная обработка",
    PositionStatus.INSPECTION: "Внутренний контроль",
    PositionStatus.DONE: "Завершена",
}


class FactKind(str, Enum):
    """Отметки факта (ФТ-20)."""

    MACHINE = "machine"
    BENCH = "bench"
    INSPECTION = "inspection"

    @property
    def label(self) -> str:
        return {
            "machine": "Изготовлено на станке",
            "bench": "Пройдена слесарно-финишная обработка",
            "inspection": "Пройден внутренний контроль",
        }[self.value]


class Mark(str, Enum):
    """Пометки расчёта (НФТ-02, ФТ-08, ФТ-12, ФТ-14, ФТ-16)."""

    APPROXIMATE = "ориентировочно"
    NO_ANALOG = "аналог не найден: время введено вручную"
    FEW_MEASUREMENTS = "по аналогу меньше 5 замеров"
    NO_COOPERATION = "без кооперации"
    PRELIMINARY = "срок предварительный"
    APPROX_ANALOG = "аналог подобран по технологии, время пересчитано"
    MATERIAL_UNKNOWN = "материал не прочитан"
