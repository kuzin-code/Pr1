from datetime import date
from pathlib import Path

import pytest

from app.domain.enums import Role
from app.services.actor import Actor
from app.services.order_service import UploadedFile, create_order
from app.services.seed import seed_reference
from app.storage.db import init_db, make_engine
from tests.fixtures.samples import make_box_step, make_pdf

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"
MON = date(2026, 10, 12)

TECH = Actor("technolog", Role.TECHNOLOGIST)
MASTER = Actor("master", Role.MASTER)
OPERATOR = Actor("operator", Role.OPERATOR)
HEAD = Actor("head", Role.HEAD)
IT = Actor("it", Role.IT)
MGMT = Actor("management", Role.MANAGEMENT)


@pytest.fixture
def session():
    factory = init_db(make_engine("sqlite://"))
    with factory() as s:
        seed_reference(s, SEED_DIR)
        yield s


def part_files(name="Кронштейн-1", dims=(152, 58, 40), pdf_lines=("Material: D16T", "Bore 10H9")):
    return [UploadedFile(f"{name}.step", make_box_step(*dims).encode()),
            UploadedFile(f"{name}.pdf", make_pdf(list(pdf_lines)))]


@pytest.fixture
def make_order(session, tmp_path):
    def _make(files=None, start=MON):
        return create_order(session, MASTER, start, files or part_files(), tmp_path / "uploads")
    return _make
