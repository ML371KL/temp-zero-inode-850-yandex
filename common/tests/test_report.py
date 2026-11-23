# -*- coding: utf-8 -*-
"""Приём отчёта и обвязка «на ожидаемом пути = перекат» на игрушечной модели (без сети, без «сегодня», без ядра).

Игрушка: цена = 1000 + 10 × выручка якоря; перекат по времени — +5 ₽. Перенос на ожидаемом пути (выручка 20) даёт
перекат; сломанный перенос (лишний рубль) обвязка ловит."""
from __future__ import annotations

import json
import types

import pytest

from common import cli
from common import facts_sheet as FS
from common import report as R
from common.testing import reanchor_invariant

EXPECTED_REVENUE = 20.0


def _price(revenue: float) -> float:
    return 1000.0 + 10.0 * revenue


def toy(bias: float = 0.0, supported=("2030H2",)):
    def expected_path(period):
        if period not in supported:
            raise NotImplementedError(f"перенос на {period} не подключён")
        return {"anchor.json": json.dumps({"revenue": EXPECTED_REVENUE})}

    def reanchor(facts, period, out_dir):
        if period not in supported:
            raise NotImplementedError(f"перенос на {period} не подключён")
        rev = json.loads(facts["anchor.json"])["revenue"]
        before = {"median": _price(18.0), "point": _price(18.5)}
        rolled = {"median": _price(EXPECTED_REVENUE) + 5.0, "point": _price(EXPECTED_REVENUE) + 5.5}
        after = {"median": _price(rev) + 5.0 + bias, "point": _price(rev) + 5.5 + bias}
        return {"before": before, "rolled": rolled, "after": after, "anchor_before": "2030H1",
                "valuation_date": "2031-03-01", "candidate": {"книга": f"{out_dir}/book.yaml"}, "notes": ["игрушка"]}

    return types.SimpleNamespace(SLUG="demo", TICKERS=("DEMO",), expected_path=expected_path, reanchor=reanchor,
                                 facts_mapping={"anchor.json": {"revenue": FS.Fact("revenue")}},
                                 facts_sheets=lambda period: [period])


def test_on_the_expected_path_the_reanchor_is_the_rollover(tmp_path):
    dec = reanchor_invariant(toy(), "2030H2", tmp_path)
    assert dec["median"]["mechanics"] == 0 and dec["median"]["rollover"] == pytest.approx(25.0)
    assert dec["point"]["surprise"] == 0


def test_a_mechanical_error_is_caught(tmp_path):
    with pytest.raises(AssertionError, match="механика"):
        reanchor_invariant(toy(bias=1.0), "2030H2", tmp_path)
    reanchor_invariant(toy(bias=0.4), "2030H2", tmp_path)                     # в допуске 0,5 ₽
    with pytest.raises(AssertionError):
        reanchor_invariant(toy(bias=0.4), "2030H2", tmp_path, tolerance=0.1)


def run(argv, root, adapter):
    lines: list[str] = []
    code = cli.main(["report", *argv], root=root, adapter=adapter, say=lines.append)
    return code, "\n".join(lines)


def test_dry_run_writes_the_report_and_not_data(tmp_path):
    code, out = run(["--period", "2030H2", "--dry-run"], tmp_path, toy())
    assert code == 0, out
    rep = tmp_path / "var" / "report-2030H2"
    text = (rep / "REPORT.md").read_text(encoding="utf-8")
    assert "Механика в допуске" in text and "сухой прогон" in text
    doc = json.loads((rep / "report.json").read_text(encoding="utf-8"))
    assert doc["dry_run"] is True and doc["decomposition"]["median"]["mechanics"] == 0
    assert not (tmp_path / "data").exists()
    code, out = run(["--period", "2030H2", "--dry-run", "--out", "data/x"], tmp_path, toy())
    assert code == 1 and "только под var/" in out and not (tmp_path / "data").exists()


def test_facts_of_the_sheet_give_the_surprise(tmp_path):
    sheets = tmp_path / "data" / "facts" / "sheets"
    sheets.mkdir(parents=True)
    (sheets / "documents.csv").write_bytes(("doc_id,title,issuer_url,published,sha256\n"
                                            f"r1,Отчёт,https://example.org/r1.pdf,2031-02-17,{'a' * 64}\n").encode())
    (sheets / "2030H2.csv").write_bytes(("metric,segment,period,value,unit,doc_id,page,note\n"
                                         "revenue,,2030H2,21.5,bn_rub,r1,3,\n").encode())
    code, out = run(["--period", "2030H2", "--out", "var/out"], tmp_path, toy())
    assert code == 0, out
    dec = json.loads((tmp_path / "var" / "out" / "report.json").read_text(encoding="utf-8"))["decomposition"]
    assert dec["median"]["surprise"] == pytest.approx(15.0) and dec["median"]["mechanics"] == 0
    assert dec["median"]["total"] == pytest.approx(dec["median"]["after"] - dec["median"]["before"])


def test_a_bad_mechanics_fails_the_command_and_unsupported_period_is_not_connected(tmp_path):
    code, out = run(["--period", "2030H2", "--dry-run"], tmp_path, toy(bias=3.0))
    assert code == 1 and "ВНЕ ДОПУСКА" in out
    code, out = run(["--period", "2031H1", "--dry-run"], tmp_path, toy())
    assert code == 2 and "не подключено: перенос на 2031H1" in out
    assert run(["--period", "2030H2"], tmp_path, None)[0] == 2
    assert R.decompose({"before": {"median": 1, "point": 2}, "rolled": {"median": 1, "point": 2},
                        "after": {"median": 1, "point": 2}})["point"]["total"] == 0
