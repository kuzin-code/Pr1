"""Расчёт позиции со снимком входов (ФТ-11…ФТ-14, ФТ-24)."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.calibration import NO_ANALOG_K, k_for
from app.domain.cost import compute_cost
from app.domain.enums import Mark, StageGroup
from app.domain.models import Dimensions, PartFeatures
from app.domain.route import route_from_list
from app.domain.timing import compute_stage_times
from app.services.actor import Actor, ServiceError
from app.services.reference_service import get_rates, journal_entry, journal_version
from app.storage.orm import PositionRow

DEFAULT_GRADE = 12


def position_features(pos: PositionRow) -> PartFeatures:
    """Признаки детали для аналога и станка; без балла сложности расчёт не идёт (ФТ-06)."""
    data = pos.data or {}
    if pos.complexity is None:
        raise ServiceError("Назначьте балл сложности по шкале сложности")
    if not data.get("dims"):
        raise ServiceError("Не заданы габариты детали: введите их вручную")
    grade = data.get("tolerance_grade")
    # Квалитет не прочитан — принимаются неуказанные допуски IT12 (H12, h12, ±IT12/2).
    return PartFeatures(Dimensions.from_list(data["dims"]), data.get("material") or "",
                        int(grade) if grade is not None else DEFAULT_GRADE, pos.complexity,
                        data.get("technology") or "milling")


def build_calculation(session: Session, actor: Actor, pos: PositionRow,
                      now: datetime | None = None) -> dict:
    if pos.needs_link:
        raise ServiceError("Нет пары STEP+PDF: свяжите файлы вручную, расчёт позиции не запускается")
    position_features(pos)
    if not pos.analog_code and not pos.no_analog:
        raise ServiceError("Подтвердите аналог из журнала калибровки или отметьте «аналога нет»")
    route = route_from_list(pos.route)
    if not route or sum(s.hours for s in route) <= 0:
        raise ServiceError("Не задано время этапов маршрута: введите время вручную")

    analog = journal_entry(session, pos.analog_code) if pos.analog_code else None
    k_machine = k_for(analog, StageGroup.MACHINE) if analog else NO_ANALOG_K
    k_bench = k_for(analog, StageGroup.BENCH) if analog else NO_ANALOG_K
    times = compute_stage_times(route, pos.quantity, k_machine.value, k_bench.value)
    rates = get_rates(session)
    cost = compute_cost(times, pos.quantity, rates)

    marks = [Mark.APPROXIMATE.value]
    if analog is None:
        marks.append(Mark.NO_ANALOG.value)
    elif k_machine.approximate or k_bench.approximate:
        marks.append(Mark.FEW_MEASUREMENTS.value)
    if analog is not None and (pos.data or {}).get("analog_approximate"):
        marks.append(Mark.APPROX_ANALOG.value)
    if not (pos.data or {}).get("material"):
        marks.append(Mark.MATERIAL_UNKNOWN.value)
    if pos.cooperation:
        marks.append(Mark.NO_COOPERATION.value)

    return {
        "created_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "by": actor.username,
        "analog_code": pos.analog_code,
        "journal_version": journal_version(session),
        "quantity": pos.quantity,
        "machine_code": pos.machine_code,
        "route": [s.to_dict() for s in route],
        "k": {"machine": k_machine.to_dict(), "bench": k_bench.to_dict()},
        "rates": rates.to_dict(),
        "stage_times": [t.to_dict() for t in times],
        "cost": cost.to_dict(),
        "marks": marks,
    }
