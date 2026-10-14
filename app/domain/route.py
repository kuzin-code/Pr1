"""Технологический маршрут (ФТ-09): формирование по журналу и редактирование."""
from app.domain.enums import STAGE_ORDER, Stage
from app.domain.models import CalibrationEntry, StageNorm


class RouteError(ValueError):
    pass


def _ordered(route: list[StageNorm]) -> list[StageNorm]:
    return sorted(route, key=lambda s: STAGE_ORDER.index(s.stage))


def route_from_entry(entry: CalibrationEntry) -> list[StageNorm]:
    return _ordered(list(entry.stages))


def set_stage(route: list[StageNorm], norm: StageNorm) -> list[StageNorm]:
    """Заменяет или добавляет этап; порядок этапов сохраняется."""
    return _ordered([s for s in route if s.stage is not norm.stage] + [norm])


def remove_stage(route: list[StageNorm], stage: Stage) -> list[StageNorm]:
    if stage not in {s.stage for s in route}:
        raise RouteError(f"Этап «{stage.label}» отсутствует в маршруте")
    return [s for s in route if s.stage is not stage]


def route_to_list(route: list[StageNorm]) -> list[dict]:
    return [s.to_dict() for s in route]


def route_from_list(data: list[dict] | None) -> list[StageNorm]:
    return _ordered([StageNorm.from_dict(d) for d in data or []])
