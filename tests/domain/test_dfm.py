"""Проверка технологичности: габарит и рабочая зона, глубина отверстий, внутренние радиусы, «не определено»."""
import json
from pathlib import Path

from app.domain import dfm
from app.domain.dfm import DfmRules, DfmStatus as St, Hole, StepFeatures
from app.domain.models import Dimensions, Machine

RULES = DfmRules(10, 1)
SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "dfm_rules.json"


def m(code, zone, tech="milling", cnc=True):
    return Machine(code, code, "", cnc, Dimensions(*zone) if zone else None, frozenset({tech}))


MACHINES = [m("VF2", (762, 406, 508)), m("SMM", (406, 305, 254)), m("ST20Y", (300, 300, 500), "turning")]


def test_rules_from_seed_and_defaults():
    data = json.loads(SEED.read_text(encoding="utf-8"))
    assert DfmRules.from_dict(data) == DfmRules(data["max_hole_depth_ratio"], data["min_internal_radius_mm"])
    assert DfmRules.from_dict(None) == DfmRules(10.0, 1.0)


def test_size_fits_or_warns():
    assert dfm.check_size(Dimensions(500, 300, 100), "milling", MACHINES).status is St.OK
    too_big = dfm.check_size(Dimensions(800, 300, 100), "milling", MACHINES)
    assert too_big.status is St.WARN and "VF2 762×406×508" in too_big.detail and "ST20Y" not in too_big.detail
    assert dfm.check_size(None, "milling", MACHINES).status is St.UNKNOWN
    assert dfm.check_size(Dimensions(1, 1, 1), "grinding", MACHINES).status is St.UNKNOWN


def test_size_unknown_when_zone_not_set():
    check = dfm.check_size(Dimensions(800, 300, 100), "milling", MACHINES + [m("V6", None)])
    assert check.status is St.UNKNOWN and "V6" in check.detail


def test_holes():
    ok = dfm.check_holes(StepFeatures([Hole(5, 20)]), RULES)
    assert ok.status is St.OK and "4.0" in ok.detail
    deep = dfm.check_holes(StepFeatures([Hole(3, 45), Hole(2, 30), Hole(5, 10)]), RULES)
    assert deep.status is St.WARN and "Ø3 глубиной 45 мм (15.0)" in deep.detail and "Ø5" not in deep.detail
    assert dfm.check_holes(StepFeatures(), RULES).status is St.OK
    assert dfm.check_holes(None, RULES).status is St.UNKNOWN


def test_radii():
    assert dfm.check_radii(StepFeatures(internal_radii=[2, 3]), RULES).status is St.OK
    small = dfm.check_radii(StepFeatures(internal_radii=[0.5, 0.5, 3]), RULES)
    assert small.status is St.WARN and "R0.5" in small.detail and "граней: 2" in small.detail
    assert dfm.check_radii(None, RULES).status is St.UNKNOWN


def test_dismissed_until_detail_changes():
    checks = dfm.check_all(Dimensions(800, 300, 100), "milling", MACHINES, StepFeatures([Hole(3, 45)]), RULES)
    assert [c.code for c in dfm.active_warnings(checks, {})] == ["size", "holes"]
    dismissed = {"holes": {"detail": checks[1].detail, "reason": "сверлим с выводом"}}
    assert [c.code for c in dfm.active_warnings(checks, dismissed)] == ["size"]
    changed = dfm.check_all(Dimensions(800, 300, 100), "milling", MACHINES, StepFeatures([Hole(2, 45)]), RULES)
    assert [c.code for c in dfm.active_warnings(changed, dismissed)] == ["size", "holes"]
    assert dfm.DfmCheck.from_dict(checks[0].to_dict()) == checks[0]
