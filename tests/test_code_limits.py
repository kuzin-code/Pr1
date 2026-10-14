"""Правила проекта: не более 500 строк в файле; нет внешних соединений (НФТ-01)."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for d in ("app", "tests") for p in (ROOT / d).rglob("*")
                 if p.suffix in {".py", ".html", ".css"})
MAX_LINES = 500


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_file_not_longer_than_500_lines(path):
    lines = path.read_text(encoding="utf-8").count("\n") + 1
    assert lines <= MAX_LINES, f"{path.name}: {lines} строк"


def test_no_external_connections_in_application():
    pattern = re.compile(r"https?://|import requests|urllib\.request|httpx|socket\.")
    offenders = [str(p.relative_to(ROOT)) for p in SOURCES
                 if p.is_relative_to(ROOT / "app") and pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
