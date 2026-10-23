"""Порядок очереди с ручным приоритетом: утверждённые по приоритету, начатые не вытесняются."""
from datetime import date

from app.domain.queue_order import QueueItem, queue_order
from app.domain.scheduling import PlanInput, build_plan


def q(pid, seq, priority=0, started=False):
    return QueueItem(pid, seq, pid, priority, started)


def test_without_priority_order_is_approval_order():
    assert queue_order([q(3, 2), q(1, None), q(2, 1), q(4, None)]) == [2, 3, 1, 4]


def test_priority_first_then_approval_order():
    assert queue_order([q(1, 1), q(2, 2, 1), q(3, 3), q(4, 4, 1)]) == [2, 4, 1, 3]
    assert queue_order([q(1, 1, 1), q(2, 2, 2)]) == [2, 1]


def test_started_not_displaced():
    # 1 начата: срочная 3 не обгоняет её, но обгоняет неначатую 2
    assert queue_order([q(1, 1, started=True), q(2, 2), q(3, 3, 5)]) == [1, 3, 2]
    # неначатая 1 стоит раньше начатой 2: срочная 3 не обгоняет 2, а 1 идёт до 2 как прежде
    assert queue_order([q(1, 1), q(2, 2, started=True), q(3, 3, 5)]) == [1, 2, 3]
    # срочная до начатой может её обогнать — она и так стояла раньше
    assert queue_order([q(1, 1), q(2, 2, 3), q(3, 3, started=True)]) == [2, 1, 3]


def test_unapproved_stay_after_approved():
    assert queue_order([q(1, None, 9), q(2, 1), q(3, 2, 1)]) == [3, 2, 1]


def _item(pid, seq, priority=0, started=False):
    return PlanInput(pid, 1, date(2026, 10, 12), "VF2", 0, 6, 0, 0, approved_seq=seq,
                     priority=priority, started=started)


def test_build_plan_uses_priority():
    plan = build_plan([_item(1, 1), _item(2, 2, priority=1)], {})
    assert plan.positions[2].machine_start < plan.positions[1].machine_start
    plan = build_plan([_item(1, 1, started=True), _item(2, 2, priority=1)], {})
    assert plan.positions[1].machine_start < plan.positions[2].machine_start
