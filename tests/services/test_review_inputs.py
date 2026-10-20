"""Инженерная проверка 20.10.2026: правка маршрута «nan», сравнение станков по плану, старые КП."""
import pytest

from app.domain.enums import Stage
from app.services import plan_service
from app.services import proposal_history as history
from app.services import quick_service as quick
from app.services import tech_service as tech
from app.services.actor import ServiceError
from tests.services.conftest import MON, TECH, part_files
from tests.services.test_quick_intake import MANAGER


@pytest.mark.parametrize("hours", ["nan", "inf"])
def test_route_hours_must_be_a_number(session, make_order, hours):
    pos = make_order().positions[0]
    with pytest.raises(ServiceError):
        tech.edit_route_stage(session, TECH, pos.id, Stage.MACHINING, float(hours), "piece", "проверка")
    plan_service.current_plan(session)  # план строится, а не зацикливается


def test_extra_machine_time_must_be_a_number(session, make_order):
    pos = make_order().positions[0]
    tech.add_machine(session, TECH, pos.id, "ST20Y")
    with pytest.raises(ServiceError):
        tech.set_extra_machine_time(session, TECH, pos.id, "ST20Y", float("nan"), 0.5, "проверка")


def test_where_faster_counts_from_same_moment_as_plan(session, tmp_path):
    # «Станки: где быстрее» и «Занятость станков» на одной странице: одна точка отсчёта — план
    r = quick.calculate(session, MANAGER, part_files("Кронштейн-1"), tmp_path / "work", start_date=MON)
    p = r.summary["positions"][0]
    current = next(a for a in p["alternatives"] if a["current"])
    busy = next(m for m in r.summary["machines"] if m["model"] == current["model"])
    assert current["free_from"][:10] == busy["start"][:10] == MON.isoformat()


def test_old_proposal_with_unknown_fields_still_opens(session, tmp_path):
    from app.services import intake_service as intake
    order = intake.quick_intake(session, MANAGER, part_files(), tmp_path / "up", start_date=MON)
    row = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    data = dict(row.data)
    data["removed_in_future"] = 1
    data["items"] = [{**i, "old_field": 2} for i in data["items"]]
    assert history.from_dict(data).items[0].designation == "Кронштейн-1"
