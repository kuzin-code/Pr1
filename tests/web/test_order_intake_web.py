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


def test_proposal_saved_in_history_preview_and_word_download(app):
    import re
    client = login(app, "manager")
    page = client.post("/orders", data={"customer": "ООО «Альфа»", "desired_date": "2030-01-15"},
                       files=[("files", ("P-1.step", make_box_step(90, 50, 20).encode())),
                              ("files", ("P-1.pdf", make_pdf(["Bore 10H9"])))]).text
    order_id = re.search(r'action="/orders/(\d+)/proposal"', page).group(1)
    preview = client.post(f"/orders/{order_id}/proposal")
    assert preview.status_code == 200 and "Коммерческое предложение" in preview.text
    assert "Цена операций" in preview.text and "Срок изготовления" in preview.text and "ООО «Альфа»" in preview.text
    kp_id = re.search(r'/proposals/(\d+)/file', preview.text).group(1)
    assert 'iframe src="/proposals/' in preview.text  # автоматическое скачивание
    doc = client.get(f"/proposals/{kp_id}/file")
    assert doc.status_code == 200 and doc.content[:2] == b"PK"
    assert "attachment" in doc.headers["content-disposition"] and ".docx" in doc.headers["content-disposition"]
    listing = client.get("/proposals?q=Альфа").text
    assert "ООО «Альфа»" in listing and "P-1" in listing
    again = client.get(f"/orders/{order_id}").text
    assert "КП-" in again  # КП заказа видны в карточке


def test_similar_proposal_shown_in_quick_calc(app):
    import re
    client = login(app, "manager")
    page = client.post("/orders", files=[("files", ("P-7.step", make_box_step(90, 50, 20).encode())),
                                         ("files", ("P-7.pdf", make_pdf(["Bore 10H9"])))]).text
    order_id = re.search(r'action="/orders/(\d+)/proposal"', page).group(1)
    client.post(f"/orders/{order_id}/proposal")
    quick = client.post("/quick", files=[("files", ("P-7.step", make_box_step(90, 50, 20).encode())),
                                         ("files", ("P-7.pdf", make_pdf(["Bore 10H9"])))]).text
    assert "Ранее выставленные КП по этим деталям" in quick
    token = re.search(r"/quick/([0-9a-f]{32})/launch", quick).group(1)
    kp = client.post(f"/quick/{token}/proposal").text
    assert "КП-Б-" in kp


def test_operator_has_no_proposal(app):
    operator = login(app, "operator")
    assert operator.get("/proposals").status_code == 403 and operator.post("/orders/1/proposal").status_code == 403


def test_delete_calculation_from_dashboard_and_order(app):
    import re
    manager = login(app, "manager")
    page = manager.post("/orders", files=[("files", ("D-1.step", make_box_step(90, 50, 20).encode())),
                                          ("files", ("D-1.pdf", make_pdf(["Bore 10H9"]))),
                                          ("files", ("D-2.step", make_box_step(80, 40, 20).encode())),
                                          ("files", ("D-2.pdf", make_pdf(["Bore 10H9"])))]).text
    order_id = re.search(r'action="/orders/(\d+)/proposal"', page).group(1)
    tech = login(app, "technolog")
    dash = tech.get("/dashboard").text
    assert "Расчёты на проверку" in dash and "/delete" in dash
    pid = re.search(r'action="/positions/(\d+)/delete"', dash).group(1)
    page = tech.post(f"/positions/{pid}/delete", data={"reason": "дубль", "back": "/dashboard"}).text
    assert "удалён" in page
    assert "Причина" not in page
    page = tech.post(f"/orders/{order_id}/delete", data={"reason": ""}).text
    assert "причину" in page  # без причины не удаляется
    page = tech.post(f"/orders/{order_id}/delete", data={"reason": "заказчик отказался"}).text
    assert "удалён" in page and "Заказов нет" in tech.get("/orders").text
