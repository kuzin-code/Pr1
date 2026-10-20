"""Ступени количества (1 / 10 / 50 шт.): наладка делится на партию, цена за штуку падает с ростом количества."""
from datetime import datetime

import pytest

from app.domain.cost import compute_cost
from app.domain.enums import CountMode, Stage
from app.domain.models import Rates, StageNorm
from app.domain.quantity_breaks import MAX_BREAKS, parse_quantities, price_breaks
from app.domain.timing import ExtraMachine, batch_times, compute_stage_times
from app.domain.work_calendar import MACHINE_CALENDAR

ROUTE = [StageNorm(Stage.PROGRAMMING, 2.0, CountMode.ONCE),
         StageNorm(Stage.SETUP, 3.0, CountMode.PER_BATCH),
         StageNorm(Stage.MACHINING, 0.5, CountMode.PER_PIECE),
         StageNorm(Stage.LOCKSMITH, 0.2, CountMode.PER_PIECE),
         StageNorm(Stage.INSPECTION, 0.5, CountMode.PER_BATCH)]
RATES = Rates(2000, 1000)


def test_parse_quantities():
    assert parse_quantities("50, 1; 10 100") == [1, 10, 50, 100]
    assert parse_quantities("10, 10, 1") == [1, 10]
    assert parse_quantities("") == [] and parse_quantities(None) == []
    assert parse_quantities([5, "2"]) == [2, 5]
    with pytest.raises(ValueError):
        parse_quantities("1, 2, 3, 4, 5")  # больше MAX_BREAKS
    with pytest.raises(ValueError):
        parse_quantities("0, 10")
    with pytest.raises(ValueError):
        parse_quantities("десять")
    assert MAX_BREAKS == 4


def test_setup_is_shared_by_the_batch():
    b1, b10 = price_breaks(ROUTE, [1, 10], 1.0, 1.0, RATES)
    assert b1.machine_hours == pytest.approx(3.5) and b10.machine_hours == pytest.approx(8.0)
    assert b1.bench_hours == pytest.approx(0.2) and b10.bench_hours == pytest.approx(2.0)
    assert b1.batch_price == pytest.approx(3.5 * 2000 + 0.2 * 1000)
    assert b10.piece_price == pytest.approx((8.0 * 2000 + 2.0 * 1000) / 10)
    assert b10.piece_price < b1.piece_price


def test_piece_price_never_grows_with_quantity():
    breaks = price_breaks(ROUTE, [100, 1, 50, 10], 1.3, 0.9, RATES)
    assert [b.quantity for b in breaks] == [1, 10, 50, 100]
    prices = [b.piece_price for b in breaks]
    assert all(a >= b for a, b in zip(prices, prices[1:]))
    assert all(a.batch_price <= b.batch_price for a, b in zip(breaks, breaks[1:]))


def test_single_break_matches_regular_calculation():
    (b,) = price_breaks(ROUTE, [7], 1.2, 0.8, RATES)
    cost = compute_cost(compute_stage_times(ROUTE, 7, 1.2, 0.8), 7, RATES)
    assert b.batch_price == pytest.approx(cost.batch_cost) and b.piece_price == pytest.approx(cost.piece_cost)
    assert b.machine_hours == pytest.approx(cost.machine_hours)


def test_second_machine_counted_per_quantity():
    extra = [ExtraMachine("ST20Y", (StageNorm(Stage.SETUP, 1.0, CountMode.PER_BATCH),
                                    StageNorm(Stage.MACHINING, 0.1, CountMode.PER_PIECE)), "turning")]
    (b,) = price_breaks(ROUTE, [10], 1.0, 1.0, RATES, extra)
    assert b.machine_hours == pytest.approx(8.0 + 1.0 + 1.0)
    times = batch_times(ROUTE, 10, 1.0, 1.0, extra)
    assert [t.machine for t in times if t.machine] == ["ST20Y", "ST20Y"]
    # второй станок — сразу после станочных этапов основного станка
    assert [t.stage for t in times].index(Stage.LOCKSMITH) > max(i for i, t in enumerate(times) if t.machine)


def test_ready_on_machine_by_calendar():
    start = datetime(2026, 10, 19, 8)
    b1, b50 = price_breaks(ROUTE, [1, 50], 1.0, 1.0, RATES, machine_start=start)
    assert b1.ready == MACHINE_CALENDAR.add_hours(start, 3.5)
    assert b50.ready == MACHINE_CALENDAR.add_hours(start, 28.0) and b50.ready > b1.ready
    assert price_breaks(ROUTE, [1], 1.0, 1.0, RATES)[0].ready is None


def test_to_dict():
    (b,) = price_breaks(ROUTE, [2], 1.0, 1.0, RATES, machine_start=datetime(2026, 10, 19, 8))
    d = b.to_dict()
    assert d["quantity"] == 2 and d["piece_price"] == pytest.approx(d["batch_price"] / 2)
    assert set(d) == {"quantity", "machine_hours", "bench_hours", "batch_price", "piece_price", "ready"}
    assert datetime.fromisoformat(d["ready"]) == b.ready
