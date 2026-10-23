"""Справочники: оборудование, ставки, шкала сложности, журнал калибровки (ФТ-25), вход."""
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.calibration import add_measurement
from app.domain.enums import Operation, StageGroup
from app.domain.operations import normalize_operations
from app.domain.history import require_reason
from app.domain.models import CalibrationEntry, Machine, Rates
from app.parsers.journal_excel import export_journal, import_journal
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.security import verify_password
from app.storage.convert import entry_from_row, entry_to_row, machine_from_row
from app.storage.orm import JournalRow, MachineRow, Parameter, User


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username))
    if user and user.active is not False and verify_password(password, user.password_hash):
        return user
    return None


def _param(session: Session, key: str):
    row = session.get(Parameter, key)
    if row is None:
        raise ServiceError(f"Не задан параметр «{key}»: загрузите справочники")
    return row


# --- ставки и параметры (Д-6) ---

def get_rates(session: Session) -> Rates:
    value = _param(session, "rates").value
    return Rates(float(value["machine"]), float(value["bench"]))


def set_rates(session: Session, actor: Actor, machine: float, bench: float, reason: str) -> Rates:
    require(actor.role, Action.EDIT_RATES)
    reason = require_reason(reason)
    if machine < 0 or bench < 0:
        raise ServiceError("Ставки не могут быть отрицательными")
    row = _param(session, "rates")
    old = dict(row.value)
    row.value = {"machine": float(machine), "bench": float(bench)}
    record(session, actor, "parameters", "rates", "rates", old, row.value, reason)
    session.commit()
    return get_rates(session)


def preferred_machines(session: Session) -> dict[str, str]:
    """Основной станок по технологии: фрезерная — Haas VF-2, токарная — Haas ST-20Y."""
    row = session.get(Parameter, "preferred_machines")
    return dict(row.value) if row else {"milling": "VF2", "turning": "ST20Y"}


def complexity_scale(session: Session) -> list[dict]:
    return list(_param(session, "complexity_scale").value)


# --- оборудование (Д-5) ---

def list_machines(session: Session) -> list[MachineRow]:
    return list(session.scalars(select(MachineRow).order_by(MachineRow.id)))


def domain_machines(session: Session) -> list[Machine]:
    return [machine_from_row(r) for r in list_machines(session)]


def get_machine(session: Session, code: str) -> MachineRow:
    row = session.scalar(select(MachineRow).where(MachineRow.code == code))
    if row is None:
        raise ServiceError(f"Станок «{code}» не найден")
    return row


def set_busy_until(session: Session, actor: Actor, code: str, moment: datetime | None) -> None:
    """Дата окончания текущих работ на станке (вводит мастер, ФТ-15)."""
    require(actor.role, Action.SET_MACHINE_BUSY)
    row = get_machine(session, code)
    old = row.busy_until.isoformat() if row.busy_until else None
    row.busy_until = moment
    record(session, actor, "machine", code, "busy_until", old, moment.isoformat() if moment else None)
    session.commit()


# --- журнал калибровки (Д-4) ---

def journal_version(session: Session) -> int:
    return int(_param(session, "journal_version").value)


def bump_journal_version(session: Session) -> None:
    row = _param(session, "journal_version")
    row.value = int(row.value) + 1


def journal_entries(session: Session) -> list[CalibrationEntry]:
    return [entry_from_row(r) for r in session.scalars(select(JournalRow).order_by(JournalRow.part_code))]


def _journal_row(session: Session, code: str) -> JournalRow:
    row = session.scalar(select(JournalRow).where(JournalRow.part_code == code))
    if row is None:
        raise ServiceError(f"Деталь «{code}» отсутствует в журнале калибровки")
    return row


def journal_entry(session: Session, code: str) -> CalibrationEntry:
    return entry_from_row(_journal_row(session, code))


def add_journal_measurement(session: Session, code: str, group: StageGroup, calc_hours: float,
                            actual_hours: float, position_id: int | None = None,
                            at: datetime | None = None, reason: str | None = None) -> CalibrationEntry:
    """Замер по факту; k пересчитывается системой (ФТ-12). reason — причина отклонения при «Закончил»."""
    row = _journal_row(session, code)
    entry = add_measurement(entry_from_row(row), group, calc_hours, actual_hours, position_id, at, reason)
    entry_to_row(entry, row)
    bump_journal_version(session)
    return entry


def remove_measurements(session: Session, code: str, position_id: int, group: StageGroup) -> None:
    """Убирает замеры позиции по группе (отменённая ошибочная отметка факта)."""
    row = session.scalar(select(JournalRow).where(JournalRow.part_code == code))
    if row is None:
        return
    kept = [m for m in row.measurements if not (m.get("position_id") == position_id and m["group"] == group.value)]
    if len(kept) != len(row.measurements):
        row.measurements = kept
        bump_journal_version(session)


def set_measurement_reason(session: Session, code: str, position_id: int, group: StageGroup,
                           reason: str | None) -> None:
    """Причина у замеров позиции по группе (мастер исправил причину отклонения)."""
    row = session.scalar(select(JournalRow).where(JournalRow.part_code == code))
    if row is None:
        return
    changed = [dict(m, reason=reason) if m.get("position_id") == position_id and m["group"] == group.value else m
               for m in row.measurements]
    if changed != row.measurements:
        row.measurements = changed
        bump_journal_version(session)


def import_journal_file(session: Session, actor: Actor, path: Path | str, replace: bool = False) -> int:
    """Загрузка журнала из Excel: записи с тем же обозначением обновляются."""
    require(actor.role, Action.IMPORT_JOURNAL)
    entries = import_journal(path)
    existing = {r.part_code: r for r in session.scalars(select(JournalRow))}
    if replace:
        for code, row in existing.items():
            session.delete(row)
        session.flush()
        existing = {}
    for entry in entries:
        session.add(entry_to_row(entry, existing.get(entry.part_code)))
    bump_journal_version(session)
    record(session, actor, "journal", "*", "import", None, [e.part_code for e in entries],
           "замена журнала" if replace else "загрузка из Excel")
    session.commit()
    return len(entries)


def export_journal_file(session: Session, path: Path | str) -> Path:
    export_journal(journal_entries(session), path)
    return Path(path)


# --- правка справочника оборудования (IT, п. 4.5) ---

MACHINE_FIELDS = [
    # (поле, подпись, тип, важно для станка с ЧПУ, важно для прочего оборудования)
    ("model", "Модель", "str", False, False),
    ("kind", "Вид оборудования", "str", False, False),
    ("work_zone", "Рабочая зона, мм", "dims", True, False),
    ("technologies", "Технологии", "list", True, False),
    ("operations", "Выполняемые операции", "operations", True, True),
    ("controller", "Стойка ЧПУ", "str", True, False),
    ("accuracy", "Точность", "str", True, False),
    ("power", "Мощность", "str", True, True),
    ("axes", "Число осей", "int", True, False),
    ("spindle_rpm", "Обороты шпинделя, об/мин", "int", True, False),
    ("tool_count", "Магазин инструментов, шт.", "int", True, False),
    ("max_weight_kg", "Макс. масса заготовки, кг", "float", True, False),
    ("table_size", "Размер стола, мм", "str", True, False),
    ("inventory_number", "Инвентарный номер", "str", True, True),
    ("hours_per_day", "Часов в смену", "float", False, False),
    ("note", "Примечание", "str", False, False),
]
MACHINE_TECHNOLOGIES = {"milling", "turning", "cutting", "finishing"}


def machine_gaps(row: MachineRow) -> list[str]:
    """Незаполненные важные характеристики («пробелы» справочника)."""
    gaps = []
    for name, label, _, for_cnc, for_other in MACHINE_FIELDS:
        if (for_cnc if row.is_cnc else for_other) and getattr(row, name) in (None, "", []):
            gaps.append(label)
    return gaps


def _parse_machine_value(name: str, kind: str, value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return {"list": [], "operations": [], "str": ""}.get(kind)
    if kind == "dims":
        if isinstance(value, list):
            parts = value
        else:
            parts = [p for p in re.split(r"[x×хX*\s;]+", str(value).replace(",", ".")) if p]
        try:
            dims = [float(p) for p in parts]
        except ValueError:
            raise ServiceError("Рабочая зона: три размера через «x», например 762 x 406 x 508") from None
        if len(dims) != 3 or min(dims) <= 0:
            raise ServiceError("Рабочая зона: три положительных размера, мм")
        return dims
    if kind == "operations":
        items = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
        valid = {op.value for op in Operation} | {"machining"}
        unknown = [v for v in items if v not in valid]
        if unknown:
            raise ServiceError(f"Неизвестные операции: {', '.join(unknown)}")
        return items
    if kind == "list":
        items = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
        unknown = set(items) - MACHINE_TECHNOLOGIES
        if unknown:
            raise ServiceError(f"Неизвестные технологии: {', '.join(sorted(unknown))} "
                               f"(допустимо: {', '.join(sorted(MACHINE_TECHNOLOGIES))})")
        return items
    if kind in ("int", "float"):
        try:
            number = (int if kind == "int" else float)(str(value).replace(",", ".").replace(" ", ""))
        except ValueError:
            raise ServiceError(f"Поле «{name}»: нужно число") from None
        if number <= 0:
            raise ServiceError(f"Поле «{name}»: число должно быть больше нуля")
        return number
    return str(value).strip()


def update_machine(session: Session, actor: Actor, code: str, fields: dict, reason: str = "") -> MachineRow:
    require(actor.role, Action.EDIT_REFERENCE)
    row = get_machine(session, code)
    kinds = {name: kind for name, _, kind, _, _ in MACHINE_FIELDS} | {"is_cnc": "bool"}
    for name, value in fields.items():
        if name not in kinds:
            raise ServiceError(f"Недопустимое поле «{name}»")
        new = bool(value) if kinds[name] == "bool" else _parse_machine_value(name, kinds[name], value)
        if name == "operations":
            new = normalize_operations(new, row.technologies)
            # Технология станка (подбор по детали) следует из его операций обработки.
            derived = [t for t in ("milling", "turning") if t in new]
            if derived and derived != row.technologies:
                record(session, actor, "machine", code, "technologies", row.technologies, derived, reason)
                row.technologies = derived
        if name in ("model", "kind", "hours_per_day") and new in (None, ""):
            raise ServiceError("Модель, вид оборудования и часы в смену обязательны")
        if getattr(row, name) != new:
            record(session, actor, "machine", code, name, getattr(row, name), new, reason)
            setattr(row, name, new)
    session.commit()
    return row


def machines_for_operation(session: Session, operation) -> list[MachineRow]:
    """Оборудование, на котором выполняется операция (Operation или этап маршрута)."""
    return [m for m in list_machines(session) if operation.value in (m.operations or [])]


def machine_operations(session: Session) -> dict[str, list[str]]:
    return {m.code: list(m.operations or []) for m in list_machines(session)}


def create_machine(session: Session, actor: Actor, fields: dict) -> MachineRow:
    require(actor.role, Action.EDIT_REFERENCE)
    code = str(fields.get("code") or "").strip()
    if not code:
        raise ServiceError("Укажите код станка")
    if session.scalar(select(MachineRow).where(MachineRow.code == code)):
        raise ServiceError(f"Станок с кодом «{code}» уже есть")
    row = MachineRow(code=code, model="", kind="", is_cnc=bool(fields.get("is_cnc")), technologies=[],
                     hours_per_day=6, maintenance=[], note="")
    session.add(row)
    session.flush()
    record(session, actor, "machine", code, "create", None, code, "добавлен в справочник")
    rest = {k: v for k, v in fields.items() if k not in ("code", "is_cnc")}
    if not str(rest.get("hours_per_day") or "").strip():
        rest["hours_per_day"] = 6 if row.is_cnc else 8
    return update_machine(session, actor, code, rest, "добавлен в справочник")
