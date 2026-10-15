"""Веб: файлы детали журнала и позиции открываются по кнопке; 3D-просмотр STEP без внешних ресурсов."""
import re

from tests.fixtures.samples import make_box_step, make_pdf


def journal_entry(login, name="КПУ.741645.001 Корпус"):
    files = [("files", (f"{name}.stp", make_box_step(84, 76, 26).encode(), "application/octet-stream")),
             ("files", (f"{name}.pdf", make_pdf(["KPU.741645.001", "Material: POM"]), "application/pdf"))]
    return login("technolog").post("/journal/new", files=files).headers["location"]


def test_journal_list_shows_buttons_and_missing_files(login):
    url = journal_entry(login)
    page = login("technolog").get("/journal").text
    assert f'href="{url}/file/pdf"' in page and f'href="{url}/model"' in page
    assert "Открыть чертёж" in page and "3D-модель" in page
    assert "нет чертежа PDF" in page  # у демо-деталей файлов нет
    assert "без исправных файлов: 9" in page


def test_entry_card_buttons_and_viewer(login):
    url = journal_entry(login)
    tech = login("technolog")
    card = tech.get(url).text
    assert "Открыть чертёж (PDF)" in card and "Открыть 3D-модель (STEP)" in card and "Скачать STEP" in card
    viewer = tech.get(f"{url}/model").text
    assert f"{url}/file/step" in viewer
    assert "/static/vendor/occt-import-js.js" in viewer and "/static/vendor/three.module.min.js" in viewer
    assert not re.search(r"https?://", viewer)  # всё грузится с сервера предприятия (НФТ-01)


def test_vendor_assets_served(app):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    wasm = client.get("/static/vendor/occt-import-js.wasm")
    assert wasm.status_code == 200 and wasm.headers["content-type"] == "application/wasm"
    assert client.get("/static/vendor/three.module.min.js").status_code == 200


def test_position_model_viewer(login):
    files = [("files", ("Деталь-1.stp", make_box_step().encode(), "application/octet-stream")),
             ("files", ("Деталь-1.pdf", make_pdf(["Material: D16T"]), "application/pdf"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-19"}, files=files)
    pid = re.search(r"/positions/(\d+)", login("master").get(resp.headers["location"]).text).group(1)
    page = login("technolog").get(f"/positions/{pid}").text
    assert f"/positions/{pid}/model" in page
    assert f"/positions/{pid}/file/step" in login("technolog").get(f"/positions/{pid}/model").text
    assert login("operator").get(f"/positions/{pid}/model").status_code == 403


def test_attach_warning_flashed(login):
    url = journal_entry(login)
    tech = login("technolog")
    files = [("files", ("Чужая-деталь.pdf", make_pdf(["KPU.999999.001"]), "application/pdf"))]
    tech.post(f"{url}/files", files=files)
    assert "не совпадает" in tech.get(url).text
