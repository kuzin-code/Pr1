"""Веб: выгрузка в 1С — форма на странице заказов, файл CSV/xlsx, права."""
from datetime import date

from tests.web.test_web_flow import create_order


def test_orders_page_has_export_form_for_head_and_manager(login):
    for user in ("head", "manager"):
        assert 'action="/orders/export-1c"' in login(user).get("/orders").text
    assert 'action="/orders/export-1c"' not in login("master").get("/orders").text


def test_download_csv_and_xlsx(login):
    create_order(login)
    today = date.today().isoformat()
    resp = login("head").get(f"/orders/export-1c?from={today}&to={today}")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"\xef\xbb\xbf") and "Кронштейн-1" in resp.content.decode("utf-8-sig")
    resp = login("manager").get(f"/orders/export-1c?from={today}&to={today}&fmt=xlsx")
    assert resp.status_code == 200 and resp.content[:2] == b"PK"


def test_empty_period_and_errors(login):
    head = login("head")
    text = head.get("/orders/export-1c?from=2020-01-01&to=2020-01-02").content.decode("utf-8-sig")
    assert text.count("\r\n") == 1 and text.startswith("Номер заказа;")
    assert head.get("/orders/export-1c?from=abc").status_code == 303
    assert login("master").get("/orders/export-1c").status_code == 403
