"""Операции позиции на оборудовании и их выполнение специалистами («Начал» / «Закончил»).

Маршрут утверждённой позиции раскладывается на операции; каждая назначается оборудованию,
которое её выполняет (справочник оборудования). Операции идут строго по порядку маршрута.
"""
from dataclasses import dataclass
from enum import Enum

from app.domain.enums import Operation, Role, Stage, StageGroup
from app.domain.timing import StageTime


class OperationError(ValueError):
    pass


class OpStatus(str, Enum):
    WAITING = "waiting"  # ждёт окончания предыдущей операции
    READY = "ready"  # можно начинать
    IN_PROGRESS = "in_progress"
    DONE = "done"

    @property
    def label(self) -> str:
        return {"waiting": "ожидает предыдущую операцию", "ready": "можно начинать",
                "in_progress": "в работе", "done": "выполнена"}[self.value]


# Кто нажимает кнопки: специалист своей операции; мастер — за любого.
OPERATION_ROLES: dict[Operation, frozenset[Role]] = {
    Operation.CUTTING: frozenset({Role.CUTTER, Role.MASTER}),
    Operation.SETUP: frozenset({Role.OPERATOR, Role.MASTER}),
    Operation.MILLING: frozenset({Role.OPERATOR, Role.MASTER}),
    Operation.TURNING: frozenset({Role.OPERATOR, Role.MASTER}),
    Operation.LOCKSMITH: frozenset({Role.LOCKSMITH, Role.MASTER}),
    Operation.FINISHING: frozenset({Role.LOCKSMITH, Role.MASTER}),
    Operation.INSPECTION: frozenset({Role.INSPECTOR, Role.MASTER}),
}
MACHINE_OPERATIONS = {Operation.SETUP, Operation.MILLING, Operation.TURNING}  # на назначенном станке позиции
STAGE_OF = {Operation.CUTTING: Stage.CUTTING, Operation.SETUP: Stage.SETUP, Operation.MILLING: Stage.MACHINING,
            Operation.TURNING: Stage.MACHINING, Operation.LOCKSMITH: Stage.LOCKSMITH,
            Operation.FINISHING: Stage.FINISHING, Operation.INSPECTION: Stage.INSPECTION}


def can_press(role: Role, operation: Operation) -> bool:
    return role in OPERATION_ROLES[operation]


def operations_for_role(role: Role) -> set[Operation]:
    return {op for op, roles in OPERATION_ROLES.items() if role in roles}


def operation_for_stage(stage: Stage, technology: str) -> Operation | None:
    if stage is Stage.PROGRAMMING:
        return None  # УП считается готовой к началу изготовления
    if stage is Stage.MACHINING:
        return Operation.TURNING if technology == "turning" else Operation.MILLING
    return Operation(stage.value)


def group_of(operation: Operation) -> StageGroup:
    return STAGE_OF[operation].group


def normalize_operations(values: list[str] | None, technologies: list[str] | None) -> list[str]:
    """Операции станка в порядке перечня; устаревшее «machining» — по технологии станка."""
    result = set()
    for value in values or []:
        if value == "machining":
            result.add("turning" if "turning" in (technologies or []) else "milling")
        elif value in {op.value for op in Operation}:
            result.add(value)
    return [op.value for op in Operation if op.value in result]


@dataclass(frozen=True)
class PlannedOperation:
    seq: int
    stage: Stage
    operation: Operation
    resource: str | None  # код оборудования; None — рабочее место специалиста (контроль)
    planned_hours: float


def plan_operations(times: list[StageTime], technology: str, machine_code: str | None,
                    machine_ops: dict[str, list[str]]) -> list[PlannedOperation]:
    result = []
    for t in times:
        operation = operation_for_stage(t.stage, technology)
        if operation is None or t.planned_hours <= 0:
            continue
        if operation in MACHINE_OPERATIONS:
            resource = machine_code
        else:
            resource = next((code for code, ops in machine_ops.items() if operation.value in ops), None)
        result.append(PlannedOperation(len(result) + 1, t.stage, operation, resource, t.planned_hours))
    return result


def refresh_statuses(statuses: list[OpStatus]) -> list[OpStatus]:
    """Первая невыполненная операция становится доступной, следующие ждут."""
    result, previous_done = [], True
    for status in statuses:
        if status in (OpStatus.DONE, OpStatus.IN_PROGRESS):
            result.append(status)
        else:
            result.append(OpStatus.READY if previous_done else OpStatus.WAITING)
        previous_done = status is OpStatus.DONE
    return result
