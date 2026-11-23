"""Корневые настройки pytest YDEX (общие для tests/ и common/tests/).

`--basetemp var/pytest-tmp` на свежем клоне: pytest создаёт сам каталог `--basetemp`, но не его родителя — без
`var/` прогон падал бы FileNotFoundError на первом `tmp_path`. Родитель создаётся здесь, до первого теста.
"""

from __future__ import annotations

from pathlib import Path


def pytest_configure(config):
    base = getattr(config.option, "basetemp", None)
    if base:
        Path(base).resolve().parent.mkdir(parents=True, exist_ok=True)
