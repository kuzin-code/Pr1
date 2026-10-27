"""Сводка прогона тестов для GitHub Actions: итог и автоматический разбор упавших тестов.

python scripts/ci_summary.py <junit.xml> "<заголовок>"
Пишет Markdown в $GITHUB_STEP_SUMMARY (локально — в консоль). Разбор: где упало в коде приложения
(самая глубокая строка app/… в трассировке), тип ошибки, группировка по модулю приложения."""
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

APP_FRAME = re.compile(r"(app[\\/][\w\\/]+\.py):(\d+)")
TEST_FRAME = re.compile(r"((?:tests|e2e)[\\/][\w\\/]+\.py):(\d+)")
ERROR_TYPE = re.compile(r"^E\s+(\w+(?:Error|Exception|Denied|Required)|AssertionError)\b", re.M)


def analyse(text: str) -> tuple[str, str, str]:
    """(место в приложении, место в тесте, тип ошибки) по трассировке."""
    app = APP_FRAME.findall(text)
    test = TEST_FRAME.findall(text)
    kind = ERROR_TYPE.findall(text)
    where = f"{app[-1][0]}:{app[-1][1]}".replace("\\", "/") if app else "—"
    in_test = f"{test[-1][0]}:{test[-1][1]}".replace("\\", "/") if test else "—"
    return where, in_test, (kind[-1] if kind else "AssertionError" if "assert" in text else "ошибка")


def short(text: str, limit: int = 300) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("E ")] or text.strip().splitlines()[:3]
    value = " ".join(ln.lstrip("E ").strip() for ln in lines[:4])
    value = value.replace("|", "\\|")
    return value[:limit] + ("…" if len(value) > limit else "")


def main(path: str, title: str) -> int:
    out = [f"## {title}", ""]
    file = Path(path)
    if not file.exists():
        out.append("⚠️ Отчёт тестов не создан — тесты не запустились (смотрите шаг установки или лог).")
        _write(out)
        return 0
    root = ET.parse(file).getroot()
    cases = list(root.iter("testcase"))
    failed = []
    skipped = 0
    for case in cases:
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if case.find("skipped") is not None:
            skipped += 1
        if bad is not None:
            name = f"{case.get('classname', '')}.{case.get('name', '')}".strip(".")
            text = (bad.get("message") or "") + "\n" + (bad.text or "")
            failed.append((name, text))
    passed = len(cases) - len(failed) - skipped
    time = sum(float(s.get("time", 0)) for s in root.iter("testsuite"))
    status = "✅ Все тесты прошли" if not failed else f"❌ Упало тестов: {len(failed)}"
    out += [f"**{status}** · прошло {passed} · пропущено {skipped} · всего {len(cases)} · {time:.0f} с", ""]
    if failed:
        rows = [(name, *analyse(text), short(text)) for name, text in failed]
        out += ["### Автоматический разбор", ""]
        modules = Counter(r[1].rsplit(":", 1)[0] for r in rows if r[1] != "—")
        kinds = Counter(r[3] for r in rows)
        if modules:
            top, count = modules.most_common(1)[0]
            out.append(f"- Чаще всего ошибка возникает в коде приложения: `{top}` ({count} из {len(rows)}).")
        out.append("- Типы ошибок: " + ", ".join(f"{k} — {v}" for k, v in kinds.most_common()) + ".")
        if all(r[1] == "—" for r in rows):
            out.append("- В трассировках нет строк приложения: вероятно, изменилось поведение или проверка в тесте "
                       "(сравните с последним изменением страницы или сервиса).")
        out += ["", "| Тест | Где в приложении | Где в тесте | Тип | Сообщение |", "|---|---|---|---|---|"]
        out += [f"| `{n}` | `{w}` | `{t}` | {k} | {m} |" for n, w, t, k, m in rows[:50]]
        if len(rows) > 50:
            out.append(f"\n…и ещё {len(rows) - 50}; полный список — в артефакте отчёта.")
    _write(out)
    return 0


def _write(lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Тесты"))
