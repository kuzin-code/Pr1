"""Запуск быстрого расчёта как заказа: правки техпроцесса не технологом — предложение технологу; без двойного заказа."""
import json
from datetime import date

import pytest

from app.domain.access import AccessDenied
from app.services import quick_apply
from app.services import quick_service as quick
from app.services.actor import ServiceError
from app.storage.orm import HistoryRow, OrderRow, PositionRow
from tests.services.conftest import HEAD, MASTER, MON, TECH, part_files
from tests.services.test_quick_intake import MANAGER

EDITS = {"quantity": 7, "machine": "SMM", "material": "12Х18Н10Т", "complexity": 4}


def calc_with_edits(session, tmp_path, actor=MANAGER):
    r = quick.calculate(session, actor, part_files("Кронштейн-1"), tmp_path / "work", quantity=5,
                        desired_date=date(2026, 12, 1), start_date=MON)
    d = r.summary["positions"][0]["designation"]
    quick.update_position(session, actor, tmp_path / "work", r.token, d, EDITS)
    return r.token, d


@pytest.mark.parametrize("actor", [MANAGER, MASTER, HEAD])
def test_not_technologist_applies_only_quantity_rest_is_proposal(session, tmp_path, actor):
    token, d = calc_with_edits(session, tmp_path, MANAGER)
    order = quick.launch(session, actor, tmp_path / "work", token, tmp_path / "up")
    pos = order.positions[0]
    assert pos.quantity == 7 and pos.machine_code != "SMM" and pos.data.get("material") != "12Х18Н10Т"
    assert pos.complexity != 4
    proposal = pos.data["quick_proposal"]
    assert proposal["values"] == {"machine": "SMM", "material": "12Х18Н10Т", "complexity": 4}
    assert proposal["by"] == actor.username and proposal["at"]
    assert session.query(HistoryRow).filter_by(entity="position", entity_id=str(pos.id), field="quick_proposal").count() == 1
    labels = dict(quick_apply.describe(proposal["values"]))
    assert labels["Станок"] == "SMM" and labels["Материал"] == "12Х18Н10Т"


def test_technologist_launch_applies_edits_in_his_name(session, tmp_path):
    token, d = calc_with_edits(session, tmp_path, TECH)
    pos = quick.launch(session, TECH, tmp_path / "work", token, tmp_path / "up").positions[0]
    assert pos.quantity == 7 and pos.machine_code == "SMM" and pos.data["material"] == "12Х18Н10Т"
    assert "quick_proposal" not in pos.data
    rows = session.query(HistoryRow).filter_by(entity="position", entity_id=str(pos.id), field="material").all()
    assert rows and rows[-1].username == "technolog"


def test_technologist_accepts_or_rejects_proposal(session, tmp_path):
    token, _ = calc_with_edits(session, tmp_path)
    pos = quick.launch(session, MANAGER, tmp_path / "work", token, tmp_path / "up").positions[0]
    with pytest.raises(AccessDenied):
        quick_apply.accept_proposal(session, MANAGER, pos.id, "согласен")
    with pytest.raises(ValueError):
        quick_apply.accept_proposal(session, TECH, pos.id, "")  # причина обязательна
    quick_apply.accept_proposal(session, TECH, pos.id, "проверил по чертежу")
    pos = session.get(PositionRow, pos.id)
    assert pos.machine_code == "SMM" and pos.data["material"] == "12Х18Н10Т" and pos.complexity == 4
    assert "quick_proposal" not in pos.data
    with pytest.raises(ServiceError):
        quick_apply.reject_proposal(session, TECH, pos.id, "уже нет")  # предложения больше нет


def test_reject_proposal_keeps_tech_data(session, tmp_path):
    token, _ = calc_with_edits(session, tmp_path)
    pos = quick.launch(session, MANAGER, tmp_path / "work", token, tmp_path / "up").positions[0]
    machine = pos.machine_code
    with pytest.raises(ValueError):
        quick_apply.reject_proposal(session, TECH, pos.id, " ")
    quick_apply.reject_proposal(session, TECH, pos.id, "станок занят, оставляем VF-2")
    pos = session.get(PositionRow, pos.id)
    assert pos.machine_code == machine and "quick_proposal" not in pos.data
    assert session.query(HistoryRow).filter_by(entity_id=str(pos.id), field="quick_proposal").count() == 2


def test_second_launch_does_not_create_second_order(session, tmp_path):
    token, _ = calc_with_edits(session, tmp_path)
    (tmp_path / "work" / "_quick" / token / "launch.lock").touch()  # первое нажатие ещё выполняется
    with pytest.raises(ServiceError, match="уже запускается"):
        quick.launch(session, MANAGER, tmp_path / "work", token, tmp_path / "up")
    assert session.query(OrderRow).count() == 0
    (tmp_path / "work" / "_quick" / token / "launch.lock").unlink()
    quick.launch(session, MANAGER, tmp_path / "work", token, tmp_path / "up")
    with pytest.raises(ServiceError):
        quick.launch(session, MANAGER, tmp_path / "work", token, tmp_path / "up")
    assert session.query(OrderRow).count() == 1


def test_failed_edits_leave_no_order(session, tmp_path):
    token, d = calc_with_edits(session, tmp_path, TECH)
    path = tmp_path / "work" / "_quick" / token / "summary.json"
    s = json.loads(path.read_text(encoding="utf-8"))
    s["params"]["overrides"][d]["machine"] = "BS275"  # ленточная пила — не станок для этой детали
    path.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ServiceError):
        quick.launch(session, TECH, tmp_path / "work", token, tmp_path / "up")
    assert session.query(OrderRow).count() == 0
    assert quick.load(tmp_path / "work", token)  # расчёт остался, можно исправить и запустить снова
    assert not (tmp_path / "work" / "_quick" / token / "launch.lock").exists()
