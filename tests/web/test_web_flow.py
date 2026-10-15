"""Сквозной сценарий через веб: заказ → расчёт → утверждение → факт → завершение."""
import re

from tests.fixtures.samples import make_box_step, make_pdf


def _files(name="Кронштейн-1"):
    return [
        ("files", (f"{name}.step", make_box_step(152, 58, 40).encode(), "application/octet-stream")),
        ("files", (f"{name}.pdf", make_pdf(["Material: D16T", "Bore 10H9"]), "application/pdf")),
    ]


def create_order(login, name="Кронштейн-1"):
    resp = login("master").post("/orders", data={"start_date": "2026-10-12"}, files=_files(name))
    assert resp.status_code == 303, resp.text
    order_url = resp.headers["location"]
    page = login("master").get(order_url).text
    position_id = int(re.search(r"/positions/(\d+)", page).group(1))
    return order_url, position_id


def prepare(login, pid):
    tech = login("technolog")
    ok = lambda r: r.status_code == 303 and "error" not in r.headers.get("location", "")  # noqa: E731
    assert ok(login("master").post(f"/positions/{pid}/quantity", data={"quantity": "10"}))
    assert ok(tech.post(f"/positions/{pid}/data", data={
        "dim_a": "152", "dim_b": "58", "dim_c": "40", "volume": "", "material": "Д16Т",
        "tolerance_grade": "9", "threads": "", "technology": "milling", "reason": "по штампу"}))
    assert ok(tech.post(f"/positions/{pid}/complexity", data={"complexity": "2"}))
    assert ok(tech.post(f"/positions/{pid}/analog", data={"code": "DEMO-02"}))
    assert ok(tech.post(f"/positions/{pid}/machine", data={"code": "VF2"}))
    assert ok(tech.post(f"/positions/{pid}/calculate"))


def test_rejected_format_shows_message(login):
    files = [("files", ("model.sldprt", b"x", "application/octet-stream"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-12"}, files=files)
    assert resp.status_code == 400
    assert "только форматы STEP" in resp.text


def test_full_flow(login):
    order_url, pid = create_order(login)
    prepare(login, pid)

    tech_page = login("technolog").get(f"/positions/{pid}").text
    assert "DEMO-02" in tech_page and "ориентировочно" in tech_page
    assert "17 000" not in tech_page  # себестоимость технологу не показывается
    head_page = login("head").get(f"/positions/{pid}").text
    assert "17 000" in head_page

    assert login("technolog").post(f"/positions/{pid}/submit").status_code == 303
    assert login("head").post(f"/positions/{pid}/approve").status_code == 303
    assert "Утверждена" in login("head").get(f"/positions/{pid}").text

    plan = login("management").get("/plan").text
    assert "Haas VF-2" in plan and "%" in plan

    # Все операции позиции по порядку: мастер может отметить за любого специалиста.
    master = login("master")
    times = ["2026-10-12T08:00", "2026-10-12T09:00", "2026-10-12T10:00", "2026-10-13T10:00",
             "2026-10-13T12:00", "2026-10-13T13:00", "2026-10-14T09:00"]
    for i in range(6):
        ready = re.findall(r'action="/operations/(\d+)/start"', master.get("/work").text)
        assert master.post(f"/operations/{ready[0]}/start", data={"at": times[i]}).status_code == 303
        assert master.post(f"/operations/{ready[0]}/finish", data={"at": times[i + 1]}).status_code == 303
    order_page = master.get(order_url).text
    assert "Заказ завершён" in order_page

    journal = login("technolog").get("/journal").text
    assert "DEMO-02" in journal
    history = login("head").get("/history").text
    assert "по штампу" in history


def test_reason_required_error_flashed(login):
    _, pid = create_order(login)
    resp = login("technolog").post(f"/positions/{pid}/data", data={
        "dim_a": "150", "dim_b": "58", "dim_c": "40", "material": "Д16Т", "tolerance_grade": "9",
        "threads": "", "technology": "milling", "reason": ""})
    assert resp.status_code == 303
    page = login("technolog").get(f"/positions/{pid}").text
    assert "указать причину" in page


def test_open_drawing_file(login):
    _, pid = create_order(login)
    resp = login("technolog").get(f"/positions/{pid}/file/pdf")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert login("operator").get(f"/positions/{pid}/file/pdf").status_code == 403


def test_master_sets_busy_until_and_sees_notifications(login):
    resp = login("master").post("/machines/VF2/busy", data={"busy_until": "2026-10-20T08:00"})
    assert resp.status_code == 303
    assert "20.10.2026" in login("master").get("/plan").text
    assert login("master").get("/notifications").status_code == 200


def test_journal_export_and_import(login, tmp_path):
    resp = login("it").get("/journal/export")
    assert resp.status_code == 200
    content = resp.content
    up = login("it").post("/journal/import", files={"file": ("j.xlsx", content, "application/octet-stream")})
    assert up.status_code == 303
    assert "DEMO-09" in login("it").get("/journal").text
