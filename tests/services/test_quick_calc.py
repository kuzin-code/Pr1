"""Быстрый расчёт по файлам: цена, срок по текущей загрузке ±10 %, занятость станков — без записи в базу."""
from datetime import date

import pytest

from app.domain.access import AccessDenied
from app.services import plan_service
from app.services import quick_service as quick
from app.services.actor import ServiceError
from app.storage.orm import HistoryRow, NotificationRow, OrderRow, PositionRow
from tests.services.conftest import HEAD, MON, OPERATOR, TECH, part_files
from tests.services.test_quick_intake import MANAGER


def counts(session):
    return tuple(session.query(m).count() for m in (OrderRow, PositionRow, NotificationRow, HistoryRow))


@pytest.fixture
def result(session, tmp_path):
    return quick.calculate(session, MANAGER, part_files("Кронштейн-1") + part_files("Планка-2"), tmp_path / "work",
                           quantity=5, desired_date=date(2026, 12, 1), start_date=MON)


def test_calculation_does_not_touch_database(session, tmp_path):
    before = counts(session)
    plan_before = plan_service.current_plan(session).slots
    quick.calculate(session, MANAGER, part_files(), tmp_path / "work", start_date=MON)
    assert counts(session) == before
    assert plan_service.current_plan(session).slots == plan_before


def test_result_has_price_dates_and_machines(result):
    s = result.summary
    assert [i["quantity"] for i in s["items"]] == [5, 5]
    assert s["total"] > 0 and s["grand_total"] == pytest.approx(s["total"] * 1.22)
    assert s["items"][0]["machine_hours"] > 0 and s["items"][0]["machine_rate"] == 2000
    assert s["range"]["earliest"] <= s["calc_date"][:10] <= s["range"]["latest"]
    assert s["check"]["status"] in ("ok", "range", "late") and s["check"]["label"]
    assert s["machines"] and s["machines"][0]["model"] == "Haas VF-2" and s["machines"][0]["positions"] == 2
    assert s["bottleneck"] == "Haas VF-2"


def test_issues_of_positions_shown(session, tmp_path):
    files = part_files("Кронштейн-1") + [part_files("Планка-2")[1]]
    s = quick.calculate(session, MANAGER, files, tmp_path / "work", start_date=MON).summary
    assert any("STEP" in text for item in s["issues"] for text in item["issues"])


def test_saved_result_can_be_reopened_and_discarded(result, tmp_path):
    assert quick.load(tmp_path / "work", result.token)["total"] == result.summary["total"]
    quick.discard(tmp_path / "work", result.token)
    with pytest.raises(ServiceError):
        quick.load(tmp_path / "work", result.token)


def test_launch_creates_real_order_with_same_files(session, result, tmp_path):
    order = quick.launch(session, MANAGER, tmp_path / "work", result.token, tmp_path / "uploads")
    assert len(order.positions) == 2 and order.positions[0].quantity == 5
    assert order.desired_date == date(2026, 12, 1)
    with pytest.raises(ServiceError):  # расчёт израсходован
        quick.load(tmp_path / "work", result.token)


def test_notify_head_with_summary(session, result, tmp_path):
    quick.notify_head(session, MANAGER, tmp_path / "work", result.token)
    (note,) = plan_service.list_notifications(session, HEAD)
    assert note.kind == "quick_calc" and "Быстрый расчёт" in note.text and "₽" in note.text
    assert "VF-2" in note.text


def test_operator_cannot_calculate(session, tmp_path):
    with pytest.raises(AccessDenied):
        quick.calculate(session, OPERATOR, part_files(), tmp_path / "work")


def test_bad_token_rejected(tmp_path):
    with pytest.raises(ServiceError):
        quick.load(tmp_path / "work", "../../etc")


def test_edit_parameters_and_recalculate_without_touching_database(session, result, tmp_path):
    before = counts(session)
    pos = result.summary["positions"][0]
    s = quick.update_position(session, MANAGER, tmp_path / "work", result.token, pos["designation"],
                              {"quantity": 20, "material": "12Х18Н10Т", "machine": "SMM"})
    p = next(x for x in s["positions"] if x["designation"] == pos["designation"])
    assert p["quantity"] == 20 and p["material"] == "12Х18Н10Т" and p["machine"] == "SMM"
    assert s["total"] != result.summary["total"]
    assert any(m["model"] == "Haas Super Mini Mill" for m in s["machines"])
    assert counts(session) == before
    assert quick.load(tmp_path / "work", result.token)["positions"][0]["quantity"] == 20  # сохранено


def test_second_machine_and_route_edit_in_quick_calc(session, result, tmp_path):
    d = result.summary["positions"][0]["designation"]
    s = quick.update_position(session, MANAGER, tmp_path / "work", result.token, d,
                              {"extra_machines": [{"machine": "ST20Y"}]})
    p = next(x for x in s["positions"] if x["designation"] == d)
    assert [e["machine"] for e in p["extra_machines"]] == ["ST20Y"]
    assert any(m["model"] == "Haas ST-20Y" for m in s["machines"])
    s2 = quick.update_position(session, MANAGER, tmp_path / "work", result.token, d, {"route": {"setup": 3.0}})
    p2 = next(x for x in s2["positions"] if x["designation"] == d)
    assert next(r["hours"] for r in p2["route"] if r["stage"] == "setup") == 3.0
    assert [e["machine"] for e in p2["extra_machines"]] == ["ST20Y"]  # прежние правки сохраняются


def test_bad_edit_keeps_previous_result(session, result, tmp_path):
    d = result.summary["positions"][0]["designation"]
    with pytest.raises(ServiceError):
        quick.update_position(session, MANAGER, tmp_path / "work", result.token, d, {"machine": "BS275"})
    assert quick.load(tmp_path / "work", result.token)["total"] == result.summary["total"]


def test_launch_by_technologist_carries_edits_into_order(session, result, tmp_path):
    d = result.summary["positions"][0]["designation"]
    quick.update_position(session, MANAGER, tmp_path / "work", result.token, d, {"quantity": 7, "machine": "SMM"})
    order = quick.launch(session, TECH, tmp_path / "work", result.token, tmp_path / "uploads")
    pos = next(p for p in order.positions if p.designation == d)
    assert pos.quantity == 7 and pos.machine_code == "SMM"


def test_machine_alternatives_show_where_faster(session, result):
    p = result.summary["positions"][0]
    alts = {a["machine"]: a for a in p["alternatives"]}
    assert {"VF2", "SMM"} <= set(alts)  # фрезерные станки, куда деталь помещается
    assert all(a["machine_hours"] > 0 and a["cost"] > 0 and a["ready"] >= a["free_from"] for a in alts.values())
    assert sum(a.get("fastest", False) for a in alts.values()) == 1
    assert alts[p["machine"]]["current"]


def test_choosing_lathe_switches_technology(session, result, tmp_path):
    d = result.summary["positions"][0]["designation"]
    s = quick.update_position(session, MANAGER, tmp_path / "work", result.token, d, {"machine": "ST20Y"})
    p = next(x for x in s["positions"] if x["designation"] == d)
    assert p["machine"] == "ST20Y" and p["technology"] == "turning"
