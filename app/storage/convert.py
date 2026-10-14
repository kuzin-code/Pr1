"""Преобразование строк БД в доменные объекты и обратно."""
from app.domain.models import CalibrationEntry, Dimensions, Machine, Measurement, StageNorm
from app.storage.orm import JournalRow, MachineRow


def machine_from_row(row: MachineRow) -> Machine:
    return Machine(
        code=row.code, model=row.model, kind=row.kind, is_cnc=row.is_cnc,
        work_zone=Dimensions.from_list(row.work_zone), technologies=frozenset(row.technologies or ()),
        controller=row.controller, accuracy=row.accuracy, power=row.power,
        hours_per_day=row.hours_per_day,
    )


def entry_from_row(row: JournalRow) -> CalibrationEntry:
    return CalibrationEntry(
        part_code=row.part_code, name=row.name, machine_code=row.machine_code, material=row.material,
        dims=Dimensions.from_list(row.dims), tolerance_grade=row.tolerance_grade,
        complexity=row.complexity, stages=[StageNorm.from_dict(s) for s in row.stages],
        measurements=[Measurement.from_dict(m) for m in row.measurements], technology=row.technology,
    )


def entry_to_row(entry: CalibrationEntry, row: JournalRow | None = None) -> JournalRow:
    row = row or JournalRow(part_code=entry.part_code)
    row.part_code = entry.part_code
    row.name = entry.name
    row.machine_code = entry.machine_code
    row.material = entry.material
    row.dims = entry.dims.as_list()
    row.tolerance_grade = entry.tolerance_grade
    row.complexity = entry.complexity
    row.technology = entry.technology
    row.stages = [s.to_dict() for s in entry.stages]
    row.measurements = [m.to_dict() for m in entry.measurements]
    return row
