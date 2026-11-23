# -*- coding: utf-8 -*-
"""Календарь слоя на выдуманном эмитенте: проверка формата, ближнее событие, закрывающий отчёт, состояние по отчёту
и срок данных — все даты параметром, без «сегодня»."""
from __future__ import annotations

import copy
import json

import pytest

from common import calendar as C


def ev(id_, kind, date, covers, precision="day", confirmed=True, lo=None, hi=None, **kw):
    return {"id": id_, "kind": kind, "date": date, "precision": precision, "earliest": lo, "latest": hi,
            "confirmed": confirmed, "covers": covers, "gives": kw.get("gives", ["revenue"]), "title": kw.get("title", id_),
            "src": kw.get("src", "сайт эмитента"), "note": None}


CAL = {"schema": C.SCHEMA, "issuer": "demo", "as_of": "2030-11-01", "events": [
    ev("demo.report.2030H1", "report", "2030-07-29", "2030H1"),
    ev("demo.operating.2030Q3", "operating", "2030-10-29", "2030Q3"),
    ev("demo.dividend.2030", "dividend", "2031-04-27", "2030", "estimate", False, "2031-04-27", "2031-04-28"),
    ev("demo.report.2030H2", "report", "2031-02-17", "2030H2", "estimate", False, "2031-02-17", "2031-02-20"),
    ev("demo.cbr.2030-12-19", "cbr", "2030-12-19", None, gives=[]),
]}


def test_a_good_calendar_passes_and_loads(tmp_path):
    assert C.check(CAL) == []
    p = tmp_path / "calendar.json"
    p.write_text(json.dumps(CAL, ensure_ascii=False), encoding="utf-8")
    assert C.load(p)["issuer"] == "demo"
    assert C.main([str(p)]) == 0


@pytest.mark.parametrize("change, words", [
    (lambda c: c["events"][0].update(src=""), "источник"),
    (lambda c: c["events"][0].update(kind="ifrs"), "kind"),
    (lambda c: c["events"][3].update(latest=None), "earliest и latest"),
    (lambda c: c["events"][3].update(confirmed=True), "precision day"),
    (lambda c: c["events"][0].update(id="demo.report.2030H2"), "id"),
    (lambda c: c["events"].append(dict(c["events"][1])), "повторяется"),
    (lambda c: c["events"][0].update(covers=None, id="demo.report.2030-07-29"), "без covers"),
    (lambda c: c["events"][3].update(date="2031-02-25"), "вне окна"),
    (lambda c: c["events"][0].update(when="2030-07-29"), "лишние поля when"),
    (lambda c: c.update(schema="demo-calendar-v1"), "schema"),
])
def test_every_broken_field_is_named(change, words, tmp_path):
    bad = copy.deepcopy(CAL)
    change(bad)
    problems = C.check(bad)
    assert problems and any(words in p for p in problems), problems
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(C.CalendarError) as caught:
        C.load(p)
    assert caught.value.problems == problems


def test_periods():
    assert [C.next_period(p) for p in ("2030H1", "2030H2", "2030Q4", "2030M12", "2030M09", "2030")] == \
        ["2030H2", "2031H1", "2031Q1", "2031M01", "2030M10", "2031"]
    assert [C.period_end(p).isoformat() for p in ("2030H1", "2030H2", "2031Q1", "2032M02", "2030")] == \
        ["2030-06-30", "2030-12-31", "2031-03-31", "2032-02-29", "2030-12-31"]


def test_closing_report_and_next_fact():
    assert C.closing_report(CAL, "2030H2")["id"] == "demo.report.2030H2"       # не дивиденд и не операционный
    assert C.closing_report(CAL, "2030Q3") is None
    assert C.next_fact(CAL, "2030-11-01")["date"] == "2031-02-17"
    assert C.next_fact(CAL, "2031-02-19")["date"] == "2031-02-17"              # окно неподтверждённой даты не прошло
    assert C.next_fact(CAL, "2031-02-21") is None


@pytest.mark.parametrize("today, state, out_since", [
    ("2030-11-01", "current", None),            # 2П ещё идёт
    ("2031-01-15", "closing_due", None),        # 2П кончилось, отчёт впереди
    ("2031-02-19", "closing_due", None),        # дата прошла, окно ещё нет
    ("2031-02-21", "overdue", None),            # окно прошло, отчёта нет
])
def test_report_state_by_the_calendar(today, state, out_since):
    r = C.report_state(CAL, today, "2030H1")
    assert (r["state"], r["out_since"], r["last_fact_period"]) == (state, out_since, "2030H1")


def test_a_confirmed_report_that_came_is_report_out():
    cal = copy.deepcopy(CAL)
    cal["events"][3].update(precision="day", confirmed=True, earliest=None, latest=None)
    assert C.check(cal) == []
    r = C.report_state(cal, "2031-02-17", "2030H1")
    assert r["state"] == "report_out" and r["out_since"] == "2031-02-17"
    assert C.report_state(cal, "2031-02-18", "2030H2")["state"] == "current"   # факт внесён: якорь сдвинулся


def test_the_data_deadline_is_the_closing_report_of_the_next_period():
    (d,) = C.deadlines(CAL, "2030H1")
    assert d["date"] == "2031-02-17" and d["kind"] == "data" and "--period 2030H2" in d["action"]
    assert C.deadlines(CAL, "2030H2") == []
