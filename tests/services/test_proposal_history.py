"""История коммерческих предложений: каждое КП сохраняется с данными и файлом Word; поиск и аналогичные заказы."""
from datetime import date

import pytest

from app.domain.access import AccessDenied
from app.services import intake_service as intake
from app.services import proposal_history as history
from app.services import quick_service as quick
from tests.services.conftest import MON, OPERATOR, part_files
from tests.services.test_quick_intake import MANAGER


@pytest.fixture
def order(session, tmp_path):
    return intake.quick_intake(session, MANAGER, part_files("Кронштейн-1") + part_files("Планка-2"), tmp_path / "up",
                               customer="ООО «Альфа»", desired_date=date(2026, 12, 1), start_date=MON, quantity=4)


def test_proposal_from_order_saved_with_snapshot_and_file(session, order, tmp_path):
    row = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    assert row.number == "КП-" + order.number.split("-")[-1] and row.order_id == order.id
    assert row.customer == "ООО «Альфа»" and row.grand_total > 0 and row.created_by == "manager"
    assert row.file_path and (tmp_path / "kp").exists()
    p = history.proposal_of(row)
    assert [i.designation for i in p.items] == ["Кронштейн-1", "Планка-2"] and p.items[0].quantity == 4
    assert p.range is not None and p.desired == date(2026, 12, 1)


def test_repeat_proposal_gets_next_version_and_old_one_kept(session, order, tmp_path):
    first = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    second = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    assert second.number == first.number + "/2"
    assert [r.number for r in history.list_proposals(session, MANAGER)] == [second.number, first.number]


def test_search_by_customer_and_designation(session, order, tmp_path):
    history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    assert len(history.list_proposals(session, MANAGER, "альфа")) == 1
    assert len(history.list_proposals(session, MANAGER, "Планка-2")) == 1
    assert history.list_proposals(session, MANAGER, "Бета") == []


def test_similar_proposals_by_part_designation(session, order, tmp_path):
    row = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    found = history.similar(session, ["Планка-2 Планка", "Другая-9"])
    assert [r.id for r in found] == [row.id]


def test_proposal_from_quick_calculation(session, tmp_path):
    result = quick.calculate(session, MANAGER, part_files("Кронштейн-1"), tmp_path / "work", quantity=3,
                             desired_date=date(2026, 12, 1), start_date=MON, customer="ИП Петров")
    row = history.create_from_quick(session, MANAGER, tmp_path / "work", result.token, tmp_path / "kp")
    assert row.order_id is None and row.number.startswith("КП-Б-") and row.customer == "ИП Петров"
    p = history.proposal_of(row)
    assert p.items[0].quantity == 3 and p.grand_total == pytest.approx(result.summary["grand_total"])


def test_operator_has_no_access(session, order, tmp_path):
    with pytest.raises(AccessDenied):
        history.create_from_order(session, OPERATOR, order.id, tmp_path / "kp")
    with pytest.raises(AccessDenied):
        history.list_proposals(session, OPERATOR)
