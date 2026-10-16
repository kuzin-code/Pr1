"""Приём заказа за минуту: одно поле для списка деталей, STEP и PDF; заказчик и желаемая дата;
уведомление о новом заказе каждому, кто в нём задействован (по операциям маршрута)."""
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.desired_date import check_desired
from app.domain.enums import Role, Stage, StageGroup
from app.domain.timing import StageTime, group_planned_hours, stage_planned_hours
from app.services import order_service, plan_service
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.order_service import UploadedFile
from app.storage.orm import MachineRow, OrderRow

LIST_SUFFIXES = (".xlsx",)
DRAWING_SUFFIXES = (".step", ".stp", ".pdf")


def _is_list(name: str) -> bool:
    return Path(name).suffix.lower() in LIST_SUFFIXES


def _skip_reason(name: str) -> str:
    hint = " — сохраните модель как STEP (AP214)" if Path(name).suffix.lower() in (".sldprt", ".sldasm") else ""
    return (f"Файл «{Path(name).name}» не принят: принимаются только форматы STEP, PDF и список деталей Excel"
            f"{hint}")


def quick_intake(session: Session, actor: Actor, files: list[UploadedFile], upload_dir: Path | str,
                 customer: str = "", desired_date: date | None = None, start_date: date | None = None,
                 number: str = "", quantity: int = 1) -> OrderRow:
    """Список деталей (Excel) определяется по расширению, остальное — чертежи и модели. Дата начала по
    умолчанию — сегодня; заказчик и желаемая дата из формы важнее шапки списка."""
    require(actor.role, Action.UPLOAD_ORDER)
    files = [f for f in files if f.filename]
    if not files:
        raise ServiceError("Перетащите в поле файлы заказа: список деталей (Excel) и/или STEP и PDF")
    lists = [f for f in files if _is_list(f.filename)]
    drawings = [f for f in files if Path(f.filename).suffix.lower() in DRAWING_SUFFIXES]
    skipped = [_skip_reason(f.filename) for f in files if f not in lists and f not in drawings]
    if len(lists) > 1:
        raise ServiceError("В заказе должен быть один список деталей (Excel)")
    start = start_date or date.today()
    if lists:
        order, warnings = order_service.create_order_from_list(session, actor, start, lists[0], drawings,
                                                               upload_dir, number.strip() or None)
    else:
        if not drawings:
            raise ServiceError("; ".join(skipped) or "Добавьте файлы STEP и PDF деталей")
        order, warnings = order_service.create_order(session, actor, start, drawings, upload_dir,
                                                     number.strip() or None, quantity=quantity), []
    if customer.strip():
        order.customer = customer.strip()
    if desired_date:
        order.desired_date = desired_date
    record(session, actor, "order", order.id, "intake", None,
           {"customer": order.customer, "desired_date": str(order.desired_date or "")}, "приём заказа")
    notify_new_order(session, order, actor)
    session.commit()
    order.intake_warnings = skipped + warnings
    return order


def _hours(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " ч"


def notify_new_order(session: Session, order: OrderRow, actor: Actor) -> None:
    """Каждой роли — что именно ей предстоит по новому заказу; создателю заказа уведомление не нужно."""
    names = {m.code: m.model for m in session.query(MachineRow)}
    machines: dict[str, list[float]] = defaultdict(list)
    work = {Role.CUTTER: [], Role.LOCKSMITH: [], Role.INSPECTOR: []}
    for pos in order.positions:
        calc = plan_service.plan_calc(pos)
        if not calc:
            continue
        times = [StageTime.from_dict(t) for t in calc["stage_times"]]
        if pos.machine_code:
            machines[names.get(pos.machine_code, pos.machine_code)].append(
                group_planned_hours(times, StageGroup.MACHINE))
        for role, hours in ((Role.CUTTER, stage_planned_hours(times, Stage.CUTTING)),
                            (Role.LOCKSMITH, group_planned_hours(times, StageGroup.BENCH)),
                            (Role.INSPECTOR, stage_planned_hours(times, Stage.INSPECTION))):
            if hours > 0:
                work[role].append(hours)

    total, unpaired = len(order.positions), sum(1 for p in order.positions if p.needs_link)
    who = f" ({order.customer})" if order.customer else ""
    title = f"Новый заказ {order.number}{who}, {total} поз."
    deadline = plan_service.order_deadline(session, order.id)
    dates = f"Расчётная дата {deadline:%d.%m.%Y}" if deadline else "Расчётная дата не определена"
    if order.desired_date:
        dates += f", желаемая {order.desired_date:%d.%m.%Y} — {check_desired(deadline, order.desired_date).label}"
    missing = f"; без пары STEP+PDF: {unpaired}" if unpaired else ""
    after = "Начало — после утверждения позиций."

    texts = {
        Role.TECHNOLOGIST: f"{title}: расчёт выполнен автоматически — проверьте расчёт{missing}.",
        Role.HEAD: f"{title}. {dates}.",
        Role.MASTER: f"{title}. {dates}{missing}.",
    }
    if machines:
        texts[Role.OPERATOR] = (f"{title}: наладка и обработка — " + "; ".join(
            f"{name}: {len(h)} поз., {_hours(sum(h))}" for name, h in sorted(machines.items())) + f". {after}")
    labels = {Role.CUTTER: "резка заготовки", Role.LOCKSMITH: "слесарная и финишная",
              Role.INSPECTOR: "внутренний контроль"}
    for role, hours in work.items():
        if hours:
            texts[role] = f"{title}: {labels[role]} — {len(hours)} поз., {_hours(sum(hours))}. {after}"

    first = order.positions[0].id if order.positions else 0
    for role, text in texts.items():
        if role is actor.role:
            continue
        plan_service.notify(session, role, f"new_order:{order.id}:{role.value}", first, "new_order",
                            datetime.now(), text)
