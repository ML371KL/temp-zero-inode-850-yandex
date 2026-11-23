"""Журнал прогнозов (model/journal.py): запись, факт, неизменяемость."""

from __future__ import annotations

import copy

import pytest

from model import journal as J

PERIOD = "2026H2"
FORECASTS = {"yandex.adj_margin": 0.0612, "yandex.revenue_growth": 0.121}
BENCH = {k: {"margin": 0.06 + i / 1000, "revenue_growth": 0.1 + i / 100, "note": k}
         for i, k in enumerate(J.BENCHMARKS)}


def _first():
    journal, new = J.update(None, period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                            recorded_at="2026-09-28", actuals={})
    for e in journal["entries"]:
        e["release_sha"] = "a" * 64            # как ставит выпуск после хэша
    return journal, new


def test_first_release_records_both_targets():
    journal, new = _first()
    assert sorted(new) == sorted(J.entry_id(t, PERIOD) for t in J.TARGETS)
    by = {e["target"]: e for e in journal["entries"]}
    assert by["yandex.adj_margin"]["forecast"] == 0.0612
    assert by["yandex.adj_margin"]["benchmarks"] == {k: BENCH[k]["margin"] for k in J.BENCHMARKS}
    assert by["yandex.revenue_growth"]["benchmarks"] == {k: BENCH[k]["revenue_growth"]
                                                      for k in J.BENCHMARKS}
    assert all(e["actual"] is None and e["errors"] is None for e in journal["entries"])
    assert journal["rule"] and journal["status"].startswith("копим зачёт: 0 из 4")


def test_next_release_keeps_entries_byte_for_byte():
    first, _ = _first()
    moved = {k: v + 0.01 for k, v in FORECASTS.items()}   # книга сменилась — прогноз другой
    second, new = J.update(first, period=PERIOD, forecasts=moved, bench=BENCH,
                           recorded_at="2026-10-15", actuals={})
    assert new == []
    assert [J.canonical(e) for e in second["entries"]] == [J.canonical(e) for e in first["entries"]]
    assert J.immutability_problems(first, second) == []


def test_actual_closes_the_entry_once():
    first, _ = _first()
    actuals = {("yandex.adj_margin", PERIOD): 0.063}
    second, _ = J.update(first, period="2027H1", forecasts=FORECASTS, bench=BENCH,
                         recorded_at="2027-03-20", actuals=actuals)
    closed = next(e for e in second["entries"] if e["target"] == "yandex.adj_margin"
                  and e["period"] == PERIOD)
    assert closed["actual"] == 0.063
    assert closed["errors"]["forecast"] == pytest.approx(0.0612 - 0.063)
    assert closed["errors"]["benchmarks"]["last_half"] == pytest.approx(BENCH["last_half"]["margin"] - 0.063)
    assert J.immutability_problems(first, second) == []
    # второй факт (правка задним числом) не переписывает первый
    third, _ = J.update(second, period="2027H1", forecasts=FORECASTS, bench=BENCH,
                        recorded_at="2027-03-21", actuals={("yandex.adj_margin", PERIOD): 0.07})
    again = next(e for e in third["entries"] if e["id"] == closed["id"])
    assert again["actual"] == 0.063
    assert J.immutability_problems(second, third) == []
    # новый период получил свои записи
    assert {(e["target"], e["period"]) for e in second["entries"]} >= {("yandex.adj_margin", "2027H1")}


def test_rewriting_the_past_is_caught():
    first, _ = _first()
    for mutate, word in ((lambda j: j["entries"][0].update(forecast=0.07), "переписана"),
                         (lambda j: j["entries"][0].update(benchmarks={}), "переписана"),
                         (lambda j: j["entries"].pop(0), "пропала"),
                         (lambda j: j["entries"].append(copy.deepcopy(j["entries"][0])),
                          "повторяется")):
        bad = copy.deepcopy(first)
        mutate(bad)
        assert any(word in p for p in J.immutability_problems(first, bad)), word
    closed, _ = J.update(first, period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                         recorded_at="2027-03-20", actuals={("yandex.adj_margin", PERIOD): 0.063})
    reopened = copy.deepcopy(closed)
    for e in reopened["entries"]:
        if e["actual"] is not None:
            e["actual"] = 0.05
    assert any("переписана" in p for p in J.immutability_problems(closed, reopened))


def test_no_forecast_after_the_fact():
    journal, new = J.update(None, period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                            recorded_at="2027-03-20", actuals={("yandex.adj_margin", PERIOD): 0.063})
    assert new == [J.entry_id("yandex.revenue_growth", PERIOD)]


def test_journal_accepts_a_list_and_checks_ids():
    first, _ = _first()
    same, new = J.update(first["entries"], period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                         recorded_at="2026-09-29", actuals={})
    assert new == [] and len(same["entries"]) == 2
    with pytest.raises(J.JournalError):
        J.entries_of([{"target": "yandex.adj_margin"}])


def test_status_after_four_closed_halves():
    entries = []
    for i, p in enumerate(("2026H2", "2027H1", "2027H2", "2028H1")):
        e = {"id": J.entry_id("yandex.adj_margin", p), "target": "yandex.adj_margin", "period": p,
             "forecast": 0.06, "benchmarks": {k: 0.06 + (j + 1) / 1000
                                              for j, k in enumerate(J.BENCHMARKS)},
             "actual": None, "errors": None}
        e["actual"] = 0.0605
        e["errors"] = J.errors_of(e, e["actual"])
        entries.append(e)
    text = J.status(entries)
    assert "MSE" in text and "4 полугодий" in text


def _facts_with(rows):
    from pathlib import Path

    from model.facts import Facts
    return Facts(root=Path("."), fixture=False, raw={"actuals": {"actuals": rows}},
                 data={"actuals": {"actuals": rows}})


def test_need_refuses_a_number_without_source():
    """`facts.need`: число факта — только узел {v, src|calc}; голое число — только для сумм сборки листа (`bare`)."""
    from pathlib import Path

    from model.facts import Facts, FactsError, need
    raw = {"balance": {"x": {"v": 1.5}, "y": {"v": 2.5, "src": "МСФО 6М2026, с. 3"}, "z": 3.5}}
    F = Facts(root=Path("."), fixture=False, raw=raw, data={})
    assert need(F, "balance.y") == 2.5 and need(F, "balance.z", bare=True) == 3.5
    for path in ("balance.x", "balance.z"):
        with pytest.raises(FactsError, match="без узла"):
            need(F, path)


def test_actual_is_a_fact_node_with_source():
    """Факт журнала — узел фактов проекта {"v": число, "src"|"calc"}; число без узла (без
    источника), узел без числа или без источника, чужая цель, не полугодие, повтор — отказ."""
    from model.facts import FactsError

    good = {"target": "yandex.adj_margin", "period": "2026H2", "reported_on": "2027-03-19",
            "value": {"v": 0.061, "src": "пресс-релиз 4 кв. 2026, с. 3"}}
    calc = {**good, "target": "yandex.revenue_growth", "value": {"v": 0.09, "calc": "R / R(p − 2) − 1"}}
    assert J.actuals_of(_facts_with([good, calc])) == {("yandex.adj_margin", "2026H2"): 0.061,
                                                       ("yandex.revenue_growth", "2026H2"): 0.09}
    bad = [({**good, "value": 0.061}, "без источника"),
           ({**good, "value": {"v": 0.061}}, "без src и без calc"),
           ({**good, "value": {"v": 0.061, "src": " "}}, "без src и без calc"),
           ({**good, "value": {"v": 0.061, "src": "?"}}, "без src и без calc"),
           ({**good, "value": {"v": None, "src": "x"}}, "ожидается число"),
           ({**good, "value": {"v": "0,061", "src": "x"}}, "ожидается число"),
           ({**good, "target": "yandex.margin"}, "цель журнала"),
           ({**good, "period": "2026-H2"}, "полугодие")]
    for row, words in bad:
        with pytest.raises(FactsError, match=words):
            J.actuals_of(_facts_with([row]))
    with pytest.raises(FactsError, match="повтор"):
        J.actuals_of(_facts_with([good, good]))
