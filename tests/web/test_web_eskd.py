"""Веб: карточка позиции показывает данные чертежа по ЕСКД."""
import re

from app.parsers.pdf_reader import parse_drawing_text
from app.services import order_service
from tests.fixtures.samples import make_box_step, make_pdf
from tests.parsers.test_eskd import STEEL


def test_position_card_shows_eskd_block(login, monkeypatch):
    monkeypatch.setattr(order_service, "read_pdf", lambda path, known=(): parse_drawing_text(STEEL, known))
    files = [("files", ("АБВГ.715411.003 Вал.step", make_box_step(80, 30, 30).encode(), "application/octet-stream")),
             ("files", ("АБВГ.715411.003 Вал.pdf", make_pdf(["x"]), "application/pdf"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-19"}, files=files)
    pid = re.search(r"/positions/(\d+)", login("master").get(resp.headers["location"]).text).group(1)
    page = login("technolog").get(f"/positions/{pid}").text
    assert "Чертёж по ЕСКД" in page
    for text in ("АБВГ.715411.003", "Круг 30-В ГОСТ 2590-2006", "2:1", "Ra 0,8", "Хим.Окс.прм", "35...40 HRC",
                 "Общие допуски по ГОСТ 30893.1-m"):
        assert text in page, text
