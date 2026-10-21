"""Ступени количества на страницах: быстрый расчёт, смена ступеней, КП по быстрому расчёту и по заказу."""
import re

from fastapi.testclient import TestClient

from tests.fixtures.samples import make_box_step, make_pdf
from tests.web.test_quick_web import client_for, files

HEAD_ROW = "Цена в зависимости от количества"


def test_quick_calc_with_breaks_and_proposal(app):
    c = client_for(app)
    assert 'name="quantities"' in c.get("/quick").text
    resp = c.post("/quick", data={"quantity": "10", "quantities": "1, 10, 50"}, files=files())
    assert resp.status_code == 200 and HEAD_ROW in resp.text
    token = re.search(r"/quick/([0-9a-f]{32})/launch", resp.text).group(1)
    page = c.post(f"/quick/{token}/quantities", data={"quantities": "1, 100"}).text
    assert "Ступени количества: 1, 100 — пересчитано" in page and HEAD_ROW in page
    bad = c.post(f"/quick/{token}/quantities", data={"quantities": "1, 2, 3, 4, 5"}).text
    assert "Не больше 4" in bad
    kp = c.post(f"/quick/{token}/proposal").text
    assert HEAD_ROW in kp and "Готовность на станке" in kp
    assert "Заказов нет" in c.get("/orders").text


def test_quick_calc_without_breaks_has_no_table(app):
    c = client_for(app)
    resp = c.post("/quick", data={"quantity": "3"}, files=files())
    assert HEAD_ROW not in resp.text and "Ступени количества" in resp.text  # поле для ввода есть


def test_order_proposal_with_breaks(app):
    c = TestClient(app)
    c.post("/login", data={"username": "manager", "password": "demo"})
    page = c.post("/orders", files=[("files", ("P-1.step", make_box_step(90, 50, 20).encode())),
                                    ("files", ("P-1.pdf", make_pdf(["Bore 10H9"])))]).text
    assert 'name="quantities"' in page
    order_id = re.search(r'action="/orders/(\d+)/proposal"', page).group(1)
    plain = c.post(f"/orders/{order_id}/proposal").text
    assert "Коммерческое предложение" in plain and HEAD_ROW not in plain  # как раньше
    kp = c.post(f"/orders/{order_id}/proposal", data={"quantities": "1, 25"}).text
    assert HEAD_ROW in kp
    bad = c.post(f"/orders/{order_id}/proposal", data={"quantities": "ноль"}).text
    assert "Ступени количества" in bad


def test_operator_cannot_change_breaks(app):
    c = client_for(app)
    resp = c.post("/quick", data={"quantity": "1"}, files=files())
    token = re.search(r"/quick/([0-9a-f]{32})/launch", resp.text).group(1)
    op = client_for(app, "operator")
    assert op.post(f"/quick/{token}/quantities", data={"quantities": "1"}).status_code == 403


def test_old_proposal_without_breaks_opens(app):
    """КП, сохранённое до ступеней количества (в снимке нет breaks и breaks_note), открывается как раньше."""
    from app.storage.orm import ProposalRow

    c = client_for(app)
    resp = c.post("/quick", data={"quantity": "2", "quantities": "1, 10"}, files=files())
    token = re.search(r"/quick/([0-9a-f]{32})/launch", resp.text).group(1)
    kp_id = re.search(r"/proposals/(\d+)/file", c.post(f"/quick/{token}/proposal").text).group(1)
    with app.state.session_factory() as db:
        row = db.get(ProposalRow, int(kp_id))
        data = {k: v for k, v in row.data.items() if k != "breaks_note"}
        data["items"] = [{k: v for k, v in i.items() if k != "breaks"} for i in data["items"]]
        row.data = data
        db.commit()
    page = c.get(f"/proposals/{kp_id}")
    assert page.status_code == 200 and "Коммерческое предложение" in page.text and HEAD_ROW not in page.text


def test_quantity_fields_limited_to_100(app):
    c = client_for(app)
    assert 'name="quantity" min="1" max="100"' in c.get("/quick").text
    assert 'max="100"' in c.get("/orders/new").text
    resp = c.post("/quick", data={"quantity": "150"}, files=files())
    assert resp.status_code == 400 and "Количество в позиции — не больше 100 шт." in resp.text
