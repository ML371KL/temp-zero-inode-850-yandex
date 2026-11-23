# -*- coding: utf-8 -*-
"""Сторож общего слоя: слой модели — побайтовая копия источника, принятая командой adopt-common.

Без сети: sha256 манифеста = состоянию модели `data/common_state.json`, версия слоя = состоянию, каждый файл слоя =
манифесту, лишних файлов в `common/` нет. Сторож сам входит в манифест — правка сторожа на месте тоже красная.
"""
from __future__ import annotations

from pathlib import Path

from common import layer as L

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / "common"


def test_the_state_names_this_manifest_and_version():
    st = L.read_state(ROOT)
    assert st is not None, f"нет {L.STATE_REL}: слой принимается командой `python -m common.cli adopt-common`"
    assert set(st) == {"version", "source", "source_commit", "manifest_sha256"}, sorted(st)
    assert L.manifest_sha256(LAYER) == st["manifest_sha256"], "манифест слоя не равен состоянию модели"
    assert L.read_version(LAYER) == st["version"]


def test_every_file_of_the_layer_is_the_manifest_file():
    bad = L.verify(LAYER)
    assert not bad, "слой не равен манифесту (слой правится только в источнике):\n  " + "\n  ".join(bad)


def test_the_guard_itself_is_in_the_manifest():
    assert "tests/test_common_guard.py" in L.read_manifest(LAYER)
