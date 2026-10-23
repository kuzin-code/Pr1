"""Технологичность по STEP в автоматическом расчёте: замечания, хранение, снятие технологом с причиной."""
import pytest

from app.domain.access import AccessDenied
from app.domain.history import ReasonRequired
from app.services import dfm_service
from app.services.history_service import list_history
from app.services.order_service import UploadedFile
from tests.fixtures.samples import make_pdf
from tests.fixtures.step_dfm import make_dfm_step
from tests.services.conftest import MASTER, TECH


def _files(step: str, name="Плита-1"):
    return [UploadedFile(f"{name}.step", step.encode()), UploadedFile(f"{name}.pdf", make_pdf(["Material: D16T"]))]


def test_deep_hole_and_small_radius_become_issues(session, make_order):
    order = make_order(_files(make_dfm_step(holes=[(20, 20, 1.5, 18)], fillets=[(5, 5, 0.4, 20)])))
    pos = order.positions[0]
    codes = {c["code"]: c["status"] for c in pos.data["dfm"]}
    assert codes == {"size": "ok", "holes": "warn", "radii": "warn"}
    issues = [i for i in pos.data["auto_issues"] if i.startswith(dfm_service.ISSUE_PREFIX)]
    assert len(issues) == 2 and any("Ø1.5 глубиной 18 мм" in i for i in issues)
    assert pos.read_data["step"]["features"]["holes"] == [[1.5, 18.0]]


def test_plain_box_ok(session, make_order):
    pos = make_order().positions[0]
    assert {c["status"] for c in pos.data["dfm"]} == {"ok"}


def test_oversize_part(session, make_order):
    pos = make_order(_files(make_dfm_step(a=900, b=100, c=50))).positions[0]
    size = next(c for c in pos.data["dfm"] if c["code"] == "size")
    assert size["status"] == "warn" and "больше рабочей зоны" in size["detail"]


def test_thresholds_from_parameter(session, make_order):
    from app.storage.orm import Parameter
    session.get(Parameter, "dfm_rules").value = {"max_hole_depth_ratio": 20, "min_internal_radius_mm": 0.1}
    session.commit()
    pos = make_order(_files(make_dfm_step(holes=[(20, 20, 1.5, 18)], fillets=[(5, 5, 0.4, 20)]))).positions[0]
    assert {c["status"] for c in pos.data["dfm"]} == {"ok"}


def test_technologist_dismisses_with_reason(session, make_order):
    pos = make_order(_files(make_dfm_step(holes=[(20, 20, 1.5, 18)]))).positions[0]
    with pytest.raises(AccessDenied):
        dfm_service.dismiss(session, MASTER, pos.id, "holes", "x")
    with pytest.raises(ReasonRequired):
        dfm_service.dismiss(session, TECH, pos.id, "holes", " ")
    with pytest.raises(ValueError, match="нет"):
        dfm_service.dismiss(session, TECH, pos.id, "size", "габарит в норме")
    dfm_service.dismiss(session, TECH, pos.id, "holes", "сверлим с выводом сверла")
    assert pos.data["dfm_dismissed"]["holes"]["reason"] == "сверлим с выводом сверла"
    assert not any(i.startswith(dfm_service.ISSUE_PREFIX) for i in pos.data["auto_issues"])
    h = [x for x in list_history(session, "position", pos.id) if x.field == "dfm_dismiss"][0]
    assert h.reason == "сверлим с выводом сверла" and "Ø1.5" in h.old
    # пересчёт не возвращает снятое предупреждение
    from app.services.auto_service import auto_prepare
    issues = auto_prepare(session, pos)
    assert not any(i.startswith(dfm_service.ISSUE_PREFIX) for i in issues)


def test_old_position_reads_features_lazily(session, make_order):
    pos = make_order(_files(make_dfm_step(holes=[(20, 20, 1.5, 18)]))).positions[0]
    step = dict(pos.read_data["step"])
    del step["features"]
    pos.read_data = {**pos.read_data, "step": step}
    assert dfm_service.features(pos).holes[0].depth == 18
    assert "features" in pos.read_data["step"]
