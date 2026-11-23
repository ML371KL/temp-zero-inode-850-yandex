# -*- coding: utf-8 -*-
"""Общие обвязки тестов моделей семейства (README слоя, раздел 5).

`reanchor_invariant(adapter, period, out_dir)` — «на ожидаемом пути = перекат»: модель подаёт свой ожидаемый путь
периода как факты (`expected_path(period)`), переносит книгу (`reanchor`), и механика — перенос на своём же пути минус
перекат прежней книги — должна быть ≈ 0 по медиане и по точке. Без сети и без «сегодня»: дату оценки и случайные числа
держит модель. Тест модели зовёт обвязку из своего набора (и на сервере — без метки ci_only).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common import report as R


def reanchor_invariant(adapter: Any, period: str, out_dir: Path | str, tolerance: float | None = None) -> dict:
    """Разложение переноса на ожидаемом пути (`common.report.decompose`); механика вне допуска — AssertionError."""
    res = adapter.reanchor(adapter.expected_path(period), period, str(out_dir))
    dec = R.decompose(res)
    tol = R.tolerance(adapter) if tolerance is None else float(tolerance)
    bad = R.mechanics_problems(dec, tol)
    assert not bad, f"{getattr(adapter, 'SLUG', '?')} {period}: на ожидаемом пути перенос ≠ перекат — " + "; ".join(bad)
    return dec
