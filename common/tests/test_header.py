# -*- coding: utf-8 -*-
"""Шапка выпуска слоя на выдуманной модели: сборка, схема, правило статуса, сроки — день параметром, без «сегодня»."""
from __future__ import annotations

import copy

import pytest

from common import header as H
from common.tests.test_calendar import CAL


def inputs(**over) -> dict:
    base = {
        "model": {"slug": "demo", "tickers": ["DEMO"], "name": "Демо"},
        "headline": {"printed": 1000.0, "median": 1004.2, "point": 1050.0, "band80": [800.0, 1200.0],
                     "band50": [900.0, 1100.0], "print_step": 50.0, "p_below_market": 0.4},
        "market": {"ticker": "DEMO", "price": 950.0, "price_date": "2030-11-01"},
        "book": {"version": "1.0", "date": "2030-10-01", "tag": "book-1.0"},
        "facts": {"anchor_period": "2030H1", "facts_date": "2030-06-30", "first_period": "2030H2",
                  "open_period": "2030H2", "periods_closed": [], "first_period_closed": False},
        "worlds": {"version": "1.7", "record_asof": "2030-10-28", "record_sha256": "a" * 64, "engine_sha256": "b" * 64},
        "release": {"generated_at": "2030-11-01T12:00:00Z", "valuation_date": "2030-11-01", "engine_commit": "c0ffee",
                    "fast": True},
        "flags": [], "gates": [],
        "deadlines": [{"date": "2031-04-30", "kind": "explanation", "what": "объяснение гейта g", "action": "продлить"}],
        "invariants": [], "calendar": CAL, "today": "2030-11-01",
    }
    base.update(over)
    return base


def test_a_header_is_built_by_the_schema_and_prints():
    h = H.build(inputs())
    assert H.validate(h) == []
    assert list(h) == list(H.FIELDS) and h["schema"] == "family-header-v1"
    assert h["market"]["upside"] == pytest.approx(1000 / 950 - 1)
    assert h["common"]["version"] and len(h["common"]["manifest_sha256"]) == 64      # слой этого дерева
    assert [d["kind"] for d in h["deadlines"]] == ["data", "explanation"]           # срок данных — из календаря, ближний первым
    assert h["report"]["state"] == "current" and h["report"]["next"]["date"] == "2031-02-17"
    assert h["status"] == {"level": "green", "reasons": []}
    assert any("заголовок 1000" in ln for ln in H.lines(h))


def test_today_defaults_to_the_build_day():
    h = H.build(inputs(today=None, release={**inputs()["release"], "generated_at": "2031-01-20T08:00:00Z"}))
    assert h["report"]["state"] == "closing_due" and h["status"]["level"] == "yellow"   # отчёт ближе 30 дней


@pytest.mark.parametrize("over, level, words", [
    ({"invariants": ["probabilities: сумма 0,9"]}, "red", "инвариант"),
    ({"gates": [{"key": "g", "explained": True, "valid_until": "2030-10-31", "mass": 0.2}]}, "red", "истёк срок"),
    ({"flags": [{"name": "price_fallback", "level": "warn", "title": "Цена — запасная", "raised": True}]}, "yellow", "флаг"),
    ({"flags": [{"name": "book_update", "level": "info", "title": "Книгу пора обновить"}]}, "green", None),
    ({"flags": [{"name": "x", "level": "alert", "title": "X", "raised": False}]}, "green", None),
    ({"today": "2031-04-05"}, "yellow", "через 25 дн."),
    ({"today": "2031-02-21"}, "yellow", "прошёл"),
    ({"calendar": None}, "yellow", "календаря нет"),
])
def test_the_status_rule_of_the_layer(over, level, words):
    h = H.build(inputs(**over))
    assert H.validate(h) == []
    assert h["status"]["level"] == level
    assert words is None or any(words in r for r in h["status"]["reasons"]), h["status"]


def test_report_out_is_yellow():
    cal = copy.deepcopy(CAL)
    cal["events"][3].update(precision="day", confirmed=True, earliest=None, latest=None)
    h = H.build(inputs(calendar=cal, today="2031-02-18"))
    assert h["report"]["state"] == "report_out" and h["report"]["out_since"] == "2031-02-17"
    assert any("факт не внесён" in r for r in h["status"]["reasons"])


@pytest.mark.parametrize("breaks, words", [
    (lambda h: h.pop("report"), "нет поля report"),
    (lambda h: h.update(extra=1), "лишнее поле extra"),
    (lambda h: h["headline"].update(printed="1000"), "headline.printed"),
    (lambda h: h["market"].update(upside=0.5), "upside"),
    (lambda h: h["deadlines"].reverse(), "ближний"),
    (lambda h: h["deadlines"][0].update(kind="law"), "kind"),
    (lambda h: h["facts"].update(anchor_period="1П2030"), "anchor_period"),
    (lambda h: h["report"].update(state="late"), "state"),
    (lambda h: h["status"].update(level="yellow"), "без причин"),
    (lambda h: h["headline"].update(band80=[800.0]), "band80"),
    (lambda h: h["release"].update(generated_at="вчера"), "generated_at"),
])
def test_validate_names_what_is_broken(breaks, words):
    h = H.build(inputs())
    breaks(h)
    problems = H.validate(h)
    assert problems and any(words in p for p in problems), problems
