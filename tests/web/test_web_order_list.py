"""Веб: новый заказ списком деталей, шаблон списка, прикрепление файлов к позиции."""
import re

from tests.fixtures.samples import make_box_step, make_order_list, make_pdf

ITEMS = [("MBIS.AI.18000.01", "Основание", 12, "Д16Т ГОСТ 4784-2019"),
         ("MBIS.AI.18000.05", "Кронштейн", 6, "Д16Т ГОСТ 4784-2019", "Анодирование")]


def test_template_download(login):
    resp = login("master").get("/orders/template")
    assert resp.status_code == 200 and resp.content[:2] == b"PK"


def test_order_from_list_and_attach_files_later(login, tmp_path):
    path = make_order_list(tmp_path / "list.xlsx", ITEMS, sections={1: "Детали из алюминия"})
    files = [("list_file", ("Заказ.xlsx", path.read_bytes(), "application/octet-stream")),
             ("files", ("MBIS.AI.18000.01 Основание.step", make_box_step(150, 60, 40).encode(), "application/octet-stream")),
             ("files", ("MBIS.AI.18000.01 Основание.pdf", make_pdf(["Bore 10H9"]), "application/pdf"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-19"}, files=files)
    assert resp.status_code == 303, resp.text
    page = login("master").get(resp.headers["location"]).text
    assert "ООО «Тест»" in page and "Детали из алюминия" in page and "Анодирование" in page
    pids = [int(x) for x in dict.fromkeys(re.findall(r"/positions/(\d+)", page))]
    assert len(pids) == 2
    tech = login("technolog")
    assert "Прикрепить файлы" in tech.get(f"/positions/{pids[1]}").text
    upload = [("files", ("MBIS.AI.18000.05.step", make_box_step(100, 50, 20).encode(), "application/octet-stream")),
              ("files", ("MBIS.AI.18000.05.pdf", make_pdf(["Bore 8H9"]), "application/pdf"))]
    assert tech.post(f"/positions/{pids[1]}/files", files=upload).status_code == 303
    assert "Проверка технолога" in tech.get(f"/positions/{pids[1]}").text
    assert login("operator").post(f"/positions/{pids[1]}/files", files=upload).status_code == 403


def test_list_without_files_allowed_and_bad_list_reported(login, tmp_path):
    path = make_order_list(tmp_path / "list.xlsx", ITEMS[:1])
    resp = login("master").post("/orders", data={"start_date": "2026-10-19"},
                                files=[("list_file", ("Заказ.xlsx", path.read_bytes(), "application/octet-stream"))])
    assert resp.status_code == 303
    bad = make_order_list(tmp_path / "bad.xlsx", [("A-1", "Деталь", "много", "Д16Т")])
    resp = login("master").post("/orders", data={"start_date": "2026-10-19"},
                                files=[("list_file", ("bad.xlsx", bad.read_bytes(), "application/octet-stream"))])
    assert resp.status_code == 400 and "количество" in resp.text
