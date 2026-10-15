"""Расчёт по данным чертежа ЕСКД: внешние операции из ТТ, та же деталь из журнала, сверка обозначения."""
import pytest

from app.domain.enums import Mark
from app.parsers.pdf_reader import parse_drawing_text
from app.services import journal_service, order_service
from app.services import tech_service as tech
from tests.parsers.test_eskd import KORPUS, STEEL
from tests.services.conftest import TECH, part_files


@pytest.fixture
def drawing(monkeypatch):
    """Подменяет текстовый слой PDF: в тестовом генераторе нет кириллицы."""
    def use(text):
        monkeypatch.setattr(order_service, "read_pdf", lambda path, known=(): parse_drawing_text(text, known))
    return use


def test_drawing_fields_saved_and_shown_to_technologist(session, make_order, drawing):
    drawing(KORPUS)
    pos = make_order(part_files("КПУ.741645.001 Корпус", dims=(84, 67, 26))).positions[0]
    d = pos.data["drawing"]
    assert (d["designation"], d["name"], d["scale"], d["sheet_format"]) == ("КПУ.741645.001", "Корпус", "1:1", "А3")
    assert d["thread_count"] == 6 and d["has_form_tolerances"] and d["ra_min"] == 2.5
    assert pos.data["material"] == "Полиацеталь ПОМ-С белый" and pos.data["tolerance_grade"] == 9
    assert pos.complexity == 4  # IT9 + резьбы и допуск расположения (+1)


def test_external_operations_from_requirements_mark_cooperation(session, make_order, drawing):
    drawing(STEEL)
    pos = make_order(part_files("АБВГ.715411.003 Вал", dims=(80, 30, 30))).positions[0]
    assert pos.cooperation
    assert Mark.NO_COOPERATION.value in pos.current_calc["marks"]
    issues = " ".join(pos.data["auto_issues"])
    assert "Хим.Окс.прм" in issues and "HRC" in issues
    assert "круг" in issues  # заготовка — круг: проверить технологию


def test_manual_cooperation_decision_kept(session, make_order, drawing):
    drawing(STEEL)
    pos = make_order(part_files("АБВГ.715411.003 Вал", dims=(80, 30, 30))).positions[0]
    tech.set_cooperation(session, TECH, pos.id, False)
    tech.auto_recalculate(session, TECH, pos.id)
    assert not pos.cooperation


def test_designation_mismatch_warned(session, make_order, drawing):
    drawing(KORPUS)
    pos = make_order(part_files("Совсем другая деталь")).positions[0]
    assert any("КПУ.741645.001" in i and "не совпадает" in i for i in pos.data["auto_issues"])


def test_same_part_from_journal_used_as_analog(session, make_order, drawing):
    drawing(KORPUS)
    first = make_order(part_files("КПУ.741645.001 Корпус", dims=(84, 67, 26))).positions[0]
    journal_service.add_to_journal(session, TECH, first.id)
    again = make_order(part_files("КПУ.741645.001 Корпус (1)", dims=(84, 67, 26))).positions[0]
    assert again.analog_code == "КПУ.741645.001 Корпус"
    assert again.data["analog_approximate"] is False
