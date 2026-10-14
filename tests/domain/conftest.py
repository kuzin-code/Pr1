import pytest

from app.domain.enums import CountMode, Stage, StageGroup
from app.domain.models import CalibrationEntry, Dimensions, Machine, Measurement, StageNorm


def make_entry(code="P-1", dims=(100, 60, 20), material="Д16Т", grade=7, complexity=3,
               machine="VF2", measurements=(), technology="milling"):
    return CalibrationEntry(
        part_code=code,
        name=f"Деталь {code}",
        machine_code=machine,
        material=material,
        dims=Dimensions(*dims),
        tolerance_grade=grade,
        complexity=complexity,
        stages=[
            StageNorm(Stage.CUTTING, 0.2, CountMode.PER_PIECE),
            StageNorm(Stage.PROGRAMMING, 2.0, CountMode.ONCE),
            StageNorm(Stage.SETUP, 1.5, CountMode.PER_BATCH),
            StageNorm(Stage.MACHINING, 0.5, CountMode.PER_PIECE),
            StageNorm(Stage.LOCKSMITH, 0.25, CountMode.PER_PIECE),
            StageNorm(Stage.FINISHING, 0.1, CountMode.PER_PIECE),
            StageNorm(Stage.INSPECTION, 0.5, CountMode.PER_BATCH),
        ],
        measurements=list(measurements),
        technology=technology,
    )


def machine_measurements(*ratios):
    return [Measurement(StageGroup.MACHINE, 10.0, 10.0 * r) for r in ratios]


@pytest.fixture
def entry():
    return make_entry()


@pytest.fixture
def machines():
    return [
        Machine("VF2", "Haas VF-2", "фрезерный", True, Dimensions(762, 406, 508), frozenset({"milling"})),
        Machine("SMM", "Haas Super Mini Mill", "фрезерный", True, Dimensions(406, 305, 254), frozenset({"milling"})),
        Machine("ST20Y", "Haas ST-20Y", "токарно-фрезерный", True, Dimensions(533, 533, 300),
                frozenset({"turning", "milling"})),
        Machine("SAW", "Holzmann BS275TOP", "ленточнопильный", False, None, frozenset({"cutting"})),
    ]
