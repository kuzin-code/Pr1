"""Выгрузка заказов за период в 1С: CSV или Excel; факт выгрузки — в историю (accounting_export.py)."""
import io
from datetime import date, datetime, time

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.accounting_export import OrderExport, headers, table, to_csv
from app.domain.enums import FactKind, PositionStatus
from app.domain.status import order_done
from app.services.actor import Actor, ServiceError
from app.services.fact_service import actual_cost
from app.services.history_service import record
from app.services.plan_service import current_plan, order_deadline
from app.storage.orm import FactRow, OrderRow, ProposalRow

FORMATS = {"csv": "text/csv; charset=utf-8",
           "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _cost(calc: dict | None) -> float | None:
    return calc["cost"]["batch_cost"] if calc else None


def _order_row(session: Session, order: OrderRow, plan) -> OrderExport:
    positions = order.positions
    planned = [_cost(p.approved_calc or p.current_calc) for p in positions]
    planned = [c for c in planned if c is not None]
    actual = []
    for p in positions:
        if p.approved_calc and session.scalar(select(FactRow.id).where(FactRow.position_id == p.id).limit(1)):
            value = actual_cost(session, p.id)["actual"]
            if value:
                actual.append(value)
    proposal = session.scalar(select(ProposalRow).where(ProposalRow.order_id == order.id)
                              .order_by(ProposalRow.created_at.desc(), ProposalRow.id.desc()).limit(1))
    if positions and order_done([PositionStatus(p.status) for p in positions]):
        ends = list(session.scalars(select(FactRow.ended_at).where(FactRow.kind == FactKind.INSPECTION.value)
                                    .where(FactRow.position_id.in_([p.id for p in positions]))))
        ready, kind = (max(ends).date(), "фактически") if ends else (None, "")
    else:
        deadline = order_deadline(session, order.id, plan)
        ready, kind = (deadline.date(), "по плану") if deadline else (None, "")
    return OrderExport(
        number=order.number, created=order.created_at.date(), customer=order.customer or "",
        positions="; ".join(p.designation for p in positions), position_count=len(positions),
        quantity=sum(p.quantity for p in positions),
        proposal_total=round(proposal.grand_total, 2) if proposal else None,
        planned_cost=round(sum(planned), 2) if planned else None,
        actual_cost=round(sum(actual), 2) if actual else None, ready=ready, ready_kind=kind)


def orders_for_period(session: Session, start: date, end: date) -> list[OrderExport]:
    """Заказы, принятые с start по end включительно (по дате приёма заказа)."""
    if end < start:
        raise ServiceError("Дата «по» раньше даты «с»")
    plan = current_plan(session)
    query = (select(OrderRow).where(OrderRow.created_at >= datetime.combine(start, time.min))
             .where(OrderRow.created_at <= datetime.combine(end, time.max)).order_by(OrderRow.created_at, OrderRow.id))
    return [_order_row(session, order, plan) for order in session.scalars(query)]


def _xlsx(rows: list[OrderExport]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Заказы"
    sheet.append(headers())
    for values in table(rows):
        sheet.append(values)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def export_1c(session: Session, actor: Actor, start: date, end: date, fmt: str = "csv") -> tuple[str, str, bytes]:
    """Файл выгрузки: (имя, тип содержимого, данные). Факт выгрузки — в историю."""
    require(actor.role, Action.EXPORT_ACCOUNTING)
    if fmt not in FORMATS:
        raise ServiceError("Формат выгрузки: csv или xlsx")
    rows = orders_for_period(session, start, end)
    data = to_csv(rows) if fmt == "csv" else _xlsx(rows)
    name = f"zakazy_1c_{start:%Y%m%d}-{end:%Y%m%d}.{fmt}"
    record(session, actor, "export", "1c", "export_1c", None,
           {"from": start.isoformat(), "to": end.isoformat(), "orders": len(rows), "format": fmt})
    session.commit()
    return name, FORMATS[fmt], data
