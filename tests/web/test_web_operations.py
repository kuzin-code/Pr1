"""Веб: окно специалиста — очередь операций, кнопки «Начал» / «Закончил», уведомления."""
import re

from tests.web.test_web_flow import create_order, prepare


def approve(login):
    _, pid = create_order(login)
    prepare(login, pid)
    login("technolog").post(f"/positions/{pid}/submit")
    login("head").post(f"/positions/{pid}/approve")
    return pid


def op_ids(page):
    return [int(x) for x in re.findall(r'action="/operations/(\d+)/start"', page)]


def press(client, op_id, action, when):
    resp = client.post(f"/operations/{op_id}/{action}", data={"at": when})
    assert resp.status_code == 303, resp.text
    return resp


def test_specialists_see_own_queue_and_press_buttons(login):
    pid = approve(login)
    cutter = login("zagotovitel")
    page = cutter.get("/work").text
    assert "Резка заготовки" in page and "Фрезерная обработка" not in page
    (cut,) = op_ids(page)
    press(cutter, cut, "start", "2026-10-19T09:00")
    press(cutter, cut, "finish", "2026-10-19T10:00")

    operator = login("operator")
    page = operator.get("/work?machine=VF2").text
    assert "Наладка" in page and "Фрезерная обработка" in page and "можно начинать" in page
    for s, e in (("2026-10-19T10:00", "2026-10-19T11:00"), ("2026-10-19T11:00", "2026-10-20T13:00")):
        (op,) = op_ids(operator.get("/work").text)  # кнопка «Начал» — только у доступной операции
        press(operator, op, "start", s)
        press(operator, op, "finish", e)
    assert "Наладка: можно начинать" in operator.get("/notifications").text

    slesar = login("slesar")
    assert "Слесарная: можно начинать" in slesar.get("/notifications").text
    position = login("technolog").get(f"/positions/{pid}").text
    assert "Слесарно-финишная обработка" in position  # статус позиции сменился сам
    assert "выполнена" in position and "Резка заготовки" in position


def test_wrong_specialist_and_errors(login):
    approve(login)
    (cut,) = op_ids(login("zagotovitel").get("/work").text)
    assert login("slesar").post(f"/operations/{cut}/start", data={"at": ""}).status_code == 403
    resp = login("zagotovitel").post(f"/operations/{cut}/finish", data={"at": ""})
    assert resp.status_code == 303
    assert "не начата" in login("zagotovitel").get("/work").text


def test_home_pages_for_specialists(login):
    for user in ("operator", "zagotovitel", "slesar", "kontroler"):
        assert login(user).get("/").headers["location"] == "/work"
    assert login("technolog").get("/work").status_code == 403
    assert login("master").get("/work").status_code == 200
    assert login("master").get("/facts").headers["location"] == "/work"


def test_master_undoes_mistaken_mark(login):
    pid = approve(login)
    cutter = login("zagotovitel")
    (cut,) = op_ids(cutter.get("/work").text)
    press(cutter, cut, "start", "2026-10-19T09:00")
    master = login("master")
    page = master.get(f"/positions/{pid}").text
    assert "Отменить отметку" in page and "«Начал»: Резка заготовки" in page
    assert "Отменить отметку" not in login("technolog").get(f"/positions/{pid}").text
    assert login("operator").post(f"/positions/{pid}/undo", data={"reason": "x"}).status_code == 403
    assert master.post(f"/positions/{pid}/undo", data={"reason": "не та деталь"}).status_code == 303
    assert cut in op_ids(cutter.get("/work").text)  # снова «можно начинать»


def test_unread_counter_in_menu_and_auto_refresh(login):
    import re as _re
    pid = approve(login)
    cutter = login("zagotovitel")
    (cut,) = op_ids(cutter.get("/work").text)
    press(cutter, cut, "start", "2026-10-19T09:00")
    press(cutter, cut, "finish", "2026-10-19T10:00")
    page = login("operator").get("/work").text
    # «Новый заказ» и «Наладка: можно начинать»
    assert _re.search(r'Уведомления <span class="nav-count">2</span>', page)
    assert "data-auto-refresh" in page
    login("operator").get("/notifications")  # прочитаны
    assert "nav-count" not in login("operator").get("/work").text
    assert pid
