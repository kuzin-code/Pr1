"""Приём заказа за минуту: одно поле для списка, STEP и PDF; заказчик, желаемая дата; уведомления участникам."""
from datetime import date, datetime

import pytest

from app.domain.access import AccessDenied
from app.domain.enums import Role
from app.services import intake_service as intake
from app.services import plan_service
from app.services.actor import Actor, ServiceError
from app.services.order_service import UploadedFile
from app.storage.orm import NotificationRow
from tests.fixtures.samples import make_order_list
from tests.services.conftest import HEAD, MASTER, MON, OPERATOR, TECH, part_files

MANAGER = Actor("manager", Role.MANAGER)


def notes(session):
    return {n.role: n.text for n in session.query(NotificationRow).filter_by(kind="new_order")}


def test_manager_takes_order_from_files_with_customer_and_desired_date(session, tmp_path):
    order = intake.quick_intake(session, MANAGER, part_files("Кронштейн-1") + part_files("Планка-2"),
                                tmp_path / "up", customer="ООО «Альфа»", desired_date=date(2026, 11, 20),
                                start_date=MON)
    assert order.customer == "ООО «Альфа»" and order.desired_date == date(2026, 11, 20)
    assert len(order.positions) == 2 and all(p.current_calc for p in order.positions)
    assert order.number.startswith("З-")


def test_list_and_drawings_in_one_field(session, tmp_path):
    path = make_order_list(tmp_path / "Заказ.xlsx", [("Кронштейн-1", "Кронштейн", 4, "Д16Т")],
                           desired_date=datetime(2026, 12, 1))
    files = [UploadedFile("Заказ.xlsx", path.read_bytes())] + part_files("Кронштейн-1")
    order = intake.quick_intake(session, TECH, files, tmp_path / "up", start_date=MON)
    assert order.positions[0].quantity == 4 and order.positions[0].step_name
    assert order.customer == "ООО «Тест»" and order.desired_date == date(2026, 12, 1)  # из шапки списка


def test_form_values_override_list_header(session, tmp_path):
    path = make_order_list(tmp_path / "Заказ.xlsx", [("Кронштейн-1", "Кронштейн", 1, "Д16Т")])
    files = [UploadedFile("Заказ.xlsx", path.read_bytes())] + part_files("Кронштейн-1")
    order = intake.quick_intake(session, HEAD, files, tmp_path / "up", customer="ИП Иванов",
                                desired_date=date(2026, 11, 2), start_date=MON)
    assert order.customer == "ИП Иванов" and order.desired_date == date(2026, 11, 2)


def test_start_date_defaults_to_today(session, tmp_path):
    order = intake.quick_intake(session, MASTER, part_files(), tmp_path / "up")
    assert order.start_date == date.today()


def test_two_lists_or_no_files_rejected(session, tmp_path):
    with pytest.raises(ServiceError):
        intake.quick_intake(session, MASTER, [], tmp_path / "up")
    path = make_order_list(tmp_path / "Заказ.xlsx", [("A-1", "A", 1, "Д16Т")])
    two = [UploadedFile("1.xlsx", path.read_bytes()), UploadedFile("2.xlsx", path.read_bytes())]
    with pytest.raises(ServiceError, match="один список"):
        intake.quick_intake(session, MASTER, two, tmp_path / "up")


def test_operator_cannot_take_orders(session, tmp_path):
    with pytest.raises(AccessDenied):
        intake.quick_intake(session, OPERATOR, part_files(), tmp_path / "up")


def test_new_order_notifies_everyone_involved_except_creator(session, tmp_path):
    intake.quick_intake(session, MANAGER, part_files("Кронштейн-1") + part_files("Планка-2"), tmp_path / "up",
                        customer="ООО «Альфа»", desired_date=date(2026, 11, 20), start_date=MON)
    got = notes(session)
    assert set(got) >= {"technologist", "head", "master", "operator", "cutter", "locksmith", "inspector"}
    assert "manager" not in got
    assert "Новый заказ З-" in got["technologist"] and "проверьте расчёт" in got["technologist"]
    assert "ООО «Альфа»" in got["head"] and "20.11.2026" in got["head"] and "Расчётная дата" in got["head"]
    assert "VF-2" in got["operator"] and "2 поз." in got["operator"] and " ч" in got["operator"]
    assert "резка" in got["cutter"] and "контроль" in got["inspector"]


def test_creator_role_not_notified(session, tmp_path):
    intake.quick_intake(session, MASTER, part_files(), tmp_path / "up", start_date=MON)
    assert "master" not in notes(session) and None not in notes(session)


def test_technologist_and_head_see_their_notifications(session, tmp_path):
    intake.quick_intake(session, MANAGER, part_files(), tmp_path / "up", start_date=MON)
    assert plan_service.unread_count(session, Role.TECHNOLOGIST) == 1
    assert [n.kind for n in plan_service.list_notifications(session, HEAD)] == ["new_order"]


def test_position_without_files_mentioned_for_master(session, tmp_path):
    files = part_files("Кронштейн-1") + [part_files("Планка-2")[1]]  # у второй детали только PDF
    intake.quick_intake(session, MANAGER, files, tmp_path / "up", start_date=MON)
    assert "без пары STEP+PDF: 1" in notes(session)["master"]


def test_stray_files_skipped_with_warning_not_whole_order(session, tmp_path):
    files = part_files() + [UploadedFile("фото.jpg", b"x")]
    order = intake.quick_intake(session, MASTER, files, tmp_path / "up", start_date=MON)
    assert len(order.positions) == 1 and "фото.jpg" in order.intake_warnings[0]


def test_part_known_in_journal_takes_its_technology(session, tmp_path):
    order = intake.quick_intake(session, MANAGER, part_files("DEMO-07", dims=(180, 40, 40)), tmp_path / "up",
                                start_date=MON)  # DEMO-07 «Вал» в журнале — токарная, ST-20Y
    pos = order.positions[0]
    assert pos.analog_code == "DEMO-07" and pos.machine_code == "ST20Y"
