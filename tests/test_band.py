"""Сетка, слои, точка и полоса на ядре сервисов (docs/MODEL.md §8, §9, §11; задача плана 5б)."""

from __future__ import annotations

import math

import pytest

from model import grid as G
from model import point as P
from model import uncertainty as U


@pytest.fixture(scope="module")
def cf(book, facts):
    from model.facts import core_facts

    return core_facts(facts, book["meta"]["anchor_period"])


@pytest.fixture(scope="module")
def g(book, cf):
    return G.evaluate(book, cf)


def test_grid_point_is_model_point_bit_for_bit(book, cf, g):
    p = P.evaluate(book, cf)
    assert (g.point.central, g.point.low, g.point.high) == (p["point"], p["low"], p["high"])
    assert g.point.v0_point == p["v0_point"] and g.point.ev_ebitda_2027_point == p["ev_ebitda_2027_point"]
    for name in G.LAYERS:
        assert g.layers[name].v0 == p["layers"][name]["v0"] and g.layers[name].price == p["layers"][name]["price"]
    assert all(g.worlds_only[w].v0 == p["worlds"][w]["v0"] for w in "NHM")


def test_band_run_prices_are_the_grid_layers(book, cf, g):
    assert G.layer_prices(book, cf) == (g.point.low, g.point.high)


def test_grid_invariants_hold(g):
    inv = G.invariants(g)
    assert {i["name"] for i in inv} >= {"probabilities", "group_identity", "fcff_identity", "ev_identity"}
    assert all(i["ok"] for i in inv), [i for i in inv if not i["ok"]]


def test_layer_probabilities_and_ev_parts(g):
    for name in G.LAYERS:
        assert math.isclose(math.fsum(c.p[name] for c in g.cells), 1.0, abs_tol=1e-12)
        L = g.layers[name]
        assert math.isclose(math.fsum(v for _, v in L.ev_parts), L.v0, rel_tol=1e-12)


def test_expected_path_sums_blocks_to_group(g):
    halves = G.expected_path(g)
    assert [h["period"] for h in halves] == list(g.ctx.P)
    for h in halves:
        assert math.isclose(math.fsum(b["revenue"] for b in h["blocks"].values()), h["revenue"], rel_tol=1e-12)
    years = G.annual_path(g, halves)
    assert years[0]["year"] == 2026 and years[0]["fact"]          # 2026 = факт 1П + прогноз 2П


def test_axes_follow_section_9_and_take_ranges_from_the_book(book):
    axes = U.band_axes(book)
    assert [a.key for a in axes] == ["at_expense", "sbc", "it_tax", "terminal_growth", "governance", "ma",
                                     "attack", "net_exercise"]
    assert [a.key for a in U.applied(axes)] == [a.key for a in axes]         # ось 6 — ключ f.at.position
    sbc = axes[1]
    off = book["sbc"]["range_offset"]                  # книга 1.2 (§9): края — сдвиги от центра
    assert (sbc.book, [sbc.low, sbc.high]) == (book["sbc"]["share"], [book["sbc"]["share"] + off[0],
                                                                       book["sbc"]["share"] + off[1]])


def test_two_piece_normal_hits_median_and_p10_p90(book):
    for ax in U.applied(U.band_axes(book)):
        assert math.isclose(ax.value(0.5), ax.book, abs_tol=1e-15)
        lo, hi = ax.bounds
        if lo is None or ax.low > lo:
            assert math.isclose(ax.value(0.1), ax.low, rel_tol=1e-9, abs_tol=1e-12)
        if hi is None or ax.high < hi:
            assert math.isclose(ax.value(0.9), ax.high, rel_tol=1e-9, abs_tol=1e-12)
    attack = next(a for a in U.band_axes(book) if a.key == "attack")
    assert attack.value(0.99) == 0.0                                       # ущерб не выше 0


def test_latin_hypercube_is_exactly_section_9():
    u = U.lhs(10, 3, 7)
    for j in range(3):
        assert sorted(int(row[j] * 10) for row in u) == list(range(10))   # по одной точке в страте
    assert U.lhs(10, 3, 7) == u


def test_band_does_not_depend_on_the_number_of_processes(book, cf):
    one = U.band(book, cf, draws=24, n_workers=1)
    two = U.band(book, cf, draws=24, n_workers=2, min_parallel=1)
    assert (one.low, one.high) == (two.low, two.high)
    U.close_pool()


def test_band_headline_is_quantile_of_rounded_draws(book, cf):
    b = U.band(book, cf, draws=30, n_workers=1)
    low, high = b.rounded()
    c = sorted(a + b.lam * (h - a) for a, h in zip(low, high))
    assert b.stats()["median"] == U.quantile7(c, 0.5)
    assert len(b.u[0]) == len(book["valuation"]["uncertainty"]["axes"])   # столбец каждой оси книги разыгран
    shares = [c["share"] for c in b.contributions()]
    assert math.isclose(math.fsum(shares), 1.0, rel_tol=1e-12)


def test_bisection_and_secant_find_a_root():
    root, calls, slope = U.bisect(lambda x: 10 * x - 3, 0.0, 1.0, 0.01)
    assert abs(10 * root - 3) <= 0.01 and slope and calls <= 2 + U.BISECTION_STEPS
    assert U.bisect(lambda x: x + 5, 0.0, 1.0, 0.01)[0] is None          # нет смены знака — недостижимо
    best, fx, calls = U.secant_refine(lambda x: x * x - 2, 1.5, 3.0, 0.0, 3.0, 1e-9)
    assert abs(best - math.sqrt(2)) < 1e-3 and abs(fx) < 0.25 and calls <= 1 + U.SECANT_STEPS   # ≤ 2 шагов


def test_reverse_dcf_point_search_reaches_the_market(book, cf):
    b = U.band(book, cf, draws=8, n_workers=1)
    sub = U.Subsample(b, b.stats()["median"], cf, size=4, n_workers=1)
    rows = U.reverse_dcf(book, cf, sub, axes=[U.reverse_axes(book)[0]], point_only=True)
    r = rows[0]
    assert r["point_status"] == "solved" and r["status"] == "skipped"
    got = U.point_price(U.at_value(book, U.applied(U.band_axes(book))[0], r["point_solved"]), cf, None, b.lam)
    assert abs(got - b.market_price) <= U.REVERSE_TOL_RUB


def test_quick_and_band_commands_print_the_numbers(tmp_path, capsys):
    from model import book_results, quick
    from ops.common_adapter import COMMANDS

    assert all(not isinstance(COMMANDS[k], str) for k in ("quick", "band", "release"))   # подключены
    assert quick.main(["--draws", "20", "--workers", "1"]) == 0
    out = capsys.readouterr().out
    assert "медиана (заголовок)" in out and "точка" in out and out.strip().splitlines()[-1].startswith("ИТОГ: медиана")
    assert book_results.main(["--draws", "20", "--workers", "1", "--no-reverse", "--no-axis-prices",
                              "--out", str(tmp_path)]) == 0
    assert (tmp_path / "results.json").exists() and (tmp_path / "run_output.txt").exists()
    assert "ИТОГ: медиана" in capsys.readouterr().out
