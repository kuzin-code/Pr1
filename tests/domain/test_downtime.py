"""Причина отклонения при «Закончил»: порог, справочник, участие замера в k, сводка часов сверх плана."""
import pytest

from app.domain import downtime as dt
from app.domain.calibration import k_for
from app.domain.enums import StageGroup
from app.domain.machine_work import IDLE_CAP_FACTOR
from app.domain.models import CalibrationEntry, Dimensions, Measurement


def test_threshold_below_idle_cap():
    assert dt.OVERRUN_FACTOR == 1.2
    assert dt.OVERRUN_FACTOR < IDLE_CAP_FACTOR  # сначала причина, потом простой в загрузке


def test_reason_required_above_threshold():
    assert not dt.needs_reason(2.0, 2.4)
    assert dt.needs_reason(2.0, 2.41)
    assert not dt.needs_reason(0, 5)  # операция без плана
    with pytest.raises(dt.DowntimeError, match="причину"):
        dt.check_reason(2.0, 3.0, None)
    assert dt.check_reason(2.0, 3.0, "tool") == (dt.Reason.TOOL, "")
    assert dt.check_reason(2.0, 2.1, "") == (None, "")


def test_closed_list_and_other_needs_note():
    assert [r.label for r in dt.Reason] == ["наладка", "инструмент", "ждал заготовку", "брак", "программа",
                                            "ремонт", "другое"]
    with pytest.raises(dt.DowntimeError, match="Неизвестная"):
        dt.check_reason(1, 3, "обед")
    with pytest.raises(dt.DowntimeError, match="комментарий"):
        dt.check_reason(1, 3, "other", "  ")
    assert dt.check_reason(1, 3, "other", " сломался кран ") == (dt.Reason.OTHER, "сломался кран")


def test_which_reasons_go_to_k():
    for reason in ("setup", "tool", "program", None):
        assert dt.counts_in_k(reason)
    for reason in ("repair", "blank", "defect", "other"):
        assert not dt.counts_in_k(reason)


def test_group_reason_prefers_losses():
    assert dt.group_reason([None, "tool", "repair"]) is dt.Reason.REPAIR
    assert dt.group_reason([None, "setup"]) is dt.Reason.SETUP
    assert dt.group_reason([None, None]) is None


def test_k_ignores_loss_measurements():
    entry = CalibrationEntry("D", "", "VF2", "Д16Т", Dimensions(1, 1, 1), 9, 3, [], [
        Measurement(StageGroup.MACHINE, 1, 2),
        Measurement(StageGroup.MACHINE, 1, 1.5, reason="tool"),
        Measurement(StageGroup.MACHINE, 1, 9, reason="repair")])
    k = k_for(entry, StageGroup.MACHINE)
    assert k.value == pytest.approx(1.75) and k.count == 2
    assert not entry.measurements[2].in_k and entry.measurements[1].in_k
    restored = Measurement.from_dict(entry.measurements[2].to_dict())
    assert restored.reason == "repair"
    assert Measurement.from_dict({"group": "machine", "calc_hours": 1, "actual_hours": 1}).reason is None


def test_overrun_table_by_reason_and_machine():
    table = dt.overrun_table([
        dt.Overrun("VF2", "tool", 2, 3.5), dt.Overrun("VF2", "tool", 1, 1.5),
        dt.Overrun("ST20Y", "repair", 2, 6), dt.Overrun("VF2", None, 2, 2.2),
        dt.Overrun("VF2", "setup", 2, 1)])  # быстрее плана — не потеря
    assert table.machines == ["ST20Y", "VF2"]
    assert [(r, cells, total) for r, cells, total in table.rows] == [
        ("инструмент", {"VF2": 2.0}, 2.0), ("ремонт", {"ST20Y": 4.0}, 4.0),
        ("не указана", {"VF2": pytest.approx(0.2)}, pytest.approx(0.2))]
    assert table.totals == {"ST20Y": 4.0, "VF2": pytest.approx(2.2)}
    assert table.total == pytest.approx(6.2)
