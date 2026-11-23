# -*- coding: utf-8 -*-
"""Общие места Яндекса (задача плана 15): календарь `family-calendar-v1`, перенос книги и обвязка слоя «на
ожидаемом пути = перекат». Без сети и без «сегодня»: даты — от даты книги и параметром."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from common import calendar as C
from common.testing import reanchor_invariant
from ops import common_adapter

ROOT = Path(__file__).resolve().parents[1]
CALENDAR = ROOT / "data" / "calendar.json"


def test_the_calendar_is_the_family_format_with_a_source_for_every_event():
    cal = C.load(CALENDAR)
    assert cal["issuer"] == common_adapter.SLUG
    assert all(e["src"].strip() for e in cal["events"])
    assert all(e["precision"] in ("day", "estimate") for e in cal["events"])
    assert all(e["earliest"] and e["latest"] for e in cal["events"] if e["precision"] == "estimate")


def test_the_closing_report_of_the_open_half_and_the_report_state(book, book_date):
    cal = C.load(CALENDAR)
    anchor = book["meta"]["anchor_period"]
    closing = C.closing_report(cal, C.next_period(anchor))
    assert closing["kind"] == "report" and closing["covers"] == book["meta"]["first_period"]
    assert not closing["confirmed"] and closing["precision"] == "estimate"
    assert C.report_state(cal, book_date, anchor)["state"] == "current"
    assert C.report_state(cal, dt.date(2027, 1, 15), anchor)["state"] == "closing_due"
    assert C.report_state(cal, dt.date.fromisoformat(closing["latest"]) + dt.timedelta(days=1), anchor)["state"] == \
        "overdue"


def test_on_the_expected_path_of_the_anchor_the_reanchor_is_the_rollover(tmp_path, monkeypatch):
    """Обвязка слоя на периоде якоря: факты = ожидание (data/facts), перевывод на копии книги, число на тех же
    случайных числах — механика 0 по медиане (малая полоса такта, 40 прогонов) и по точке."""
    monkeypatch.setattr(common_adapter, "REANCHOR_DRAWS", 40)
    dec = reanchor_invariant(common_adapter, "2026H1", tmp_path / "var" / "report")
    assert dec["median"]["mechanics"] == 0 and dec["point"]["mechanics"] == 0
    assert (tmp_path / "var" / "report" / "book" / "assumptions.yaml").exists()


def test_the_next_period_is_not_connected_and_says_why(tmp_path):
    """Перенос на следующий период (2026H2) модель не умеет: якорь держат факты и перевывод — «не подключено» с
    причиной, а не тихий ноль (рабочий файл, раздел 6, задача 15)."""
    with pytest.raises(NotImplementedError, match="перенос книги на якорь 2026H2"):
        common_adapter.expected_path("2026H2")
    with pytest.raises(NotImplementedError, match="model/facts.py"):
        common_adapter.reanchor({}, "2026H2", str(tmp_path))


def test_report_surprise_is_counted_from_the_expected_path_not_from_the_rollover():
    """Приём отчёта (`common.report.decompose`): механика (ожидаемый путь − перекат) в сюрприз не входит — сюрприз =
    факты − ожидаемый путь. Тест слоя закрывает этот случай только при механике 0, поэтому — здесь, без правки слоя."""
    from common import report as R
    dec = R.decompose({"before": {"median": 100.0, "point": 200.0}, "rolled": {"median": 110.0, "point": 205.0},
                       "after": {"median": 110.25, "point": 204.5}}, {"after": {"median": 125.0, "point": 210.0}})
    assert dec["median"] == pytest.approx({"before": 100.0, "rolled": 110.0, "expected": 110.25, "after": 125.0,
                                           "rollover": 10.0, "mechanics": 0.25, "surprise": 14.75, "total": 25.0})
    assert dec["point"]["surprise"] == pytest.approx(5.5) and dec["point"]["mechanics"] == pytest.approx(-0.5)
