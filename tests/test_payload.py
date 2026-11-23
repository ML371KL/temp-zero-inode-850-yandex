"""Выпуск `yandex-v1` на ядре сервисов (docs/PAYLOAD.md; задача плана 5б): быстрая сборка на малой выборке."""

from __future__ import annotations

import copy
import math

import pytest

from model import payload as P
from model import uncertainty as U

DRAWS = 24


@pytest.fixture(scope="module")
def release_book(book):
    """Книга выпусков такта: книга 1.4 без `forks.rederive` (решение ведущего по правилам: потолок такта 180 с;
    развилки с перевыводом — долгий тест `tests/test_book_14_forks.py` и сборка выпуска)."""
    B = copy.deepcopy(book)
    B["forks"].pop("rederive", None)
    return B


@pytest.fixture(scope="module")
def release(release_book, facts):
    try:
        return P.build_payload(book=release_book, facts=facts, fast=True, draws=DRAWS, n_workers=1)
    finally:
        U.close_pool()


def test_release_is_valid_and_complete(release):
    assert P.validate(release) == []
    assert release["schema"] == "yandex-v1" and list(release) == list(P.REQUIRED_TOP_LEVEL)
    assert release["meta"]["fast"] is True and release["headline"]["draws"] == DRAWS


def test_fields_read_by_the_hub_stay_in_place(release):
    """Хаб и сводка состояния читают поля образца (обзор общих мест, раздел 2)."""
    m, h = release["meta"], release["headline"]
    for key in ("generated_at", "valuation_date", "facts_date", "book_version", "anchor_period", "open_period",
                "payload_sha256", "bytes", "engine_commit"):
        assert key in m, key
    for key in ("central", "printed_central", "band", "printed_band", "inner", "p_central_below_market",
                "market_price"):
        assert key in h, key
    assert {"price", "price_date", "price_status"} <= set(release["market"])
    assert release["book"]["version"] == m["book_version"]
    assert all("raised" in f for f in release["checks"]["flags"])
    assert all("fired" in g for g in release["checks"]["gates"])
    assert "closing" in release["next_report"] and "events" in release["calendar"]
    assert set(release["fair_value"]["by_world"]) == set(release["worlds"]["order"])


def test_family_header_is_valid_and_sealed_after_it(release, book_date):
    """Шапка семейства `family-header-v1` (common.header, ops/common_adapter.py): по схеме слоя; прежние блоки и числа
    на месте; хэш и размер — после шапки, время сборки шапки в хэш не входит; закрывающий отчёт — из календаря."""
    from common import header as H
    from ops import common_adapter

    release = copy.deepcopy(release)                     # выпуск модуля общий: шапку ставим на копию
    head = H.build(common_adapter.header_inputs(release, today=book_date.isoformat()))
    assert H.validate(head) == []
    assert head["headline"]["median"] == release["headline"]["central"]
    assert head["headline"]["point"] == release["fair_value"]["central"]
    assert head["facts"]["anchor_period"] == "2026H1" and head["report"]["last_fact_period"] == "2026H1"
    assert any(d["kind"] == "data" and "--period 2026H2" in d["action"] for d in head["deadlines"])
    out = {"schema": release["schema"], "header": head, **{k: v for k, v in release.items() if k != "schema"}}
    out["meta"] = dict(release["meta"])
    P.seal(out)
    assert P.validate(out) == [] and out["meta"]["payload_sha256"] != release["meta"]["payload_sha256"]
    assert {k: v for k, v in out.items() if k not in ("header", "meta")} == \
        {k: v for k, v in release.items() if k != "meta"}
    moved = dict(out, header={**head, "release": {**head["release"], "generated_at": "2031-01-01T00:00:00Z"}})
    assert P.content_digest(moved) == out["meta"]["payload_sha256"]
    assert release["next_report"]["closing"]["date"] == "2027-02-17"          # отчёт за 2П26, а не дивиденд
    # Отказ наблюдаемого живого входа — флаг warn в шапке (жёлтый статус), облигации и аналоги — нет (аудит 422).
    assert not any(f["name"] == "live_inputs" for f in head["flags"])
    bad = copy.deepcopy(release)
    bad["live"]["errors"] = {"curve": "HTTP 503", "bonds": "нет", "peer_X": "нет"}
    hb = H.build(common_adapter.header_inputs(bad, today=book_date.isoformat()))
    assert H.validate(hb) == [] and hb["status"]["level"] in ("yellow", "red")
    assert [f["title"] for f in hb["flags"] if f["name"] == "live_inputs"] ==         ["Часть живых входов не собрана: кривая ОФЗ"]


def test_point_is_the_grid_point_and_blocks_replace_areas(release, book, facts):
    from model.facts import core_facts
    from model.point import evaluate

    p = evaluate(book, core_facts(facts, book["meta"]["anchor_period"]))
    assert abs(release["fair_value"]["central"] - p["point"]) <= 0.005
    year = release["paths"]["annual"][1]
    assert set(year["blocks"]) == {"A", "B", "C", "D", "E", "F", "G", "X"}
    assert math.isclose(math.fsum(b["revenue"] for b in year["blocks"].values()), year["revenue"], rel_tol=1e-7)


def test_same_inputs_give_the_same_digest(release, release_book, facts):
    again = P.build_payload(book=release_book, facts=facts, fast=True, draws=DRAWS, n_workers=1)
    assert again["meta"]["payload_sha256"] == release["meta"]["payload_sha256"]


def test_tampered_headline_is_caught(release):
    bad = copy.deepcopy(release)
    bad["headline"]["central"] += 10
    assert any("заголовок" in p or "печать" in p or "payload_sha256" in p for p in P.validate(bad))


def test_axes_of_the_book_are_counted_or_named(release, book):
    u = release["uncertainty"]
    assert u["axes_count"] == len(book["valuation"]["uncertainty"]["axes"]) - len(u["skipped_axes"])


def test_changes_against_a_previous_release_sum_up(release, release_book, facts):
    nxt = P.build_payload(book=release_book, facts=facts, fast=True, draws=DRAWS, n_workers=1, previous=release)
    vs = nxt["changes"]["vs_previous"]
    assert vs["previous_sha"] == release["meta"]["payload_sha256"]
    assert math.isclose(math.fsum(r["rub"] for r in vs["rows"]), vs["total_rub"], abs_tol=1e-6)
