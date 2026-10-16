"""План, загрузка, факт, k, отклонения, уведомления (ФТ-12, ФТ-15…ФТ-23)."""
from datetime import datetime

import pytest

from app.domain.access import AccessDenied
from app.domain.enums import FactKind, PositionStatus, StageGroup
from app.domain.status import StatusError
from app.services.actor import ServiceError
from app.services import approval_service as approval
from app.services import fact_service as facts
from app.services import plan_service as plans
from app.services import reference_service as ref
from app.services import tech_service as tech
from tests.services.conftest import HEAD, IT, MASTER, OPERATOR, TECH, part_files
from tests.services.test_order_flow import prepared


def approved(session, make_order, quantity=10):
    pos = prepared(session, make_order, quantity)
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    return pos


def test_plan_deadline_and_load(session, make_order):
    pos = approved(session, make_order)
    plan = plans.current_plan(session)
    p = plan.positions[pos.id]
    assert not p.preliminary
    assert plans.order_deadline(session, pos.order_id) == p.finish
    load = plans.load_tables(session)
    assert sum(c.hours for c in load.daily["VF2"].values()) == pytest.approx(7)
    assert set(load.daily) == {"VF2"}


def test_master_sets_machine_busy_until(session, make_order):
    pos = approved(session, make_order)
    with pytest.raises(AccessDenied):
        ref.set_busy_until(session, TECH, "VF2", datetime(2026, 10, 20, 8))
    ref.set_busy_until(session, MASTER, "VF2", datetime(2026, 10, 20, 8))
    assert plans.current_plan(session).positions[pos.id].machine_start == datetime(2026, 10, 20, 8)


def test_machine_fact_updates_k_status_and_keeps_approved_calc(session, make_order):
    pos = approved(session, make_order)
    approved_calc = dict(pos.approved_calc)
    version = ref.journal_version(session)
    # возможное начало — пн 08:00; 14 рабочих часов станка = пн, вт + 2 ч в ср
    fact = facts.record_fact(session, OPERATOR, pos.id, FactKind.MACHINE, datetime(2026, 10, 14, 10, 0))
    assert fact.duration_hours == pytest.approx(14)
    assert fact.calc_hours == pytest.approx(7)
    assert pos.status == PositionStatus.BENCH.value
    entry = ref.journal_entry(session, "DEMO-02")
    assert entry.measurements[-1].ratio == pytest.approx(2.0)
    assert ref.journal_version(session) == version + 1
    assert pos.approved_calc == approved_calc  # пересчёт k не меняет утверждённую позицию

    # Новый расчёт по тому же аналогу берёт новое k.
    other = prepared(session, make_order)
    assert other.current_calc["k"]["machine"] == {"value": pytest.approx(2.0), "count": 1}


def test_bench_and_inspection_facts_by_master_finish_order(session, make_order):
    pos = approved(session, make_order)
    facts.record_fact(session, OPERATOR, pos.id, FactKind.MACHINE, datetime(2026, 10, 13, 10, 0))
    with pytest.raises(AccessDenied):
        facts.record_fact(session, OPERATOR, pos.id, FactKind.BENCH, datetime(2026, 10, 14, 10, 0))
    bench = facts.record_fact(session, MASTER, pos.id, FactKind.BENCH, datetime(2026, 10, 13, 15, 0))
    assert bench.duration_hours == pytest.approx(5)  # вт 10:00–15:00
    assert ref.journal_entry(session, "DEMO-02").measurements[-1].group is StageGroup.BENCH
    facts.record_fact(session, MASTER, pos.id, FactKind.INSPECTION, datetime(2026, 10, 14, 9, 0))
    assert pos.status == PositionStatus.DONE.value
    assert plans.order_is_done(session, pos.order_id)
    summary = facts.actual_cost(session, pos.id)
    # станок: пн 08:00 – вт 10:00 = 8 рабочих ч; слесарно-финишная — 5 ч
    assert summary["actual"] == pytest.approx(8 * 2000 + 5 * 1000)
    assert summary["calculated"] == pytest.approx(17000)


def test_fact_on_unapproved_position_rejected(session, make_order):
    pos = prepared(session, make_order)
    with pytest.raises(StatusError):
        facts.record_fact(session, OPERATOR, pos.id, FactKind.MACHINE, datetime(2026, 10, 13, 10))


def test_late_fact_shifts_next_position_and_shows_deviation(session, make_order):
    first = approved(session, make_order)
    second = approved(session, make_order)
    before = plans.current_plan(session).positions[second.id].finish
    facts.record_fact(session, OPERATOR, first.id, FactKind.MACHINE, datetime(2026, 10, 19, 12, 0))
    after = plans.current_plan(session).positions[second.id]
    assert after.machine_start == datetime(2026, 10, 19, 12, 0)
    assert after.finish > before
    devs = {d.position_id: d for d in plans.deviations(session)}
    assert devs[second.id].shift_hours > 0
    assert devs[first.id].shift_hours > 0


def test_notifications_for_master(session, make_order):
    pos = approved(session, make_order)
    finish = plans.current_plan(session).positions[pos.id].finish
    assert plans.sync_notifications(session, datetime(2026, 10, 12, 9)) == []
    created = plans.sync_notifications(session, finish)
    assert {n.kind for n in created} == {k.value for k in FactKind}
    assert plans.sync_notifications(session, finish) == []  # повторно не создаются
    assert len(plans.list_notifications(session, MASTER)) == 3
    with pytest.raises(AccessDenied):
        plans.list_notifications(session, IT)


def test_preliminary_deadline_for_unapproved(session, make_order):
    pos = prepared(session, make_order)
    assert plans.current_plan(session).positions[pos.id].preliminary


def test_snapshot_survives_k_and_rate_changes(session, make_order):
    pos = approved(session, make_order)
    snap = dict(pos.approved_calc)
    ref.add_journal_measurement(session, "DEMO-02", StageGroup.MACHINE, 1, 3)
    assert pos.approved_calc == snap
    assert snap["k"]["machine"]["count"] == 0
    assert part_files  # фикстура импортирована для prepared()


def test_fact_before_possible_start_rejected_and_k_untouched(session, make_order):
    pos = approved(session, make_order)  # заказ начинается 12.10 08:00
    with pytest.raises(ServiceError, match="раньше"):
        facts.record_fact(session, OPERATOR, pos.id, FactKind.MACHINE, datetime(2026, 10, 11, 15, 54))
    assert ref.journal_entry(session, "DEMO-02").measurements == []
    assert pos.status == PositionStatus.APPROVED.value
