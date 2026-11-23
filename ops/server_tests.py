"""Набор тестов сервера: `python -m common.cli server-tests [--groups K]` (= `python -B -m ops.server_tests`).

Тот же отбор, что у шага «тесты такта» `ops/run.sh` (`TACT_TESTS`, читается из run.sh): тесты с меткой `tact` —
белый список файлов `TACT_FILES` в `tests/conftest.py`. Файлы делятся на K групп (по времени прошлого прогона,
`var/server-tests/times.json`; без него — по размеру файла), каждая группа — свой процесс pytest с отчётом JUnit;
группы идут параллельно. Печать — группы, счётчики против ожидаемых `EXPECTED` и время против потолка `LIMIT_S`.

Код выхода: 0 — счётчики равны ожидаемым и провалов нет; 1 — иначе. Время сверх потолка — строка «ВНИМАНИЕ», код
не меняет. Новый тест такта — поправить `EXPECTED` тем же коммитом (тест `tests/test_server_tests.py`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var" / "server-tests"
TIMES = OUT / "times.json"
RUN_SH = ROOT / "ops" / "run.sh"
GROUPS = 4                      # процессов pytest по умолчанию
LIMIT_S = 180                   # потолок набора на ноутбуке (задача плана 7)
POLL_S = 0.2                    # опрос процессов групп, с
# Ожидаемые счётчики набора такта (`-m TACT_TESTS`, без сети и CI).
EXPECTED = {"passed": 411, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0}


def tact_expression() -> str:
    """Выражение отбора такта — ровно `TACT_TESTS` из ops/run.sh."""
    m = re.search(r'^TACT_TESTS="([^"]+)"$', RUN_SH.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("ops/run.sh: строки TACT_TESTS=\"…\" нет — отбор такта неизвестен")
    return m.group(1)


def tact_files() -> tuple[str, ...]:
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from conftest import TACT_FILES  # noqa: E402 — белый список живёт рядом с тестами
    finally:
        sys.path.pop(0)
    return tuple(TACT_FILES)


def split(files, k: int, weights: dict) -> list[list[str]]:
    """Жадно по убыванию веса: каждый файл — в самую лёгкую группу."""
    groups = [[] for _ in range(max(1, min(k, len(files))))]
    load = [0.0] * len(groups)
    for f in sorted(files, key=lambda f: (-weights.get(f, 0.0), f)):
        i = load.index(min(load))
        groups[i].append(f)
        load[i] += weights.get(f, 0.0)
    return [g for g in groups if g]


def weights_of(files) -> dict:
    if TIMES.exists():
        try:
            known = json.loads(TIMES.read_text(encoding="utf-8"))
            if all(f in known for f in files):
                return {f: float(known[f]) for f in files}
        except (ValueError, OSError):
            pass
    return {f: (ROOT / f).stat().st_size / 1000.0 for f in files}


def counts(xml_path: Path) -> tuple[dict, dict]:
    """Счётчики и время по файлам из отчёта JUnit."""
    c = {k: 0 for k in EXPECTED}
    per_file: dict[str, float] = {}
    if not xml_path.exists():
        c["errors"] += 1
        return c, per_file
    for tc in ET.parse(xml_path).iter("testcase"):
        kids = {child.tag: child for child in tc}
        if "failure" in kids:
            c["failed"] += 1
        elif "error" in kids:
            c["errors"] += 1
        elif "skipped" in kids:
            c["xfailed" if (kids["skipped"].get("type") or "") == "pytest.xfail" else "skipped"] += 1
        else:
            c["passed"] += 1
        f = (tc.get("file") or "?").replace("\\", "/")
        per_file[f] = per_file.get(f, 0.0) + float(tc.get("time") or 0.0)
    return c, per_file


def run(k: int = GROUPS, python: str | None = None) -> int:
    expr, files = tact_expression(), tact_files()
    missing = [f for f in files if not (ROOT / f).exists()]
    if missing:
        print(f"нет файлов белого списка: {', '.join(missing)}")
        return 1
    groups = split(files, k, weights_of(files))
    OUT.mkdir(parents=True, exist_ok=True)
    (ROOT / "var" / "pytest-tmp").mkdir(parents=True, exist_ok=True)    # родитель --basetemp групп (свежий клон)
    env ={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("YANDEX_NETWORK", None)
    t0 = time.perf_counter()
    procs = []
    for i, g in enumerate(groups, 1):
        xml = OUT / f"group-{i}.xml"
        xml.unlink(missing_ok=True)
        cmd = [python or sys.executable, "-m", "pytest", "-q", "-m", expr, "-p", "no:cacheprovider",
               "-o", "junit_family=xunit1",
               "--basetemp", str(ROOT / "var" / "pytest-tmp" / f"server-{i}"), "--junitxml", str(xml), *g]
        log = open(OUT / f"group-{i}.log", "w", encoding="utf-8")
        procs.append((i, g, xml, log, time.perf_counter(),
                      subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)))
    ends: dict[int, float] = {}
    while len(ends) < len(procs):
        for i, *_rest, p in procs:
            if i not in ends and p.poll() is not None:
                ends[i] = time.perf_counter()
        if len(ends) < len(procs):
            time.sleep(POLL_S)
    total = {k_: 0 for k_ in EXPECTED}
    times: dict[str, float] = {}
    bad_groups = []
    for i, g, xml, log, start, p in procs:
        rc = p.wait()
        log.close()
        spent = ends[i] - start
        c, per_file = counts(xml)
        times.update(per_file)
        for k_ in total:
            total[k_] += c[k_]
        line = ", ".join(f"{v} {k_}" for k_, v in c.items() if v)
        print(f"группа {i} ({len(g)} файл.): {line or 'пусто'} за {spent:.1f} с; код pytest {rc}; "
              f"журнал {OUT.relative_to(ROOT).as_posix()}/group-{i}.log")
        if rc not in (0,):
            bad_groups.append(i)
    elapsed = time.perf_counter() - t0
    by_file = {f: round(times.get(f, 0.0), 2) for f in files}
    TIMES.write_text(json.dumps(by_file, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = total == EXPECTED and not bad_groups
    print("счётчики против ожидаемых: " + "; ".join(f"{k_} {total[k_]} / {EXPECTED[k_]}" for k_ in EXPECTED))
    if elapsed > LIMIT_S:
        print(f"ВНИМАНИЕ: набор шёл {elapsed:.0f} с — дольше потолка {LIMIT_S} с")
    print(f"ИТОГ: {'прошло' if ok else 'ПРОВАЛ'} — {sum(total.values())} тестов такта в {len(groups)} процессах "
          f"за {elapsed:.1f} с (потолок {LIMIT_S} с); отбор «{expr}»")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="server-tests", description="набор тестов такта, параллельно по группам файлов")
    ap.add_argument("--groups", type=int, default=GROUPS, help=f"процессов pytest (по умолчанию {GROUPS})")
    args = ap.parse_args(argv)
    return run(args.groups)


if __name__ == "__main__":
    raise SystemExit(main())
