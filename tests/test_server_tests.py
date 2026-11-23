"""Набор тестов сервера (`ops/server_tests.py`, `python -m common.cli server-tests`) и белый список такта
(`tests/conftest.py: TACT_FILES`). Сам набор здесь не гоняется — только отбор, деление и разбор отчёта."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from ops import server_tests as S

ROOT = Path(__file__).resolve().parents[1]


def test_selection_is_exactly_the_tact_step_of_run_sh():
    assert S.tact_expression() == "tact and not network and not docs and not ci_only"


def test_whitelist_names_existing_files_and_leaves_out_the_tact_itself():
    files = S.tact_files()
    assert len(files) == len(set(files)) and all((ROOT / f).is_file() for f in files)
    for out in ("tests/test_run_sh.py", "tests/test_server_tests.py", "tests/test_live_network.py"):
        assert out not in files


def test_expected_counters_match_the_collected_tact_tests():
    """Новый тест такта без правки EXPECTED — провал здесь, а не на сервере."""
    res = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          "-m", S.tact_expression(), *S.tact_files()],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    m = re.search(r"(\d+)(?:/\d+)? tests? collected", res.stdout)
    assert m, res.stdout[-500:]
    assert int(m.group(1)) == sum(S.EXPECTED.values())


def test_groups_are_balanced_by_weight():
    groups = S.split(["a", "b", "c", "d"], 2, {"a": 5.0, "b": 3.0, "c": 2.0, "d": 1.0})
    assert sorted(map(sorted, groups)) == [["a", "d"], ["b", "c"]]
    assert S.split(["a"], 4, {}) == [["a"]]


def test_junit_report_is_counted(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text(
        '<testsuites><testsuite>'
        '<testcase file="tests/x.py" name="a" time="1.5"/>'
        '<testcase file="tests/x.py" name="b" time="0.5"><failure/></testcase>'
        '<testcase file="tests/y.py" name="c" time="0.1"><skipped type="pytest.xfail"/></testcase>'
        '<testcase file="tests/y.py" name="d" time="0.1"><skipped type="pytest.skip"/></testcase>'
        '<testcase file="tests/y.py" name="e" time="0.1"><error/></testcase>'
        '</testsuite></testsuites>', encoding="utf-8")
    c, per_file = S.counts(xml)
    assert c == {"passed": 1, "failed": 1, "errors": 1, "skipped": 1, "xfailed": 1}
    assert per_file == pytest.approx({"tests/x.py": 2.0, "tests/y.py": 0.3})
    assert S.counts(tmp_path / "none.xml")[0]["errors"] == 1


def test_the_command_is_connected():
    from ops import common_adapter

    assert common_adapter.COMMANDS["server-tests"][-1] == "ops.server_tests"
