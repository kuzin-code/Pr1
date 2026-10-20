"""Ступени количества позиции для быстрого расчёта и КП: по снимку расчёта позиции, без записи в базу."""
from app.domain.models import Rates
from app.domain.quantity_breaks import price_breaks
from app.domain.route import route_from_list
from app.domain.scheduling import Plan
from app.domain.timing import extra_from_list
from app.services.plan_service import plan_calc
from app.storage.orm import PositionRow


def breaks_from_calc(calc: dict, quantities: list[int], machine_start=None) -> list[dict]:
    """Тот же маршрут, k, ставки и второй станок, что в расчёте позиции, — для каждого количества."""
    k, rates = calc["k"], calc["rates"]
    return [b.to_dict() for b in price_breaks(
        route_from_list(calc["route"]), quantities, k["machine"]["value"], k["bench"]["value"],
        Rates(rates["machine"], rates["bench"]), extra_from_list(calc.get("extra_machines")), machine_start)]


def position_breaks(pos: PositionRow, plan: Plan | None, quantities: list[int]) -> list[dict]:
    """Готовность на станке — от начала работы станка над позицией по текущему плану."""
    calc = plan_calc(pos)
    if not calc or not quantities:
        return []
    planned = plan.positions.get(pos.id) if plan else None
    return breaks_from_calc(calc, quantities, planned.machine_start if planned else None)
