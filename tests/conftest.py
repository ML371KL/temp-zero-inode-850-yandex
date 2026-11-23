"""Общие настройки тестов YDEX.

* **Маркеры** (`pytest.ini`, `--strict-markers`): `network` — только при
  `YANDEX_NETWORK=1`; `ci_only` — только в CI (переменная `CI`);
  `docs`, `slow` — для отбора (`-m "not slow"`); `tact` — тесты такта сервера: ставится здесь по белому
  списку `TACT_FILES`, руками в тестах не пишется.
* **Пропуск без маркера — ошибка.** Тест, пропущенный (`skip`/`skipif`/
  `importorskip`) без одного из маркеров выше, считается упавшим: молчаливый
  пропуск прятал бы непроверенное.
* **«Сегодня»** — `FAKE_TODAY=ГГГГ-ММ-ДД` (прогон «в будущем»); код читает его
  через `model.book.today()`, тесты — фикстурой `today`. Даты в тестах берутся
  от даты книги (`book_date`), а не от сегодняшнего дня.
"""

from __future__ import annotations

import datetime as dt
import getpass
import os
import tempfile
from pathlib import Path

import pytest

SKIP_MARKERS = ("network", "ci_only", "docs", "slow")
ROOT = Path(__file__).resolve().parents[1]
# Тесты такта — БЕЛЫЙ СПИСОК файлов (ops/run.sh, TACT_TESTS; набор `python -m common.cli server-tests`): их тесты
# получают метку `tact`. Новый файл в такт не попадает сам. Вне списка: tests/test_run_sh.py (такт и есть run.sh),
# tests/test_web_node.py (нужен node), tests/test_live_network.py (только сеть), tests/test_server_tests.py (зовёт
# сам набор).
TACT_FILES = (
    "tests/test_core_services.py", "tests/test_checks.py", "tests/test_band.py", "tests/test_payload.py",
    "tests/test_facts_sheet_yandex.py", "tests/test_journal.py", "tests/test_ops_build_release.py",
    "tests/test_publish.py", "tests/test_live_cbr.py", "tests/test_live_http.py", "tests/test_live_iss.py",
    "tests/test_live_price.py", "tests/test_worlds_dependents.py", "tests/test_web_contract.py",
    "tests/test_web_static.py", "tests/test_render_numbers.py",
    "tests/test_book_13.py", "tests/test_book_14.py",          # книги 1.3 и 1.4 (правка этапа 4, ниже порога, п. 4)
    "common/tests/test_cli.py", "common/tests/test_common_guard.py", "common/tests/test_facts_sheet.py",
    "common/tests/test_calendar.py", "common/tests/test_header.py", "common/tests/test_report.py",   # общий слой 1.1
    "tests/test_common_yandex.py",                               # календарь и перенос книги Яндекса (задача 15)
    "tests/test_live_collect.py",                                # вход тактов к сборщикам (задача 16)
)


def _temp_root_usable() -> bool:
    """Каталог временных файлов pytest доступен (на ноутбуке владельца его ACL
    бывает испорчен чужими прогонами — тогда `tmp_path` падал бы PermissionError)."""
    base = Path(tempfile.gettempdir()) / f"pytest-of-{getpass.getuser()}"
    try:
        if base.exists():
            list(os.scandir(base))
        return True
    except OSError:
        return False


if "PYTEST_DEBUG_TEMPROOT" not in os.environ and not _temp_root_usable():
    _root = Path(__file__).resolve().parents[1] / "var" / "pytest-tmp"
    _root.mkdir(parents=True, exist_ok=True)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(_root)


def pytest_report_header(config):
    from model.book import BOOK_YAML
    from model.facts import FACTS_DIR

    lines = []
    if not BOOK_YAML.exists():
        lines.append("книги нет: data/assumptions/assumptions.yaml (ядро и книга — задачи плана)")
    if not (FACTS_DIR / "accounting.json").exists():
        lines.append("фактов нет: data/facts (лист фактов — задача плана)")
    if os.environ.get("FAKE_TODAY"):
        lines.append(f"FAKE_TODAY={os.environ['FAKE_TODAY']}")
    return lines


def pytest_collection_modifyitems(config, items):
    network = os.environ.get("YANDEX_NETWORK") == "1"
    ci = bool(os.environ.get("CI"))
    for item in items:
        try:
            rel = Path(str(item.path)).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            rel = None
        if rel in TACT_FILES:
            item.add_marker(pytest.mark.tact)
        if item.get_closest_marker("network") and not network:
            item.add_marker(pytest.mark.skip(reason="сеть: YANDEX_NETWORK=1"))
        if item.get_closest_marker("ci_only") and not ci:
            item.add_marker(pytest.mark.skip(reason="только в CI"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and not hasattr(report, "wasxfail"):
        if not any(item.get_closest_marker(m) for m in SKIP_MARKERS):
            report.outcome = "failed"
            report.longrepr = (f"тест пропущен без маркера {'/'.join(SKIP_MARKERS)} — это "
                               f"ошибка: {report.longrepr}")


# ------------------------------------------------------------------ фикстуры


@pytest.fixture(scope="session")
def book():
    """Книга по умолчанию (assumptions.yaml или черновик) — только для чтения."""
    from model.book import load_book

    return load_book()


@pytest.fixture(scope="session")
def facts():
    """Факты по умолчанию (data/facts или фикстура)."""
    from model.facts import load_facts

    return load_facts()


@pytest.fixture(scope="session")
def grid(book, facts):
    """Сетка на входах книги."""
    from model.grid import evaluate

    return evaluate(book, facts)


@pytest.fixture(scope="session")
def book_date(book) -> dt.date:
    return dt.date.fromisoformat(book["meta"]["date"])


@pytest.fixture
def today() -> dt.date:
    from model.book import today as _today

    return _today()
