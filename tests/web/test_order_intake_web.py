"""Страница приёма заказа за минуту: одно поле файлов, заказчик, желаемая дата; сроки в списке заказов."""
from fastapi.testclient import TestClient

from tests.fixtures.samples import make_box_step, make_pdf


def login(app, user):
    client = TestClient(app)
    client.post("/login", data={"username": user, "password": "demo"})
    return client


def test_manager_takes_order_in_one_step_and_sees_dates(app):
    client = login(app, "manager")
    page = client.get("/orders/new")
    assert page.status_code == 200 and "Перетащите сюда все файлы заказа" in page.text
    files = [("files", ("P-1.step", make_box_step(90, 50, 20).encode())), ("files", ("P-1.pdf", make_pdf(["Bore 10H9"])))]
    resp = client.post("/orders", data={"customer": "ООО «Альфа»", "desired_date": "2030-01-15", "opened_at": "0"},
                       files=files)
    assert resp.status_code == 200 and "Желаемая дата заказчика" in resp.text and "успеваем" in resp.text
    assert "принят" in resp.text
    listing = client.get("/orders").text
    assert "ООО «Альфа»" in listing and "15.01.2030" in listing
    assert "Сроки заказов" in client.get("/dashboard").text


def test_technologist_gets_new_order_notification(app):
    login(app, "manager").post("/orders", files=[("files", ("P-2.step", make_box_step(90, 50, 20).encode())),
                                                  ("files", ("P-2.pdf", make_pdf(["Bore 10H9"])))])
    page = login(app, "technolog").get("/notifications").text
    assert "новый заказ" in page and "проверьте расчёт" in page


def test_bad_request_keeps_form_values(app):
    resp = login(app, "master").post("/orders", data={"customer": "ИП Петров"})
    assert resp.status_code == 400 and "ИП Петров" in resp.text


def test_proposal_preview_and_word_download(app):
    import re
    client = login(app, "manager")
    page = client.post("/orders", data={"customer": "ООО «Альфа»", "desired_date": "2030-01-15"},
                       files=[("files", ("P-1.step", make_box_step(90, 50, 20).encode())),
                              ("files", ("P-1.pdf", make_pdf(["Bore 10H9"])))]).text
    order_id = re.search(r'href="/orders/(\d+)/proposal"', page).group(1)
    preview = client.get(f"/orders/{order_id}/proposal")
    assert preview.status_code == 200 and "Коммерческое предложение" in preview.text
    assert "Цена операций" in preview.text and "Срок изготовления" in preview.text and "ООО «Альфа»" in preview.text
    assert f"/orders/{order_id}/proposal.docx" in preview.text  # автоматическое скачивание
    doc = client.get(f"/orders/{order_id}/proposal.docx")
    assert doc.status_code == 200 and doc.content[:2] == b"PK"
    assert "attachment" in doc.headers["content-disposition"] and ".docx" in doc.headers["content-disposition"]


def test_operator_has_no_proposal(app):
    assert login(app, "operator").get("/orders/1/proposal.docx").status_code == 403
