"""Цена заготовок: загрузка прайса в справочник с историей, цена позиции, цена технолога, марки, КП без двойного НДС."""
import pytest

from app.domain.access import AccessDenied
from app.domain.history import ReasonRequired
from app.services import blank_price_service as prices
from app.services import intake_service as intake
from app.services import proposal_history as history
from app.services import proposal_service as kp
from app.services import quick_service as quick
from app.services.actor import ServiceError
from app.services.history_service import list_history
from app.storage.orm import BlankPriceRow, PositionRow, ProposalRow
from tests.fixtures.price_list import price_xlsx
from tests.services.conftest import HEAD, IT, MON, TECH, part_files
from tests.services.test_order_flow import prepared
from tests.services.test_quick_intake import MANAGER

PLATE = 3290  # плита Д16Т 50×100×200 мм (код 114102) — заготовка h50 158×64 мм помещается


@pytest.fixture
def loaded(session):
    return prices.load_price(session, IT, price_xlsx(), "price.xlsx", "первичная загрузка")


def test_load_price_replaces_old_and_goes_to_history(session, loaded):
    assert loaded["date"] == "2026-10-26" and loaded["vat_percent"] == 22 and loaded["count"] == 10
    meta = prices.load_price(session, TECH, price_xlsx(rows=[(1, "Заготовка плита алюминиевая Д16Т 50 х 100 х 200 мм",
                                                             3000, 1)]), "price.xlsx", "новый прайс")
    assert meta["count"] == 1 and session.query(BlankPriceRow).count() == 1  # старый заменён целиком
    rows = [h for h in list_history(session, entity="parameters") if h.field == "blank_price_list"]
    assert [r.reason for r in rows] == ["новый прайс", "первичная загрузка"]
    assert rows[0].old["count"] == 10 and rows[0].new["count"] == 1


def test_load_price_rights_reason_and_format(session):
    with pytest.raises(AccessDenied):
        prices.load_price(session, HEAD, price_xlsx(), "price.xlsx", "причина")
    with pytest.raises(AccessDenied):
        prices.load_price(session, MANAGER, price_xlsx(), "price.xlsx", "причина")
    with pytest.raises(ReasonRequired):
        prices.load_price(session, IT, price_xlsx(), "price.xlsx", " ")
    with pytest.raises(ServiceError, match="Прайс не загружен: Файл не читается"):
        prices.load_price(session, IT, b"garbage", "price.xls", "причина")
    assert prices.price_meta(session) is None


def test_position_cost_by_price(session, make_order, loaded):
    pos = prepared(session, make_order)  # Д16Т, лист h50 158×64 мм — 10 шт.
    cost = prices.position_cost(session, pos)
    assert cost.status == "price" and cost.piece.code == "114102" and cost.total == PLATE * 13
    assert cost.price_date == "2026-10-26" and cost.vat_percent == 22


def test_position_cost_without_price_and_old_position(session, make_order):
    pos = make_order().positions[0]
    pos.data = {k: v for k, v in pos.data.items() if k != "dims"}
    cost = prices.position_cost(session, pos)
    assert cost.status == "none" and cost.total is None


def test_manual_price_with_reason_history_and_reset(session, make_order, loaded):
    pos = prepared(session, make_order)
    with pytest.raises(AccessDenied):
        prices.set_manual_price(session, HEAD, pos.id, "500", "", "причина")
    with pytest.raises(ReasonRequired):
        prices.set_manual_price(session, TECH, pos.id, "500", "", "")
    with pytest.raises(ServiceError, match="больше нуля"):
        prices.set_manual_price(session, TECH, pos.id, "0", "", "причина")
    cost = prices.set_manual_price(session, TECH, pos.id, "2 500,5", "счёт поставщика", "дешевле у другого")
    assert cost.status == "manual" and cost.total == pytest.approx(2500.5 * 13) and cost.note == "счёт поставщика"
    (row,) = [h for h in list_history(session, entity="position", entity_id=pos.id) if h.field == "blank_price"]
    assert row.reason == "дешевле у другого" and "код 114102" in row.old and "задано технологом" in row.new
    back = prices.reset_manual_price(session, TECH, pos.id, "вернуть прайс")
    assert back.status == "price" and back.total == PLATE * 13


def test_rules_edited_with_reason(session, make_order, loaded):
    pos = prepared(session, make_order)
    text = "Д16Т = Д16Т\nАМг6 = АМг6"
    with pytest.raises(AccessDenied):
        prices.set_rules(session, HEAD, text, "0", "22", "причина")
    with pytest.raises(ServiceError, match="строка 1"):
        prices.set_rules(session, TECH, "Д16Т", "3", "22", "причина")
    prices.set_rules(session, TECH, text, "0", "22", "брак не закладываем")
    assert prices.position_cost(session, pos).total == PLATE * 10
    (row,) = [h for h in list_history(session, entity="parameters") if h.field == "blank_price_rules"]
    assert row.reason == "брак не закладываем" and row.old["scrap_pieces"] == 3 and row.new["scrap_pieces"] == 0
    prices.set_rules(session, TECH, "AISI 304 = AISI 304", "3", "22", "только нержавейка")
    assert "марки нет в прайсе" in prices.position_cost(session, pos).reason


@pytest.fixture
def order(session, tmp_path):
    return intake.quick_intake(session, MANAGER, part_files("Кронштейн-1"), tmp_path / "up", start_date=MON,
                               quantity=4)


def test_proposal_blanks_net_of_vat_no_double_vat(session, order, loaded):
    p = kp.build_proposal(session, MANAGER, order.id, quantities=[1, 4])
    (item,) = p.items
    net = PLATE / 1.22
    assert item.blank_pieces == 7 and item.blank_unit == pytest.approx(net)
    assert item.blank_cost == pytest.approx(net * 7) and "плита алюминиевая Д16Т 50×100×200 мм" in item.blank_text
    assert item.total_price == pytest.approx(item.batch_price + net * 7)
    assert item.price == pytest.approx(item.total_price / 4)
    # НДС КП начисляется один раз — на итог без НДС, куда заготовки вошли без НДС прайса
    assert p.total == pytest.approx(item.total_price) and p.grand_total == pytest.approx(p.total * 1.2)
    assert p.blank_label == "Заготовки (материал по прайсу prutki.ru от 26.10.2026)"
    assert "26.10.2026" in p.price_note and "Доставка заготовок" in p.price_note
    one, four = item.breaks
    assert one["blank_cost"] == pytest.approx(net * 4) and four["batch_price"] == pytest.approx(item.total_price)


def test_proposal_without_price_unchanged(session, order):
    p = kp.build_proposal(session, MANAGER, order.id)
    assert p.items[0].blank_cost is None and p.price_note == kp.PRICE_NOTE and not p.has_blanks
    assert p.total == pytest.approx(p.items[0].batch_price)


def test_proposal_note_when_blank_not_found(session, order, loaded):
    pos = order.positions[0]
    pos.data = {**pos.data, "material": "Поликарбонат"}
    session.commit()
    p = kp.build_proposal(session, MANAGER, order.id)
    assert p.items[0].blank_cost is None and "цена заготовки не определена" in " ".join(p.notes)


def test_saved_proposal_and_word_contain_blanks(session, order, loaded, tmp_path):
    from docx import Document
    row = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    p = history.proposal_of(row)
    assert p.items[0].blank_cost == pytest.approx(PLATE / 1.22 * 7) and row.grand_total == pytest.approx(p.grand_total)
    text = "\n".join(c.text for t in Document(row.file_path).tables for r in t.rows for c in r.cells)
    assert "Заготовки (материал по прайсу prutki.ru от 26.10.2026)" in text and "7 шт." in text


def test_old_proposal_without_blank_fields_opens(session, order, tmp_path):
    row = history.create_from_order(session, MANAGER, order.id, tmp_path / "kp")
    data = dict(row.data)
    data.pop("blank_price_date")
    data["items"] = [{k: v for k, v in i.items() if not k.startswith("blank")} for i in data["items"]]
    old = ProposalRow(number="КП-старое", created_at=row.created_at, created_by="manager", source="order",
                      customer="", designations=row.designations, grand_total=row.grand_total, data=data)
    session.add(old)
    session.commit()
    p = history.proposal_of(session.get(ProposalRow, old.id))
    assert p.items[0].blank_cost is None and p.grand_total == pytest.approx(row.grand_total)


def test_quick_calc_and_its_proposal_include_blanks(session, loaded, tmp_path):
    result = quick.calculate(session, MANAGER, part_files("Кронштейн-1"), tmp_path / "work", quantity=10,
                             start_date=MON)
    (item,) = result.summary["items"]
    assert item["blank_pieces"] == 13 and item["blank_cost"] == pytest.approx(PLATE * 13 / 1.22)
    assert result.summary["total"] == pytest.approx(item["batch_price"] + item["blank_cost"])
    assert result.summary["has_blanks"] and "26.10.2026" in result.summary["blank_label"]
    row = history.create_from_quick(session, MANAGER, tmp_path / "work", result.token, tmp_path / "kp")
    p = history.proposal_of(row)
    assert p.items[0].blank_cost == pytest.approx(item["blank_cost"])
    assert p.grand_total == pytest.approx(result.summary["grand_total"]) and "26.10.2026" in p.price_note


def test_blank_price_kept_out_of_machining_cost(session, make_order, loaded):
    pos = prepared(session, make_order)
    calc = session.get(PositionRow, pos.id).current_calc
    assert calc["cost"]["batch_cost"] == pytest.approx(calc["cost"]["machine_hours"] * 2000
                                                      + calc["cost"]["bench_hours"] * 1000)


def test_user_example_zaglushka_on_position(session, make_order):
    """МБЦР.107.070.020-01 Заглушка 35,7×23,2×5, AW 6061, 40 шт.: лист h8 39×27 — из плиты 8×100×300 22 шт."""
    from app.services import blank_service, order_service
    prices.load_price(session, IT, price_xlsx(rows=[
        (1, "Заготовка плита алюминиевая Д16Т 8 х 100 х 300 мм", 1000, 5),
        (2, "Заготовка лист алюминиевый Д16Т 8 х 500 х 1000 мм", 20000, 5)]), "price.xlsx", "пример")
    order = make_order(part_files("Заглушка", dims=(35.7, 23.2, 5), pdf_lines=("Material: AW 6061",)))
    pos = order.positions[0]
    order_service.set_quantity(session, TECH, pos.id, 40)
    assert blank_service.position_blank(session, pos).text == "AW 6061, лист h8 мм, 39×27 мм — 40 шт."
    cost = prices.position_cost(session, pos)
    assert (cost.piece.code, cost.per_piece, cost.pieces, cost.total) == ("1", 22, 2, 2000)
    assert cost.cut_text == "из куска 22 заготовки, нужно 2 куска на 40 + 3 шт." and cost.warning == ""
