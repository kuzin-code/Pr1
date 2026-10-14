"""Справочники, ставки, журнал, учётные записи (ФТ-25, Д-5, Д-6, НФТ-06)."""
import pytest

from app.domain.access import AccessDenied
from app.domain.history import ReasonRequired
from app.domain.models import Rates
from app.services import reference_service as ref
from app.services.history_service import list_history
from app.services.seed import seed_reference
from app.storage.db import init_db, make_engine
from tests.domain.conftest import machine_measurements, make_entry
from tests.services.conftest import HEAD, IT, SEED_DIR, TECH


def test_seed_loads_seven_machines_rates_and_nine_journal_parts(session):
    assert len(ref.list_machines(session)) == 7
    assert sum(m.is_cnc for m in ref.list_machines(session)) == 5
    assert ref.get_rates(session) == Rates(2000, 1000)
    assert len(ref.journal_entries(session)) == 9
    assert len(ref.complexity_scale(session)) == 5


def test_seed_is_idempotent(session):
    seed_reference(session, SEED_DIR)
    assert len(ref.list_machines(session)) == 7
    assert len(ref.journal_entries(session)) == 9


def test_authenticate(session):
    assert ref.authenticate(session, "it", "demo").role == "it"
    assert ref.authenticate(session, "it", "wrong") is None
    assert ref.authenticate(session, "nobody", "demo") is None


def test_only_it_changes_rates_with_history(session):
    with pytest.raises(AccessDenied):
        ref.set_rates(session, HEAD, 2500, 1200, "индексация")
    with pytest.raises(ReasonRequired):
        ref.set_rates(session, IT, 2500, 1200, "")
    ref.set_rates(session, IT, 2500, 1200, "индексация")
    assert ref.get_rates(session) == Rates(2500, 1200)
    (record,) = list_history(session, entity="parameters")
    assert record.username == "it" and record.reason == "индексация"


def test_journal_import_replaces_entries_and_bumps_version(session, tmp_path):
    from app.parsers.journal_excel import export_journal
    path = tmp_path / "j.xlsx"
    export_journal([make_entry("REAL-1", measurements=machine_measurements(1.2))], path)
    version = ref.journal_version(session)
    with pytest.raises(AccessDenied):
        ref.import_journal_file(session, HEAD, path)
    assert ref.import_journal_file(session, IT, path, replace=True) == 1
    (entry,) = ref.journal_entries(session)
    assert entry.part_code == "REAL-1" and len(entry.measurements) == 1
    assert ref.journal_version(session) == version + 1
    ref.import_journal_file(session, TECH, path)  # технолог ведёт журнал


def test_journal_keeps_measurements_after_restart(tmp_path):
    from app.domain.enums import StageGroup
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    factory = init_db(make_engine(url))
    with factory() as s:
        seed_reference(s, SEED_DIR)
        ref.add_journal_measurement(s, "DEMO-01", StageGroup.MACHINE, 2.0, 2.4)
        s.commit()
    factory = init_db(make_engine(url))
    with factory() as s:
        entry = ref.journal_entry(s, "DEMO-01")
        assert entry.measurements[0].actual_hours == 2.4
