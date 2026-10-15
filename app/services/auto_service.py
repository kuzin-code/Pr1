"""Автоматический расчёт позиции: сложность, аналог, маршрут, станок, себестоимость.

Система выполняет все шаги сама; технолог проверяет и исправляет. Ручные решения технолога
помечаются в data["auto"] (False) и при пересчёте не перезаписываются.
"""
from sqlalchemy.orm import Session

from app.domain.analog import evaluate_analog, pick_analog
from app.domain.complexity import estimate_complexity
from app.domain.enums import CountMode, PositionStatus, Role, Stage
from app.domain.machines import choose_machine
from app.domain.models import Dimensions, PartFeatures, StageNorm
from app.domain.route import route_to_list
from app.domain.status import is_editable
from app.services.actor import Actor, ServiceError
from app.services.calc_service import DEFAULT_GRADE, build_calculation
from app.services.history_service import record
from app.services.reference_service import (
    domain_machines,
    journal_entries,
    journal_entry,
    preferred_machines,
)
from app.storage.orm import PositionRow

SYSTEM = Actor("система", Role.TECHNOLOGIST)
AUTO_KEYS = ("complexity", "analog", "machine", "route")
TECHNOLOGY_LABELS = {"milling": "фрезерная", "turning": "токарная"}

# Маршрут для ручного ввода, если аналога той же технологии нет (ФТ-08).
MANUAL_ROUTE = [
    StageNorm(Stage.CUTTING, 0, CountMode.PER_PIECE),
    StageNorm(Stage.PROGRAMMING, 0, CountMode.ONCE),
    StageNorm(Stage.SETUP, 0, CountMode.PER_BATCH),
    StageNorm(Stage.MACHINING, 0, CountMode.PER_PIECE),
    StageNorm(Stage.LOCKSMITH, 0, CountMode.PER_PIECE),
    StageNorm(Stage.FINISHING, 0, CountMode.PER_PIECE),
    StageNorm(Stage.INSPECTION, 0, CountMode.PER_BATCH),
]


def auto_flags(pos: PositionRow) -> dict[str, bool]:
    return {key: True for key in AUTO_KEYS} | dict((pos.data or {}).get("auto") or {})


def mark_manual(pos: PositionRow, *keys: str) -> None:
    """Технолог принял решение сам: система его не меняет."""
    pos.data = {**(pos.data or {}), "auto": auto_flags(pos) | {k: False for k in keys}}


def auto_prepare(session: Session, pos: PositionRow, actor: Actor = SYSTEM) -> list[str]:
    """Подготовить и рассчитать позицию. Возвращает замечания для технолога."""
    if not is_editable(PositionStatus(pos.status)):
        return []
    data = dict(pos.data or {})
    auto = auto_flags(pos)
    issues: list[str] = []

    def finish() -> list[str]:
        data["auto"], data["auto_issues"] = auto, issues
        pos.data = data
        return issues

    if pos.needs_link:
        issues.extend(data.get("list_issues") or [])
        issues.append("Нет пары STEP+PDF: прикрепите файлы в карточке позиции или свяжите вручную — "
                      "расчёт не запускается")
        pos.current_calc = None
        return finish()
    if not data.get("dims"):
        issues.append("Габариты не прочитаны из STEP: введите их вручную")
        pos.current_calc = None
        return finish()
    issues.extend(data.get("list_issues") or [])
    grade = data.get("tolerance_grade")
    if grade is None:
        issues.append(f"Квалитет не прочитан: принят IT{DEFAULT_GRADE} (неуказанные допуски)")
    if not data.get("material"):
        issues.append("Материал не прочитан: время оценено как для Д16Т, укажите материал")

    if auto["complexity"] or pos.complexity is None:
        pos.complexity = estimate_complexity(grade, len(data.get("threads") or []))
        auto["complexity"] = True
    technology = data.get("technology") or "milling"
    features = PartFeatures(Dimensions.from_list(data["dims"]), data.get("material") or "",
                            int(grade) if grade is not None else DEFAULT_GRADE, pos.complexity, technology)

    if auto["analog"]:
        picked = pick_analog(features, journal_entries(session))
        pos.analog_code, pos.no_analog = (picked.entry.part_code, False) if picked else (None, True)
    choice = evaluate_analog(features, journal_entry(session, pos.analog_code)) if pos.analog_code else None
    if choice:
        data["analog_approximate"], data["analog_reasons"] = choice.approximate, list(choice.reasons)
    else:
        data["analog_approximate"], data["analog_reasons"] = False, []
        issues.append(f"В журнале нет деталей с технологией «{TECHNOLOGY_LABELS.get(technology, technology)}»: "
                      "введите время этапов вручную")
    if auto["route"]:
        pos.route = route_to_list(choice.route if choice else MANUAL_ROUTE)

    if auto["machine"]:
        machine = choose_machine(features, domain_machines(session), choice.entry.machine_code if choice else None,
                                 bool(choice and not choice.approximate), preferred_machines(session))
        pos.machine_code = machine.code if machine else None
        if machine is None:
            issues.append("Ни один станок не вмещает деталь: ситуацию разбирает технолог (О-8)")

    finish()
    try:
        pos.current_calc = build_calculation(session, actor, pos)
    except ServiceError as err:
        pos.current_calc = None
        issues.append(str(err))
        return finish()
    if pos.status == PositionStatus.CALCULATION.value:
        pos.status = PositionStatus.TECH_REVIEW.value
    record(session, actor, "position", pos.id, "auto_calculation", None,
           {"complexity": pos.complexity, "analog": pos.analog_code, "machine": pos.machine_code},
           "автоматический расчёт")
    return issues
