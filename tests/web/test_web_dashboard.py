"""Веб: рабочий экран — главная страница офисных ролей."""
import pytest

from tests.web.test_web_operations import approve


@pytest.mark.parametrize("user", ["technolog", "master", "head", "management"])
def test_office_roles_land_on_dashboard(login, user):
    assert login(user).get("/").headers["location"] == "/dashboard"
    page = login(user).get("/dashboard").text
    assert 'class="dash-hero"' in page and "Заказов в работе" in page and "Загрузка ЧПУ на неделе" in page
    assert "Главная" in page  # пункт меню


@pytest.mark.parametrize("user", ["operator", "slesar", "it"])
def test_other_roles_keep_their_home(login, user):
    assert login(user).get("/").headers["location"] != "/dashboard"
    assert login(user).get("/dashboard").status_code == 403


def test_role_tasks_and_cost_visibility(login):
    from tests.web.test_web_flow import create_order, prepare
    _, pid = create_order(login)
    prepare(login, pid)
    login("technolog").post(f"/positions/{pid}/submit")
    head = login("head").get("/dashboard").text
    assert "Позиции к утверждению" in head and f'href="/positions/{pid}"' in head and "₽" in head
    tech = login("technolog").get("/dashboard").text
    assert "Расчёты на проверку" in tech and "₽" not in tech


def test_week_machines_and_deadlines(login):
    approve(login)
    page = login("management").get("/dashboard").text
    assert "Haas VF-2" in page and "Ближайшие сроки" in page and "З-0001" in page
    assert 'href="/positions/' not in page  # руководству карточки позиций недоступны
