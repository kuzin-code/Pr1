"""Веб: ведение журнала калибровки и справочника оборудования."""
import re

from tests.fixtures.samples import make_box_step, make_pdf

STAGES = ["cutting", "programming", "setup", "machining", "locksmith", "finishing", "inspection"]


def _files(name="КПУ.200.001 Планка"):
    return [("files", (f"{name}.stp", make_box_step(90, 40, 12).encode(), "application/octet-stream")),
            ("files", (f"{name}.pdf", make_pdf(["Material: Poliacetal POM-S", "Bore 6H9"]), "application/pdf"))]


def new_entry(login):
    resp = login("technolog").post("/journal/new", files=_files())
    assert resp.status_code == 303, resp.text
    return resp.headers["location"]


def test_technologist_creates_entry_from_files_and_edits_times(login):
    url = new_entry(login)
    assert re.fullmatch(r"/journal/\d+", url)
    tech = login("technolog")
    page = tech.get(url).text
    assert "КПУ.200.001 Планка" in page and "Poliacetal POM-S" in page
    form = {f"hours_{s}": "0.5" for s in STAGES} | {f"mode_{s}": "piece" for s in STAGES}
    form |= {"hours_finishing": "", "hours_machining": "0.75", "reason": "хронометраж"}
    assert tech.post(f"{url}/stages", data=form).status_code == 303
    page = tech.get(url).text
    assert "0.75" in page
    assert tech.post(f"{url}/measurement", data={"group": "machine", "calc_hours": "2", "actual_hours": "2.4"}).status_code == 303
    assert "1.200" in tech.get(url).text  # k = 2.4 / 2
    assert tech.get(f"{url}/file/pdf").content.startswith(b"%PDF")


def test_edit_characteristics_and_delete(login):
    url = new_entry(login)
    tech = login("technolog")
    data = {"part_code": "КПУ.200.001 Планка", "name": "Планка", "machine_code": "SMM", "material": "Капролон",
            "dim_a": "90", "dim_b": "40", "dim_c": "12", "tolerance_grade": "8", "complexity": "2",
            "technology": "milling", "reason": "по чертежу"}
    assert tech.post(f"{url}/update", data=data).status_code == 303
    assert "Капролон" in tech.get(url).text
    assert tech.post(f"{url}/delete", data={"reason": "дубль"}).headers["location"] == "/journal"
    assert "КПУ.200.001" not in tech.get("/journal").text


def test_journal_editing_forbidden_for_other_roles(login):
    url = new_entry(login)
    assert login("head").get(url).status_code == 200  # просмотр
    assert login("head").post(f"{url}/delete").status_code == 403
    assert login("it").post("/journal/new", files=_files("X")).status_code == 403


def test_reference_shows_gaps_and_it_edits_machine(login):
    page = login("it").get("/reference").text
    assert "Пробелы" in page and "не заполнено" in page
    it = login("it")
    assert it.get("/machines/MV6/edit").status_code == 200
    resp = it.post("/machines/MV6", data={"model": "Mintech V6", "kind": "Вертикально-фрезерный с ЧПУ",
                                          "work_zone": "650 x 420 x 450", "technologies": "milling",
                                          "controller": "Fanuc", "accuracy": "0,005", "power": "11 кВт",
                                          "axes": "3", "spindle_rpm": "10000", "tool_count": "24",
                                          "max_weight_kg": "350", "table_size": "800x420",
                                          "inventory_number": "42", "hours_per_day": "6", "note": "",
                                          "is_cnc": "on", "reason": "паспорт"})
    assert resp.status_code == 303
    assert "650 × 420 × 450" in it.get("/reference").text
    assert login("technolog").get("/machines/MV6/edit").status_code == 200
    assert login("head").get("/machines/MV6/edit").status_code == 403
    assert 'action="/rates"' not in login("technolog").get("/reference").text  # ставки меняет только IT


def test_it_adds_machine(login):
    it = login("it")
    assert it.get("/machines/new").status_code == 200
    resp = it.post("/machines", data={"code": "DMU50", "model": "DMG Mori DMU 50", "kind": "5-осевой",
                                      "is_cnc": "on", "technologies": "milling", "work_zone": "650x520x475"})
    assert resp.status_code == 303
    assert "DMG Mori DMU 50" in it.get("/reference").text


def test_operations_checkboxes_in_machine_form(login):
    tech = login("technolog")
    form = tech.get("/machines/BS275/edit").text
    assert 'name="operations" value="cutting"' in form and "Резка заготовки" in form
    data = {"model": "Holzmann BS275TOP", "kind": "Ленточнопильный", "technologies": "cutting",
            "hours_per_day": "8", "operations": ["cutting"], "reason": "паспорт"}
    assert tech.post("/machines/BS275", data=data).status_code == 303
    assert "Резка заготовки" in tech.get("/reference").text


def test_journal_template_and_import_with_files(login, tmp_path):
    from app.parsers.journal_excel import export_journal
    from tests.domain.conftest import make_entry
    tech = login("technolog")
    assert tech.get("/journal/template").content[:2] == b"PK"
    xlsx = tmp_path / "j.xlsx"
    export_journal([make_entry("KPU.300.001")], xlsx)
    files = [("file", ("j.xlsx", xlsx.read_bytes(), "application/octet-stream")),
             ("files", ("KPU.300.001 Ось.stp", make_box_step(60, 20, 20).encode(), "application/octet-stream")),
             ("files", ("KPU.300.001 Ось.pdf", make_pdf(["KPU.300.001"]), "application/pdf"))]
    assert tech.post("/journal/import", files=files).status_code == 303
    page = tech.get("/journal").text
    assert "деталей 1, файлов прикреплено 2" in page and "KPU.300.001" in page


def test_import_folder_button_and_confirmation(app, login, tmp_path):
    kd = tmp_path / "КД"
    kd.mkdir()
    (kd / "KPU.600.001 Скоба.stp").write_text(make_box_step(80, 40, 10))
    (kd / "KPU.600.001 Скоба.pdf").write_bytes(make_pdf(["KPU.600.001", "Material: D16T"]))
    app.state.settings.drawings_dir = kd
    tech = login("technolog")
    assert tech.post("/journal/import-folder").status_code == 303
    page = tech.get("/journal").text
    assert "новых деталей 1" in page and "время не подтверждено" in page
    import re
    entry = re.search(r'href="(/journal/\d+)"><b>KPU.600.001 Скоба', page).group(1)
    assert "Время проверено" in tech.get(entry).text
    tech.post(f"{entry}/confirm")
    assert "время не подтверждено" not in tech.get("/journal").text
    assert login("head").post("/journal/import-folder").status_code == 403


def test_second_machine_in_entry_card(login):
    """Технолог добавляет, меняет и убирает второй станок техпроцесса детали — с причиной."""
    url = new_entry(login)
    tech = login("technolog")
    assert "Второй станок техпроцесса" in tech.get(url).text
    resp = tech.post(f"{url}/extra-machine", data={"code": "SMM", "setup": "0,7", "machining": "0,2", "reason": ""})
    assert resp.status_code == 303 and not re.search(r'name="setup" value="0\.7"', tech.get(url).text)  # без причины
    resp = tech.post(f"{url}/extra-machine", data={"code": "SMM", "setup": "0,7", "machining": "0,2",
                                                   "reason": "по техпроцессу"})
    assert resp.status_code == 303
    page = tech.get(url).text
    assert re.search(r'name="setup" value="0\.7"', page) and re.search(r'name="machining" value="0\.2"', page)
    tech.post(f"{url}/extra-machine", data={"code": "", "setup": "", "machining": "", "reason": "не нужен"})
    assert not re.search(r'name="setup" value="0\.7"', tech.get(url).text)
    resp = login("head").post(f"{url}/extra-machine", data={"code": "SMM", "setup": "1", "machining": "1",
                                                            "reason": "x"})
    assert resp.status_code == 403
