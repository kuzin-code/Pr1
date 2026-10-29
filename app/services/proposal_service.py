"""Коммерческое предложение — простой вариант: заказчик, позиции и количество, цена операций (станочная и
слесарно-финишная обработка по ставкам расчёта), срок — расчётная дата по текущей загрузке ±10 %,
сравнение с желаемой датой заказчика. Правила цены и условия выставления уточняются на следующем этапе."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.blank_price import pieces_for
from app.domain.desired_date import ProposalRange, RangeCheck, proposal_range
from app.services import blank_price_service as blank_price, order_service, plan_service
from app.services.quantity_service import position_breaks
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
# Прайс заготовок загружен: материал заготовок — по прайсу поставщика на дату, доставка не включена.
PRICE_NOTE_BLANKS = ("Цена — по операциям механической обработки: станочная (наладка и обработка) и "
                     "слесарно-финишная, время × ставка участка, — и материал заготовок по прайсу поставщика "
                     "(prutki.ru) от {date}: кусков — по раскрою заготовок с запасом {scrap} шт. на брак, без НДС (НДС начисляется "
                     "на итог). Доставка заготовок, резка, программирование, покрытие и термообработка не включены "
                     "и уточняются отдельно.")
BREAKS_NOTE = ("Наладка станка распределяется на всю партию, поэтому с ростом количества цена за штуку снижается. "
               "Готовность на станке — окончание станочной обработки партии по текущей загрузке; "
               "итог КП — по количеству, указанному в таблице выше.")
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
    breaks: list[dict] = field(default_factory=list)  # ступени количества: цена и срок для 1 / 10 / 50 шт.
    # Заготовки (материал по прайсу поставщика): без НДС, на партию с запасом на брак; нет — None
    blank_cost: float | None = None
    blank_pieces: int = 0
    blank_unit: float | None = None  # за кусок, без НДС
    blank_text: str = ""  # кусок прайса или «цена куска задана технологом»

    @property
    def total_price(self) -> float | None:
        """Сумма позиции: обработка и заготовки; обработка не рассчитана — None."""
        return None if self.batch_price is None else self.batch_price + (self.blank_cost or 0)

    @property
    def price(self) -> float | None:
        return None if self.total_price is None else self.total_price / self.quantity

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
    breaks_note: str = BREAKS_NOTE
    disclaimer: str = DISCLAIMER
    notes: list[str] = field(default_factory=list)
    blank_price_date: str | None = None  # дата прайса заготовок (ISO)

    @property
    def total(self) -> float:
        return sum(i.total_price or 0 for i in self.items)

    @property
    def has_blanks(self) -> bool:
        return any(i.blank_cost is not None for i in self.items)

    @property
    def blank_label(self) -> str:
        when = f" от {date.fromisoformat(self.blank_price_date):%d.%m.%Y}" if self.blank_price_date else ""
        return f"Заготовки (материал по прайсу prutki.ru{when})"

    @property
    def vat(self) -> float:
        return self.total * self.vat_percent / 100

    @property
    def grand_total(self) -> float:
        return self.total + self.vat

    @property
    def has_breaks(self) -> bool:
        return any(i.breaks for i in self.items)

    @property
    def contact_line(self) -> str:
        return ", ".join(v for v in (self.contacts or {}).values() if v)


def _split(designation: str, data: dict) -> tuple[str, str]:
    """«КПУ.123 Корпус» → обозначение и наименование (наименование из списка или чертежа)."""
    name = data.get("name") or (data.get("drawing") or {}).get("name") or ""
    if name and designation.endswith(" " + name):
        return designation[: -len(name) - 1], name
    return designation, name


def _blanks(item: ProposalItem, cost, meta: dict | None, notes: list[str], designation: str) -> None:
    """Заготовки строкой без НДС: цены прайса с НДС, а НДС КП начисляется на итог — иначе НДС дважды.
    Ступени количества — тот же кусок × (количество ступени + запас на брак)."""
    if cost.unit_price is None:
        if meta:  # прайс загружен, но кусок не найден — менеджеру уточнить
            notes.append(f"{designation}: цена заготовки не определена ({cost.reason}) — уточните вручную")
        return
    item.blank_cost, item.blank_pieces, item.blank_unit = cost.net_total, cost.pieces, cost.net_unit
    item.blank_text = (f"{cost.piece.title} (код {cost.piece.code}), {cost.cut_text}" if cost.piece
                       else "цена куска задана технологом")
    for b in item.breaks:
        extra = cost.net_unit * pieces_for(b["quantity"] + cost.scrap, cost.per_piece)
        b.update(blank_cost=extra, batch_price=b["batch_price"] + extra)
        b["piece_price"] = b["batch_price"] / b["quantity"]


def build_proposal(session: Session, actor: Actor, order_id: int, today: date | None = None,
                   record_history: bool = False, quantities: list[int] | None = None) -> Proposal:
    require(actor.role, Action.UPLOAD_ORDER)
    order = order_service.get_order(session, order_id)
    today = today or date.today()
    plan = plan_service.current_plan(session)
    items, notes = [], []
    meta, price_rules = blank_price.price_meta(session), blank_price.rules(session)
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
            item.breaks = position_breaks(pos, plan, quantities or [])
            _blanks(item, blank_price.position_cost(session, pos, price_rules), meta, notes, pos.designation)
        else:
            notes.append(f"{pos.designation}: цена не рассчитана (нет пары STEP+PDF или расчёта) — уточните вручную")
        items.append(item)

    deadline = plan_service.order_deadline(session, order.id, plan)
    span = proposal_range(order.start_date, deadline, not_before=today) if deadline else None
    check = span.against(order.desired_date) if span else RangeCheck("none")
    if not span:
        notes.append("Расчётная дата не определена: срок укажите вручную")
    suffix = order.number.split("-")[-1]
    proposal = Proposal(number=f"КП-{suffix}", date=today, valid_until=today + timedelta(days=VALID_DAYS),
                        order_number=order.number, customer=order.customer or "", contacts=order.contacts or {},
                        items=items, start=order.start_date, calc_date=deadline, range=span,
                        desired=order.desired_date, desired_check=check, notes=notes,
                        blank_price_date=(meta or {}).get("date"))
    if proposal.has_blanks:
        when = date.fromisoformat(meta["date"]).strftime("%d.%m.%Y") if (meta or {}).get("date") else "даты расчёта"
        proposal.price_note = PRICE_NOTE_BLANKS.format(date=when, scrap=price_rules.scrap)
    if record_history:
        record(session, actor, "order", order.id, "proposal", None,
               {"number": proposal.number, "total": round(proposal.grand_total, 2)}, "сформировано КП")
        session.commit()
    return proposal


def proposal_docx(session: Session, actor: Actor, order_id: int, path: Path | str,
                  quantities: list[int] | None = None) -> Path:
    from app.services.proposal_docx import write_docx

    return write_docx(build_proposal(session, actor, order_id, quantities=quantities), Path(path))
