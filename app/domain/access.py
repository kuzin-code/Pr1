"""Права ролей (ТЗ 1.0, п. 4.5)."""
from enum import Enum

from app.domain.enums import Role

T, M, O, H, IT, MG = (Role.TECHNOLOGIST, Role.MASTER, Role.OPERATOR, Role.HEAD, Role.IT,
                      Role.MANAGEMENT)


class Action(str, Enum):
    UPLOAD_ORDER = "upload_order"
    VIEW_ORDERS = "view_orders"
    EDIT_TECH_DATA = "edit_tech_data"  # считанные данные, балл, аналог, маршрут
    ASSIGN_MACHINE = "assign_machine"
    VIEW_MACHINE_ASSIGNMENT = "view_machine_assignment"
    SET_MACHINE_BUSY = "set_machine_busy"
    APPROVE = "approve"
    FACT_MACHINE = "fact_machine"
    FACT_BENCH = "fact_bench"  # слесарно-финишная и контроль
    VIEW_PLAN = "view_plan"  # загрузка станков и срок
    VIEW_COST = "view_cost"  # себестоимость и ставки
    EDIT_RATES = "edit_rates"
    VIEW_JOURNAL = "view_journal"
    EDIT_JOURNAL = "edit_journal"
    IMPORT_JOURNAL = "import_journal"
    VIEW_REFERENCE = "view_reference"  # справочник оборудования, параметры
    EDIT_REFERENCE = "edit_reference"
    RECEIVE_NOTIFICATIONS = "receive_notifications"
    VIEW_HISTORY = "view_history"


PERMISSIONS: dict[Action, frozenset[Role]] = {
    Action.UPLOAD_ORDER: frozenset({M}),
    # Технолог, мастер и начальник видят неопределённые в матрице клетки (п. 4.5).
    Action.VIEW_ORDERS: frozenset({T, M, H}),
    Action.EDIT_TECH_DATA: frozenset({T}),
    Action.ASSIGN_MACHINE: frozenset({T}),
    Action.VIEW_MACHINE_ASSIGNMENT: frozenset({T, M, H}),
    Action.SET_MACHINE_BUSY: frozenset({M}),
    Action.APPROVE: frozenset({H}),
    Action.FACT_MACHINE: frozenset({M, O}),
    Action.FACT_BENCH: frozenset({M}),
    Action.VIEW_PLAN: frozenset({T, M, H, MG}),
    Action.VIEW_COST: frozenset({H, IT}),
    Action.EDIT_RATES: frozenset({IT}),
    Action.VIEW_JOURNAL: frozenset({T, M, H, IT}),
    Action.EDIT_JOURNAL: frozenset({T}),
    Action.IMPORT_JOURNAL: frozenset({T, IT}),  # IT — первичная загрузка
    Action.VIEW_REFERENCE: frozenset({T, H, IT}),
    Action.EDIT_REFERENCE: frozenset({IT}),
    Action.RECEIVE_NOTIFICATIONS: frozenset({M}),
    Action.VIEW_HISTORY: frozenset({T, M, H}),
}


class AccessDenied(PermissionError):
    pass


def can(role: Role, action: Action) -> bool:
    return role in PERMISSIONS[action]


def require(role: Role, action: Action) -> None:
    if not can(role, action):
        raise AccessDenied(f"Роль «{role.label}» не может выполнить действие «{action.value}»")
