"""Кнопки помощи «?» и ссылки «Как это сделать?» стоят на рабочих экранах и ведут на пункты справки."""
import re

import pytest

from app.web.help import TOPICS
from tests.web.test_web_operations import approve, op_ids, press

TIP = re.compile(r'<details class="help-tip[^"]*">.*?href="/help#([\w.]+)"', re.S)
LINK = re.compile(r'<a class="help-link" href="/help#([\w.]+)"')

# Экран → (пользователь, адрес, ключи, которые обязательно есть)
SCREENS = {
    "рабочий экран": ("head", "/dashboard", {"dashboard.tasks"}),
    "приём заказа": ("manager", "/orders/new", {"intake.files", "intake.dates", "intake.quantity"}),
    "быстрый расчёт": ("manager", "/quick", {"quick.start"}),
    "загрузка станков": ("head", "/plan", {"plan.views", "plan.gantt", "plan.deviations", "plan.priority"}),
    "загрузка станков, мастер": ("master", "/plan", {"plan.busy"}),
    "сменное задание": ("operator", "/plan/shift?machine=VF2", {"shift.open"}),
    "мои операции": ("operator", "/work", {"work.queue"}),
    "журнал калибровки": ("technolog", "/journal", {"journal.what", "journal.add", "journal.excel"}),
    "уведомления": ("master", "/notifications", {"notifications.list"}),
    "КП": ("manager", "/proposals", {"kp.history"}),
    "пользователи": ("it", "/users", {"it.users"}),
    "мой пароль": ("operator", "/account", {"start.password"}),
}


def _keys(html: str) -> tuple[list[str], list[str]]:
    return TIP.findall(html), LINK.findall(html)


@pytest.mark.parametrize("screen", SCREENS)
def test_help_on_screen(login, screen):
    user, url, expected = SCREENS[screen]
    resp = login(user).get(url)
    assert resp.status_code == 200
    tips, links = _keys(resp.text)
    assert expected <= set(tips), f"{screen}: нет {expected - set(tips)}"
    assert set(tips) | set(links) <= set(TOPICS)


def test_help_on_order_and_position_cards(login):
    pid = approve(login)
    tech = login("technolog").get(f"/positions/{pid}").text
    tips, _ = _keys(tech)
    for key in ("position.data", "position.complexity", "position.analog", "position.route", "position.machine",
                "position.blank", "position.operations"):
        assert key in tips, key
    oid = re.search(r'href="/orders/(\d+)"', tech).group(1)
    order_tips, order_links = _keys(login("manager").get(f"/orders/{oid}").text)
    assert {"order.positions", "intake.dates"} <= set(order_tips) and "kp.make" in order_links


def test_work_card_tips_for_cutter_and_links(login):
    approve(login)
    cutter = login("zagotovitel")
    assert "work.blank" in _keys(cutter.get("/work").text)[0]  # заготовка на карточке резки
    (cut,) = op_ids(cutter.get("/work").text)
    press(cutter, cut, "start", "2026-10-19T09:00")
    html = cutter.get("/work").text  # операция в работе: «Закончил» и причина отклонения
    tips, links = _keys(html)
    assert {"work.queue", "work.blank", "work.reason"} <= set(tips)
    assert {"work.start", "work.undo"} <= set(links)
    # Кнопки «Начал» / «Закончил» остаются чистыми: «?» не внутри кнопки.
    assert not re.search(r'<button class="w-big[^>]*>[^<]*<details', html)


def test_empty_queue_has_next_step(login):
    html = login("slesar").get("/work").text
    _, links = _keys(html)
    assert "Операций в очереди нет" in html
    assert "work.queue" in links
