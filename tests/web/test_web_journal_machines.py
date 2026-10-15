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
    assert login("technolog").get("/machines/MV6/edit").status_code == 403


def test_it_adds_machine(login):
    it = login("it")
    assert it.get("/machines/new").status_code == 200
    resp = it.post("/machines", data={"code": "DMU50", "model": "DMG Mori DMU 50", "kind": "5-осевой",
                                      "is_cnc": "on", "technologies": "milling", "work_zone": "650x520x475"})
    assert resp.status_code == 303
    assert "DMG Mori DMU 50" in it.get("/reference").text
