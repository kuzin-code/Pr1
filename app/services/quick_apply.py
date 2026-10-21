"""Правки параметров позиций из быстрого расчёта: на копии базы, при запуске как заказа, предложение технологу.

Техпроцесс (материал, габариты, квалитет, сложность, технология, станки, время операций) задаёт технолог
(решение пользователя 20.10.2026). Запускает заказ технолог — правки применяются от его имени с историей.
Запускает другая роль — применяется только количество, остальное сохраняется в позиции как
«Предложение из быстрого расчёта» и ждёт решения технолога: «Принять» или «Отклонить» с причиной."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.access import Action, can, require
from app.domain.enums import CountMode, Stage
from app.domain.history import require_reason
from app.services import order_service, tech_service
from app.services.actor import Actor, ServiceError
from app.services.auto_service import SYSTEM
from app.services.history_service import record

PROPOSAL_KEY = "quick_proposal"
LABELS = {"material": "Материал", "dims": "Габариты, мм", "tolerance_grade": "Квалитет IT",
          "complexity": "Балл сложности", "technology": "Технология", "machine": "Станок",
          "extra_machines": "Второй станок", "route": "Время операций"}
_TECHNOLOGY = {"milling": "фрезерная", "turning": "токарная"}


def apply_position(session: Session, actor: Actor, pos, ov: dict, reason: str) -> None:
    """Правки одной позиции теми же действиями, что у технолога (каждое — в историю с причиной)."""
    if "quantity" in ov:
        order_service.set_quantity(session, actor, pos.id, int(ov["quantity"]))
    data = {k: ov[k] for k in ("material", "dims", "tolerance_grade", "technology") if k in ov}
    if data:
        tech_service.update_data(session, actor, pos.id, data, reason)
    if "complexity" in ov:
        tech_service.set_complexity(session, actor, pos.id, int(ov["complexity"]), reason)
    if ov.get("machine") and ov["machine"] != pos.machine_code:
        from app.services.reference_service import domain_machines
        machine = next((m for m in domain_machines(session) if m.code == ov["machine"]), None)
        technology = (pos.data or {}).get("technology") or "milling"
        if machine and machine.technologies and technology not in machine.technologies:
            # токарный станок для фрезерной детали и наоборот — технология меняется вместе со станком
            tech_service.update_data(session, actor, pos.id, {"technology": sorted(machine.technologies)[0]}, reason)
        tech_service.assign_machine(session, actor, pos.id, ov["machine"])
    if "extra_machines" in ov:
        wanted = {e["machine"]: e for e in ov["extra_machines"]}
        for e in (pos.data or {}).get("extra_machines") or []:
            if e["machine"] not in wanted:
                tech_service.remove_machine(session, actor, pos.id, e["machine"])
        for code, e in wanted.items():
            if code not in {x["machine"] for x in (pos.data or {}).get("extra_machines") or []}:
                tech_service.add_machine(session, actor, pos.id, code)
            if e.get("setup") is not None or e.get("machining") is not None:
                current = next(x for x in pos.data["extra_machines"] if x["machine"] == code)
                tech_service.set_extra_machine_time(
                    session, actor, pos.id, code, float(e.get("setup", current.get("setup") or 0)),
                    float(e.get("machining", current.get("machining") or 0)), reason)
    for stage, hours in (ov.get("route") or {}).items():
        mode = next((s["count_mode"] for s in pos.route if s["stage"] == stage), None)
        mode = mode or (CountMode.PER_BATCH.value if stage in ("setup", "inspection")
                        else CountMode.ONCE.value if stage == "programming" else CountMode.PER_PIECE.value)
        tech_service.edit_route_stage(session, actor, pos.id, Stage(stage), float(hours), mode, reason)


def apply_overrides(session: Session, order, overrides: dict) -> None:
    """Правки быстрого расчёта на временной копии базы (от имени системы)."""
    for pos in order.positions:
        apply_position(session, SYSTEM, pos, overrides.get(pos.designation) or {}, "правка в быстром расчёте")


def apply_on_launch(session: Session, actor: Actor, order, overrides: dict) -> None:
    """Запуск как заказа: технолог — все правки; другая роль — количество, остальное — предложение технологу."""
    technologist = can(actor.role, Action.EDIT_TECH_DATA)
    for pos in order.positions:
        ov = dict(overrides.get(pos.designation) or {})
        if technologist:
            apply_position(session, actor, pos, ov, "правка из быстрого расчёта")
            continue
        if "quantity" in ov:
            order_service.set_quantity(session, actor, pos.id, int(ov.pop("quantity")))
        if ov:
            proposal = {"values": ov, "by": actor.username, "at": datetime.now().isoformat(timespec="seconds")}
            pos.data = {**(pos.data or {}), PROPOSAL_KEY: proposal}
            record(session, actor, "position", pos.id, PROPOSAL_KEY, None, ov,
                   "предложение из быстрого расчёта — ждёт решения технолога")
    session.commit()


def describe(values: dict) -> list[tuple[str, str]]:
    """Предложение для карточки позиции: что и на какое значение предлагается изменить."""
    from app.domain.enums import Stage as _Stage
    rows = []
    for key, value in values.items():
        if key == "dims":
            text = " × ".join(f"{float(v):g}" for v in value)
        elif key == "technology":
            text = _TECHNOLOGY.get(value, value)
        elif key == "extra_machines":
            text = ", ".join(e["machine"] + (f" (наладка {e['setup']} ч, обработка {e['machining']} ч/шт)"
                                            if e.get("setup") is not None and e.get("machining") is not None else "")
                             for e in value) or "— нет —"
        elif key == "route":
            text = "; ".join(f"{_Stage(s).label}: {float(h):g} ч" for s, h in value.items())
        else:
            text = str(value)
        rows.append((LABELS.get(key, key), text))
    return rows


def _proposal(session: Session, actor: Actor, position_id: int, reason: str):
    require(actor.role, Action.EDIT_TECH_DATA)
    reason = require_reason(reason)
    pos = order_service.get_position(session, position_id)
    proposal = (pos.data or {}).get(PROPOSAL_KEY)
    if not proposal:
        raise ServiceError("Предложения из быстрого расчёта по позиции нет")
    return pos, proposal, reason


def _drop(session: Session, actor: Actor, pos, proposal: dict, decision: str, reason: str) -> None:
    session.refresh(pos)
    pos.data = {k: v for k, v in (pos.data or {}).items() if k != PROPOSAL_KEY}
    record(session, actor, "position", pos.id, PROPOSAL_KEY, proposal["values"], decision, reason)
    session.commit()


def accept_proposal(session: Session, actor: Actor, position_id: int, reason: str) -> None:
    """Технолог принимает предложение: правки применяются его функциями с его причиной."""
    pos, proposal, reason = _proposal(session, actor, position_id, reason)
    apply_position(session, actor, pos, proposal["values"], f"предложение из быстрого расчёта ({proposal['by']}): "
                                                          f"{reason}")
    _drop(session, actor, pos, proposal, "принято", reason)


def reject_proposal(session: Session, actor: Actor, position_id: int, reason: str) -> None:
    pos, proposal, reason = _proposal(session, actor, position_id, reason)
    _drop(session, actor, pos, proposal, "отклонено", reason)
