"""Допустимые станки и предложение станка (ФТ-10)."""
from app.domain.models import CalibrationEntry, Machine, PartFeatures


def allowed_machines(part: PartFeatures, machines: list[Machine]) -> list[Machine]:
    """Станки с ЧПУ, поддерживающие технологию детали и вмещающие её габариты."""
    return [
        m for m in machines
        if m.is_cnc and part.technology in m.technologies
        and m.work_zone is not None and part.dims.fits_in(m.work_zone)
    ]


def suggest_machine(allowed: list[Machine], analog: CalibrationEntry | None) -> Machine | None:
    """Станок, на котором делался аналог, если он среди допустимых."""
    if analog is None:
        return None
    return next((m for m in allowed if m.code == analog.machine_code), None)


def choose_machine(part: PartFeatures, machines: list[Machine], analog_machine: str | None, strict: bool,
                   preferred: dict[str, str]) -> Machine | None:
    """Станок назначает система: станок строгого аналога, иначе основной станок технологии,
    иначе станок аналога, иначе первый допустимый. Технолог может переназначить."""
    allowed = {m.code: m for m in allowed_machines(part, machines)}
    order = [analog_machine] if strict else []
    order += [preferred.get(part.technology), analog_machine]
    for code in order:
        if code in allowed:
            return allowed[code]
    return next(iter(allowed.values()), None)
