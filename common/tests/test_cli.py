# -*- coding: utf-8 -*-
"""Набор команд и приём слоя — на пустом временном дереве и выдуманном переходнике (без сети и без модели)."""
from __future__ import annotations

import json
import shutil
import types
from pathlib import Path

from common import cli
from common import layer as L

LAYER = Path(__file__).resolve().parents[1]


def run(argv, root, adapter):
    lines: list[str] = []
    code = cli.main(argv, root=root, adapter=adapter, say=lines.append)
    return code, "\n".join(lines)


def fake_adapter(**over):
    a = types.SimpleNamespace(
        SLUG="demo", TICKERS=("DEMO",), facts_mapping={},
        COMMANDS={"rederive": "задача плана 9", "quick": lambda argv: 7, "band": None},
        header_inputs=lambda payload: {}, reanchor=lambda facts, period, out: {}, expected_path=lambda period: {})
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_every_command_name_is_known_and_unconnected_ones_say_why(tmp_path):
    a = fake_adapter()
    assert run(["rederive"], tmp_path, a) == (2, "не подключено: задача плана 9")
    assert run(["quick", "--x"], tmp_path, a)[0] == 7
    code, out = run(["band"], tmp_path, a)
    assert code == 2 and "не подключено" in out
    for name in ("server-tests", "release"):
        code, out = run([name], tmp_path, a)
        assert code == 2 and "нет в COMMANDS" in out, name
    code, out = run(["report", "--period", "2030H2", "--dry-run"], tmp_path, a)   # report делает слой через reanchor
    assert code == 1 and "ОТКАЗ" in out
    assert run(["nope"], tmp_path, a)[0] == 2
    assert run(["quick"], tmp_path, None)[0] == 2


def test_state_prints_what_there_is_without_failing(tmp_path):
    code, out = run(["state"], tmp_path, None)
    assert code == 0
    for title in ("модель: нет", "слой: нет", "миры: нет", "выпуск: нет", "книга: нет", "переходник:", "команды:"):
        assert title in out, title
    code, out = run(["state"], tmp_path, fake_adapter(RELEASE_PATH="var/release/latest.json"))
    assert code == 0 and "модель: demo (DEMO)" in out and "выпуск не собран" in out


def test_worlds_without_the_engine_is_not_connected(tmp_path):
    assert run(["worlds", "--dry-run"], tmp_path, None)[0] == 2


def test_adopt_common_copies_the_layer_and_writes_the_state(tmp_path):
    src = tmp_path / "src" / "common"
    shutil.copytree(LAYER, src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    root = tmp_path / "model"
    (root / "common").mkdir(parents=True)
    (root / "common" / "stray.txt").write_text("x", encoding="utf-8")
    a = fake_adapter()
    code, out = run(["adopt-common", "--source", str(src), "--dry-run"], root, a)
    assert code == 0 and "- stray.txt" in out and not (root / L.STATE_REL).exists()
    assert "common/** -text" in out                               # .gitattributes модели — поправить
    code, out = run(["adopt-common", "--source", str(src)], root, a)
    assert code == 0, out
    assert L.verify(root / "common") == [] and not (root / "common" / "stray.txt").exists()
    st = json.loads((root / L.STATE_REL).read_text(encoding="utf-8"))
    assert st["manifest_sha256"] == L.manifest_sha256(src) and st["version"] == L.read_version(src)
    assert L.verify_state(root) == []
    st["source_commit"] = "c0ffee"                                  # повтор приёма того же слоя: состояние прежнее
    (root / L.STATE_REL).write_text(json.dumps(st), encoding="utf-8")
    code, out = run(["adopt-common", "--source", str(src)], root, a)
    assert code == 0 and "состояние data/common_state.json: прежнее" in out
    assert json.loads((root / L.STATE_REL).read_text(encoding="utf-8"))["source_commit"] == "c0ffee"
    (root / "common" / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    assert any("VERSION" in b for b in L.verify_state(root))


def test_adopt_common_refuses_a_source_that_is_not_its_manifest(tmp_path):
    src = tmp_path / "src" / "common"
    shutil.copytree(LAYER, src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (src / "extra.py").write_text("", encoding="utf-8")
    code, out = run(["adopt-common", "--source", str(src)], tmp_path / "model", fake_adapter())
    assert code == 1 and "extra.py: лишний файл" in out


def test_adapter_gaps_name_what_is_missing():
    a = fake_adapter()
    del a.reanchor
    gaps = L.adapter_gaps(a)
    assert any("`reanchor`" in g for g in gaps)
    assert any("server-tests" in g for g in gaps)                 # команды без строки COMMANDS
    assert L.adapter_gaps(None)[0].startswith("нет переходника")
