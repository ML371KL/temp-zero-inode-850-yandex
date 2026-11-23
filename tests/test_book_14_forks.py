# -*- coding: utf-8 -*-
"""Книга 1.4, долгие тесты (вне такта): развилки с перевыводом (§13.5, ключ `forks.rederive`) и цена ограниченной
ответственности в объяснении гейта капитала (§13.2) — на малой полосе."""
from __future__ import annotations

import pytest

from model import checks as C
from model import uncertainty as U
from model.book import load_book, override
from model.book_results import limited_liability
from model.book_schema import REGIMES
from model.facts import core_facts, load_facts
from model.grid import evaluate

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def cf():
    return core_facts(load_facts())


@pytest.fixture(scope="module")
def book():
    return load_book()


def test_stress_fork_rederives_and_holds_its_keys(book, cf):
    """Стресс 40 %: уровни κ и δ выводятся заново по ожиданию клеток со стрессом; веса режимов — варианта."""
    w = float(book["forks"]["stress_weight"])
    probs = {r: (w if r == "stress" else (1.0 - w) / (len(REGIMES) - 1)) for r in REGIMES}
    B, note = C._fork_book(override(book, ["joint.regime_prob"], "dict", probs), cf, (("joint", "regime_prob"),))
    assert B["joint"]["regime_prob"] == probs and "с перевыводом" in note
    k = B["capex"]["kappa_levels"]
    assert (round(k["base"], 4), round(k["high"], 4), round(B["capex"]["h1_dip"], 4)) == (0.3517, 0.3824, 0.0179)
    assert k["low"] == book["capex"]["kappa_levels"]["low"]
    B0, note0 = C._fork_book(override({**book, "forks": {"stress_weight": w}}, ["joint.regime_prob"], "dict", probs),
                             cf, (("joint", "regime_prob"),))
    assert note0 == "" and B0["capex"]["kappa_levels"] == book["capex"]["kappa_levels"]


def test_ecom_fork_keeps_its_centre(book, cf):
    """e-com: центр C — выведенное число; перевывод его не трогает, остальное не меняется (κ от маржи не зависит)."""
    x = 0.0
    c0 = float(book["margin"]["center"]["C"])
    B = override(book, ["margin.center.C"] + [f"margin.targets.C.{r}" for r in REGIMES], "shift", x - c0)
    B2, note = C._fork_book(B, cf, (("margin", "center", "C"), ("margin", "targets", "C"), ("margin", "range", "C")))
    assert B2["margin"]["center"]["C"] == x and "(0 выведенных чисел изменилось)" in note


def test_limited_liability_price_in_the_explanation(book, cf):
    """Цена ограниченной ответственности на числах книги 1.4: точка 0, медиана малой полосы +16,2 (3 009,9 → 3 026,1)."""
    g = evaluate(book, cf)
    b = U.band(book, cf, valuation_date=g.ctx.v, market_price=g.market_price, draws=U.small_draws(book), n_workers=1)
    ll = limited_liability(book, cf, g, b)
    text = C.load_gate_explanations()["equity_cushion"]["explanation"]
    assert abs(ll["point_shift"]) < 1e-6 and "0 ₽ точки" in text
    assert f"+{C._fmt(ll['median_shift'])} ₽ медианы малой полосы" in text
    assert f"{C._fmt(b.stats()['median'])} → {C._fmt(ll['median'])}" in text
