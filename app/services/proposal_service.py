"""Коммерческое предложение — простой вариант: заказчик, позиции и количество, цена операций (станочная и
слесарно-финишная обработка по ставкам расчёта), срок — расчётная дата по текущей загрузке ±10 %,
сравнение с желаемой датой заказчика. Правила цены и условия выставления уточняются на следующем этапе."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.desired_date import ProposalRange, RangeCheck, proposal_range
from app.services import order_service, plan_service
from app.services.actor import Actor
from app.services.history_service import record

ORG = "Производственный отдел · Участок механической обработки"
REQUISITES = "Реквизиты предприятия: адрес, ИНН/КПП, телефон, e-mail"
VAT_PERCENT = 20
VALID_DAYS = 14
PAYMENT = "50 % предоплата, 50 % по готовности"
DELIVERY = "самовывоз"
PRICE_NOTE = ("Цена — по операциям механической обработки: станочная (наладка и обработка) и слесарно-финишная, "
              "время × ставка участка. Материал, резка, программирование, покрытие и термообработка не включены "
              "и уточняются отдельно.")
DISCLAIMER = "Срок рассчитан по фактической загрузке производства на дату КП и носит ориентировочный характер."


@dataclass
class ProposalItem:
    designation: str
    name: str
    material: str
    quantity: int
    machine_hours: float = 0.0
    bench_hours: float = 0.0
    machine_rate: float = 0.0
    bench_rate: float = 0.0
    batch_price: float | None = None

    @property
    def price(self) -> float | None:
        return None if self.batch_price is None else self.batch_price / self.quantity

    @property
    def machine_price(self) -> float:
        return self.machine_hours * self.machine_rate

    @property
    def bench_price(self) -> float:
        return self.bench_hours * self.bench_rate


@dataclass
class Proposal:
    number: str
    date: date
    valid_until: date
    order_number: str
    customer: str
    contacts: dict
    items: list[ProposalItem]
    start: date
    calc_date: datetime | None
    range: ProposalRange | None
    desired: date | None
    desired_check: RangeCheck
    org: str = ORG
    requisites: str = REQUISITES
    vat_percent: int = VAT_PERCENT
    payment: str = PAYMENT
    delivery: str = DELIVERY
    price_note: str = PRICE_NOTE
    disclaimer: str = DISCLAIMER
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(i.batch_price or 0 for i in self.items)

    @property
    def vat(self) -> float:
        return self.total * self.vat_percent / 100

    @property
    def grand_total(self) -> float:
        return self.total + self.vat

    @property
    def contact_line(self) -> str:
        return ", ".join(v for v in (self.contacts or {}).values() if v)


def _split(designation: str, data: dict) -> tuple[str, str]:
    """«КПУ.123 Корпус» → обозначение и наименование (наименование из списка или чертежа)."""
    name = data.get("name") or (data.get("drawing") or {}).get("name") or ""
    if name and designation.endswith(" " + name):
        return designation[: -len(name) - 1], name
    return designation, name


def build_proposal(session: Session, actor: Actor, order_id: int, today: date | None = None,
                   record_history: bool = False) -> Proposal:
    require(actor.role, Action.UPLOAD_ORDER)
    order = order_service.get_order(session, order_id)
    today = today or date.today()
    items, notes = [], []
    for pos in order.positions:
        data = pos.data or {}
        code, name = _split(pos.designation, data)
        item = ProposalItem(code, name, data.get("material") or "", pos.quantity)
        calc = plan_service.plan_calc(pos)
        if calc:
            cost = calc["cost"]
            item.machine_hours, item.bench_hours = cost["machine_hours"], cost["bench_hours"]
            item.machine_rate, item.bench_rate = cost["rates"]["machine"], cost["rates"]["bench"]
            item.batch_price = cost["batch_cost"]
        else:
            notes.append(f"{pos.designation}: цена не рассчитана (нет пары STEP+PDF или расчёта) — уточните вручную")
        items.append(item)

    deadline = plan_service.order_deadline(session, order.id)
    span = proposal_range(order.start_date, deadline) if deadline else None
    check = span.against(order.desired_date) if span else RangeCheck("none")
    if not span:
        notes.append("Расчётная дата не определена: срок укажите вручную")
    suffix = order.number.split("-")[-1]
    proposal = Proposal(number=f"КП-{suffix}", date=today, valid_until=today + timedelta(days=VALID_DAYS),
                        order_number=order.number, customer=order.customer or "", contacts=order.contacts or {},
                        items=items, start=order.start_date, calc_date=deadline, range=span,
                        desired=order.desired_date, desired_check=check, notes=notes)
    if record_history:
        record(session, actor, "order", order.id, "proposal", None,
               {"number": proposal.number, "total": round(proposal.grand_total, 2)}, "сформировано КП")
        session.commit()
    return proposal


def proposal_docx(session: Session, actor: Actor, order_id: int, path: Path | str) -> Path:
    from app.services.proposal_docx import write_docx

    return write_docx(build_proposal(session, actor, order_id), Path(path))
