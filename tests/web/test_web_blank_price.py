"""Веб: прайс заготовок в «Справочниках», цена заготовок в карточке позиции, быстрый расчёт и КП."""
import re

from tests.fixtures.price_list import price_xlsx
from tests.web.test_web_flow import create_order, prepare

LINE = "Кусок по прайсу prutki.ru от 26.10.2026: плита алюминиевая Д16Т 50×100×200 мм (код 114102)"


def upload(client, content=None, reason="обновление прайса"):
    return client.post("/reference/blank-prices", data={"reason": reason},
                       files={"file": ("price.xlsx", content or price_xlsx())})


def test_reference_upload_with_history_and_rights(login):
    it = login("it")
    resp = upload(it)
    assert resp.status_code == 303 and resp.headers["location"] == "/reference#blank-prices"
    page = it.get("/reference").text
    assert 'id="blank-prices"' in page and "Прайс заготовок загружен" in page
    assert "26.10.2026" in page and "позиций: <b>10</b>" in page and "с НДС 22 %" in page
    assert "AISI 304, 12Х18Н10Т, 12Х18Н9Т, AISI 321 = AISI 304; круг: 12Х18Н10Т" in page
    head = login("head").get("/reference").text
    assert "26.10.2026" in head and 'action="/reference/blank-prices"' not in head  # смотрит, не загружает
    assert upload(login("head")).status_code == 403
    assert login("manager").get("/reference").status_code == 403
    bad = upload(it, b"not excel")
    assert "Прайс не загружен" in it.get(bad.headers["location"]).text


def test_position_card_shows_blank_price_and_manual_price(login):
    upload(login("it"))
    _, pid = create_order(login)
    prepare(login, pid)
    tech = login("technolog")
    page = tech.get(f"/positions/{pid}").text
    assert LINE in page and "из куска 1 заготовка, нужно 13 кусков на 10 + 3 шт." in page
    assert "13 × 3 290 ₽ = <b>42 770 ₽</b> (с НДС 22 %)" in page
    assert "Заготовки (отдельная статья" in page  # в расчёте — отдельно от себестоимости обработки
    assert "position.blank_price" in page  # кнопка помощи «?»
    assert LINE in login("head").get(f"/positions/{pid}").text
    assert "Цена заготовок" not in login("manager").get(f"/positions/{pid}").text

    form = {"price": "500", "note": "счёт", "reason": ""}
    resp = tech.post(f"/positions/{pid}/blank-price", data=form)
    assert "причин" in tech.get(resp.headers["location"]).text.lower()
    tech.post(f"/positions/{pid}/blank-price", data={**form, "reason": "договорная цена"})
    page = tech.get(f"/positions/{pid}").text
    assert "задано технологом" in page and "6 500 ₽" in page and "договорная цена" in page
    assert login("head").post(f"/positions/{pid}/blank-price", data={**form, "reason": "x"}).status_code == 403
    tech.post(f"/positions/{pid}/blank-price", data={"reset": "1", "reason": "по прайсу"})
    assert "42 770 ₽" in tech.get(f"/positions/{pid}").text


def test_cutter_does_not_see_price(login):
    from tests.web.test_web_operations import approve
    upload(login("it"))
    approve(login)
    assert "₽" not in login("zagotovitel").get("/work").text


def test_quick_and_proposal_pages_show_blanks(login):
    from tests.fixtures.samples import make_box_step, make_pdf
    upload(login("it"))
    manager = login("manager")
    files = [("files", ("Q-1.step", make_box_step(152, 58, 40).encode())),
             ("files", ("Q-1.pdf", make_pdf(["Material: D16T", "Bore 10H9"])))]
    resp = manager.post("/quick", data={"quantity": "10", "start_date": "2026-10-12"}, files=files)
    page = manager.get(resp.headers["location"]).text
    assert "Заготовки (материал по прайсу prutki.ru от 26.10.2026)" in page and "13 ×" in page
    assert "доставка заготовок" in page.lower()
    token = re.search(r"/quick/([0-9a-f]{32})/launch", page).group(1)
    kp = manager.post(f"/quick/{token}/proposal")
    page = manager.get(kp.headers["location"]).text
    assert "Цена операций и заготовок (на партию)" in page and "13 шт. ×" in page
