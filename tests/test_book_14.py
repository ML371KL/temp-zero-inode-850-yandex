# -*- coding: utf-8 -*-
"""Книга 1.4 (правка этапа 4): каждый ключ книги — своё правило методики (docs/MODEL.md §17, «Ключи книги 1.4»);
закреплённые числа книги 1.4; объяснение гейта капитала — на числах сборки; порчи из списка ошибок ниже порога, п. 4
(рабочий файл, раздел 4). Книга 1.3 без ключей 1.4 — бит в бит: `tests/test_book_13.py` (книга из коммита 469d33d)."""
from __future__ import annotations

import copy
import math
import re

import pytest

from model import checks as C
from model import core as CORE
from model import uncertainty as U
from model.book import load_book, path_value
from model.book_schema import KEYS_1_4, BookError, opt, validate_book
from model.core import Context, _cohort_da, _general_part, _tax_split, anchor_rest, land_da, run_cell
from model.facts import core_facts, load_facts, need, one_composition
from model.grid import evaluate
from model.rederive import Calendar, da_check, derive_world_weights, ssum
from tests.test_book_13 import BOOK_13, _git_book

POINT_14, MEDIAN_14 = 3204.2616360730817, 3009.9         # книга 1.4, малая полоса — 400 прогонов, семя книги
FLOW_14 = 12.745006662671289                             # разница потоков 9М26, млрд ₽
EFFECT = {"2024Q4": 4.3, "2025Q1": 8.7}                  # эффект смены внутригрупповых расчётов (§0.5)
LTM_PREV = ["2024Q4", "2025Q1", "2025Q2", "2025Q3"]


@pytest.fixture(scope="module")
def cf():
    return core_facts(load_facts())


@pytest.fixture(scope="module")
def book():
    return load_book()


@pytest.fixture(scope="module")
def grid(book, cf):
    return evaluate(book, cf)


def _drop(B: dict, *path: str) -> dict:
    B = copy.deepcopy(B)
    node = B
    for part in path[:-1]:
        node = node[part]
    node.pop(path[-1])
    return B


def _cell(B, cf, w="H", r="base", c="base"):
    return run_cell(Context(B, cf), w, r, c)


# ------------------------------------------------------------------ числа книги 1.4 и её ключи


def test_book_14_point_median_and_flow_are_pinned(book, cf, grid):
    """Книга 1.4: точка, медиана малой полосы и поток 9М26 (проверка решений 411: 3 021,53 − 10,1 − 1,0 − 0,5)."""
    assert abs(grid.point.central - POINT_14) < 1e-9
    b = U.band(book, cf, valuation_date=grid.ctx.v, market_price=grid.market_price, draws=U.small_draws(book),
               n_workers=1)
    assert abs(b.stats()["median"] - MEDIAN_14) < 1e-9
    assert abs(next(x for x in C.gate_calcs(grid) if x.name == "flow_9m26").value - FLOW_14) < 1e-6


def test_book_14_carries_every_key(book):
    assert book["meta"]["version"] == "1.4"
    for path in KEYS_1_4:
        assert opt(book, *path) is not None, path
    assert book["growth"]["A"]["intragroup_2025"] == EFFECT
    assert book["tax"]["general_blocks"] == ["G"] and book["capex"]["anchor_land"] == {"da_half": 0.118}
    assert book["valuation"]["options_recognized"] == 0.535
    assert book["checks"]["tax_2026_27"] == [0.208, 0.288] and book["checks"]["flow_9m26_claims"]["withheld_tax"] == 2.376
    ax = next(a for a in book["valuation"]["uncertainty"]["axes"] if a["key"] == "it_tax")
    assert ax["bounds_from"] == [None, "tax.general"]
    # выведенные числа книги 1.4 — как у проверки решений 411 (p32, «Выведенные числа»)
    assert abs(book["tax"]["it_share"] - 0.5044) < 5e-5
    assert [round(book["capex"]["kappa_levels"][c], 4) for c in ("low", "base", "high")] == [0.3211, 0.3462, 0.3713]
    assert round(book["capex"]["h1_dip"], 5) == 0.0176 and round(book["growth"]["A"]["y2027_real"], 5) == 0.02358


def test_unknown_values_and_missing_companions_are_refused(book):
    for path in KEYS_1_4:
        B = copy.deepcopy(book)
        node = B
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = "nope"
        with pytest.raises(BookError):
            validate_book(B)
    for drop, key in ((("capex", "anchor_rest"), "anchor_land"), (("capex", "season"), "gordon_dip"),
                      (("tax", "general_blocks"), "general_blocks_da"),
                      (("checks", "flow_9m26_bridge"), "flow_9m26_claims")):
        with pytest.raises(BookError, match=key):
            validate_book(_drop(book, *drop))
    B = copy.deepcopy(book)
    B["tax"]["general_blocks"] = ["Q"]
    with pytest.raises(BookError, match="general_blocks"):
        validate_book(B)


# ------------------------------------------------------------------ §0.5: факты одного состава


def test_facts_of_one_composition(book, cf):
    """§0.5: эффект вычитается из A (выручка, EBITDA), прибавляется к X (выручка), F и «прочим» F (EBITDA) во всех
    периодах, содержащих квартал; группа не меняется; повторно не прикладывается; без ключа — те же факты."""
    one = one_composition(book, cf)
    assert one_composition(book, one) is one
    assert one_composition(_drop(book, "growth", "A", "intragroup_2025"), cf) is cf
    for q, e in EFFECT.items():
        y, n = q[:4], int(q[5:])
        for p in (q, f"{y}H{1 if n <= 2 else 2}", y):
            assert one.rev["A"][p] == cf.rev["A"][p] - e and one.ebitda["A"][p] == cf.ebitda["A"][p] - e
            assert one.rev["X"][p] == cf.rev["X"][p] + e
            assert one.ebitda["F"][p] == cf.ebitda["F"][p] + e and one.ebitda["F_o"][p] == cf.ebitda["F_o"][p] + e
    for p in ("2025Q2", "2025H2", "2026H1", "2026Q3"):
        assert one.rev["A"][p] == cf.rev["A"][p]
    assert one.rev["group"] == cf.rev["group"] and one.ebitda["group"] == cf.ebitda["group"]
    # рост A 2027 г. — 12 мес. минус эффект (не чистое полугодие)
    assert math.isclose(ssum(one.rev["A"], LTM_PREV), ssum(cf.rev["A"], LTM_PREV) - sum(EFFECT.values()), abs_tol=1e-9)
    assert Context(book, cf).cf.composition and not Context(_drop(book, "growth", "A", "intragroup_2025"), cf).cf.composition


# ------------------------------------------------------------------ §4.11: банк по общей ставке


def test_general_blocks_tax(book, cf):
    """§4.11: τ̄ × max(0, base − pg) + τ_g × max(0, pg); pg — прибыль G без доли DA; без ключа — τ̄ × max(0, base)."""
    Eb = {"A": 50.0, "B": 10.0, "C": -5.0, "D": 3.0, "E": 1.0, "G": 8.0}
    Rb = {"A": 100.0, "B": 60.0, "C": 80.0, "D": 20.0, "E": 10.0, "G": 30.0}
    assert _general_part(book, Eb, Rb, -2.0, 15.0) == (8.0, 0.0)
    assert _tax_split(book, 0.15, 40.0, (8.0, 0.0), 12.0) == 0.15 * 32.0 + 0.25 * 8.0
    B = _drop(book, "tax", "general_blocks_da")
    e, sh = _general_part(B, Eb, Rb, -2.0, 15.0)
    assert e == 8.0 and math.isclose(sh, 30.0 / (100 + 60 + 20 + 10 + 30))
    B0 = _drop(_drop(book, "tax", "general_blocks_da"), "tax", "general_blocks")
    assert _general_part(B0, Eb, Rb, -2.0, 15.0) == (0.0, 0.0) and _tax_split(B0, 0.15, 40.0, (8.0, 0.0), 12.0) == 6.0
    x1, x0 = _cell(book, cf), _cell(B0, cf)
    assert all(t1 >= t0 for t1, t0 in zip(x1.group["tax"], x0.group["tax"]))     # банк по 25 % дороже


# ------------------------------------------------------------------ §4.9: веса заголовка, земля, сверка срока


def test_headline_weights(book, grid):
    pw = derive_world_weights(book)
    assert {w: round(v, 4) for w, v in pw.items()} == {"N": 0.175, "H": 0.225, "M": 0.6}
    lam = grid.point.lam
    w = C._cell_weights(grid)
    assert all(w[c.key] == lam * c.p["analytical"] + (1 - lam) * c.p["macro_neutral"] for c in grid.cells)
    w0 = C._cell_weights(evaluate(_drop(book, "joint", "expectation"), grid.ctx.cf))
    assert all(w0[c.key] == c.p["analytical"] for c in grid.cells)


def test_land_is_written_off_by_the_fact_to_the_end(book, cf):
    """§4.9, §6.3: L_0 вне остатка; DA_L = 0,118 за полугодие сквозным счётом, остаток — последним, дальше 0."""
    ctx = Context(book, cf)
    l0 = need(cf.F, f"balance.{cf.facts_date}.rou_land")
    assert math.isclose(anchor_rest(ctx), anchor_rest(Context(_drop(book, "capex", "anchor_land"), cf)) - l0)
    seq = [land_da(ctx, k) for k in range(0, 80)]
    n = int(l0 // 0.118)
    assert seq[0] == 0.0 and all(x == 0.118 for x in seq[1:n + 1])
    assert math.isclose(seq[n + 1], l0 - 0.118 * n) and all(x == 0.0 for x in seq[n + 2:])
    assert n + 1 > 21 + 1                                   # списание идёт и в когортах терминала (после 2036H2)
    n2 = int(round(2 * ctx.life))
    da1, cx = _cohort_da(ctx, 21)
    da0, _ = _cohort_da(Context(_drop(book, "capex", "anchor_land"), cf), 21)
    cx_all = dict(cx)
    cx_all.update({f"{y}H{h}": 10.0 for y in range(2026, 2038) for h in (1, 2)})
    cx_all[book["meta"]["anchor_period"]] = cx[book["meta"]["anchor_period"]]
    for k in (1, n2, n2 + 1, 21):
        assert math.isclose(da1(k, cx_all) - da0(k, cx_all), land_da(ctx, k) - (l0 / n2 if k <= n2 else 0.0),
                            abs_tol=1e-12)


@pytest.mark.parametrize("which", ["13", "14"])
def test_term_check_uses_the_core_da_of_2026h2(which, book, cf, monkeypatch):
    """Порча «сверка срока без остатка якоря» (ниже порога, п. 4): DA(2026H2) сверки = DA ядра k = 1 (остаток якоря и
    земля — те же)."""
    B = book if which == "14" else _git_book(BOOK_13)
    ctx = Context(B, cf)
    da, cx = _cohort_da(ctx, 1)
    land = opt(B, "capex", "anchor_land")
    chk = da_check(ctx.cf, Calendar(B["meta"]), ctx.life, True, None if land is None else land["da_half"])
    k9 = next(k for k in chk if k.startswith("9М"))
    assert math.isclose(chk[k9]["da_next"], da(1, cx), rel_tol=1e-12)
    bad = da_check(ctx.cf, Calendar(B["meta"]), ctx.life, False)
    assert not math.isclose(bad[k9]["da_next"], da(1, cx), rel_tol=1e-6)


# ------------------------------------------------------------------ §6.3: Гордон с провалом


def test_gordon_rows_carry_the_dip(book, cf):
    """§6.3: строки Гордона — капитал на линии κ (на 30.06 — ниже на δ); Σ_h (CX − DA)(T_h) = g × κ × R12(2036H2)."""
    x = _cell(book, cf)
    kc, dl, g = book["capex"]["kappa_levels"]["base"], book["capex"]["h1_dip"], x.g
    ro = x.group["revenue_xfs"]
    rt = [r * (1 + g) for r in ro[-2:]]
    t = x.terminal
    f0, f1, f2 = kc * (ro[-2] + ro[-1]), (kc - dl) * (rt[0] + ro[-1]), kc * (rt[0] + rt[1])
    assert math.isclose(t["capex"][0] - t["da"][0], f1 - f0, rel_tol=1e-9)
    assert math.isclose(t["capex"][1] - t["da"][1], f2 - f1, rel_tol=1e-9)
    x0 = _cell(_drop(book, "terminal", "gordon_dip"), cf)
    assert math.isclose(sum(x0.terminal["capex"]) - sum(x0.terminal["da"]),
                        sum(t["capex"]) - sum(t["da"]), rel_tol=1e-9)
    assert x0.terminal["capex"][0] != t["capex"][0]


# ------------------------------------------------------------------ §7: делитель и налог нетто-исполнения


def test_divisor_and_net_exercise_tax_with_the_recognised_part(book, cf, grid, monkeypatch):
    o, ov = need(cf.F, f"equity.{cf.facts_date}.options_outstanding"), cf.options_vested_mln
    od = ov + 0.535 * (o - ov)
    assert math.isclose(grid.ctx.n_eff, cf.shares_mln + 0.7 * od) and round(grid.ctx.n_eff, 1) == 398.3
    assert math.isclose(grid.ctx.bridge["net_exercise_tax"], 0.111 * od * book["meta"]["market_price"] / 1000)
    c0 = Context(_drop(book, "valuation", "options_recognized"), cf)
    assert round(c0.n_eff, 2) == 390.19
    assert next(t for t in C.traps(grid) if t["name"] == "sbc_once")["ok"]
    # порча: делитель ядра без признанной части — ловушка ловит
    monkeypatch.setattr(CORE, "options_of_divisor", lambda A, cf_: cf_.options_vested_mln)
    bad = evaluate(book, cf)
    assert not C.trap_sbc_once(bad)["ok"]


# ------------------------------------------------------------------ §9: граница оси IT


def test_it_axis_bound_follows_the_general_rate(book):
    ax = next(a for a in U.band_axes(book) if a.key == "it_tax")
    assert ax.bounds[1] == book["tax"]["general"] == 0.25
    B = copy.deepcopy(book)
    B["tax"]["general"] = 0.30                               # книга «ухудшения» (§13.3)
    ax30 = next(a for a in U.band_axes(B) if a.key == "it_tax")
    assert ax30.bounds[1] == 0.30 and ax30.value(0.9999) > 0.25


# ------------------------------------------------------------------ §13.2: гейты


def test_flow_9m26_without_claims_movements(book, cf, grid):
    """§13.2: налог к уплате 14,12 → 11,04, поступления по инвестициям в аренду 1,03, удержанный налог 2,38."""
    g1 = next(x for x in C.gate_calcs(grid) if x.name == "flow_9m26")
    g0 = next(x for x in C.gate_calcs(evaluate(_drop(book, "checks", "flow_9m26_claims"), cf)) if x.name == "flow_9m26")
    assert "налог к уплате 14,12 → 11,04" in g1.reason and "аренду 1,03" in g1.reason and "нетто-исполнения 2,38" in g1.reason
    np_d, lease = 17.314 - 3.194 - (69.599 * (1 - 50.644 / (14.073 + 50.644)) - 4.091), (2.47 + 2.003) - (2.466 + 0.974)
    assert math.isclose(g1.value, g0.value - (np_d - lease + 2.376), abs_tol=1e-6)


def test_roic_gate_one_season_and_general_part(book, cf, grid):
    """§13.2: IC конца — 30.06 последнего года; часть G к τ_g не переводится; ожидание — с весами заголовка."""
    g = next(x for x in C.gate_calcs(grid) if x.name == "roic_increment")
    assert "капитал одного сезона" in g.reason and "весами заголовка" in g.reason
    lo, hi = book["terminal"]["roic"] * 0.75, book["terminal"]["roic"] * 1.25
    assert lo <= g.value <= hi
    g0 = next(x for x in C.gate_calcs(evaluate(_drop(book, "checks", "roic_one_season"), cf)) if x.name == "roic_increment")
    assert g0.value != g.value


def test_tax_gate_norm_in_the_gate_form(grid):
    g = next(x for x in C.gate_calcs(grid) if x.name == "tax_rate_2026_27")
    assert g.corridor == (0.208, 0.288) and 0.208 <= g.value <= 0.288


def test_zero_weight_pass_only_for_capital_and_terminal_share(book, cf):
    """Порча «вес 0 у всех гейтов» (ниже порога, п. 4): у прочих гейтов клетки с весом 0 срабатывают по-прежнему."""
    B = copy.deepcopy(book)
    B["checks"]["capex_range"] = [0.0, 1e-4]
    gl = C.gates(evaluate(B, cf), explanations={})
    cx = next(x for x in gl if x.name == "capex_range")
    assert "M|stress|high" in cx.cells and cx.fired


def test_equity_explanation_numbers_are_those_of_the_build(grid):
    """Объяснение гейта капитала (§13.2, точка 3-2): клетки с капиталом ≤ 0 и их капитал, ближайшие к нулю клетки,
    V0/D слоёв — числа сборки книги 1.4; цена ограниченной ответственности — тест `test_book_14_forks.py`."""
    text = C.load_gate_explanations()["equity_cushion"]["explanation"].replace("−", "-")
    eq = {c.key: c.result.ev - c.result.claims["total"] for c in grid.cells}
    neg = {k: v for k, v in eq.items() if v <= 0}
    assert neg and all(f"{C._fmt(v)} млрд" in text for v in neg.values())
    words = {"stress": "стресс", "high": "высокие вложения", "base": "базовые вложения"}
    for k in sorted(eq, key=eq.get)[:3]:
        w, r, c = k.split("|")
        assert f"«мир {w}, {words[r]}, {words[c]}»" in text
        v = eq[k]
        assert (f"{C._fmt(v)} млрд" if v <= 0 else f"+{C._fmt(v)} млрд") in text
    vd = [L.v0_to_d for L in grid.layers.values()]
    assert f"({C._fmt(min(vd))}–{C._fmt(max(vd))})" in text and min(vd) > 3
    assert "0 ₽ точки" in text
    assert f"{C._fmt(MEDIAN_14)} →" in text


# ------------------------------------------------------------------ суждение 7: 11 % выручки группы — в разбросе


def test_company_capex_forecast_is_within_the_cells(book, grid):
    """Решение «Капвложения через κ»: прогноз компании 11 % выручки группы — в разбросе средних 2027–30 гг. клеток с
    весом по точке > 0 (p32: 8,2–11,4 %)."""
    lam = grid.point.lam
    sh = []
    for c in grid.cells:
        if lam * c.p["analytical"] + (1 - lam) * c.p["macro_neutral"] == 0:
            continue
        g, ys = c.result.group, range(2027, 2031)
        v = [sum(g["capex"][i] + g["lease_new"][i] for i, p in enumerate(c.result.periods) if int(p[:4]) == y)
             / sum(g["revenue"][i] for i, p in enumerate(c.result.periods) if int(p[:4]) == y) for y in ys]
        sh.append(sum(v) / 4)
    assert min(sh) < 0.11 < max(sh)
    assert round(min(sh), 3) == 0.082 and round(max(sh), 3) == 0.114


# ------------------------------------------------------------------ ловушка «одна аренда» — своя линия κ


def test_one_lease_trap_does_not_share_the_kappa_line(book, cf, monkeypatch):
    """Порча линии κ ядра (ниже порога, п. 4): ловушка считает линию сама и порчу ловит."""
    assert next(t for t in C.traps(evaluate(book, cf)) if t["name"] == "one_lease")["ok"]
    real = CORE.kappa_line
    monkeypatch.setattr(CORE, "kappa_line", lambda A, capex, k1, i: real(A, capex, k1, i) + 0.002)
    assert not C.trap_one_lease(evaluate(book, cf))["ok"]


# ------------------------------------------------------------------ §14: выпуск несёт правила капитала


def test_release_carries_capital_rules_and_bridge_labels(book, cf, grid):
    """Ниже порога, п. 3: блок капвложений выпуска — провал κ, доля I полугодия, остаток якоря, земля, D_b, веса;
    строки моста — словами; книга 1.3 — без поля `rules` и с прежними подписями."""
    from model.payload import capex_block, debt_block
    r = capex_block(book, grid.ctx)["rules"]
    assert (r["season"], r["kappa_target"], r["expectation"], r["anchor_rest"]) == ("h1_dip", "all_cells", "headline",
                                                                                    "cohort")
    assert r["h1_dip"] == book["capex"]["h1_dip"] and r["h1_share"] == book["capex"]["h1_share"]
    assert math.isclose(r["anchor_rest_bn"], anchor_rest(grid.ctx)) and 78.0 < r["anchor_rest_bn"] < 79.5
    assert r["anchor_land_bn"] == 8.618 and r["land_da_half"] == 0.118
    assert r["base_cell_capex"] == book["capex"]["kappa_base_cell_capex"] and 0.11 < r["base_cell_capex"] < 0.115
    rows = {x["key"]: x["label"] for x in debt_block(book, cf, grid)["bridge"]["rows_at_valuation"]}
    assert rows["bs_income_tax_payable"] == "налог на прибыль к уплате"
    assert rows["oca_income_tax_prepaid"] == "авансы по налогу на прибыль"
    assert rows["net_exercise_tax"] == "налог при нетто-исполнении опционов"
    b13 = _git_book(BOOK_13)
    g13 = evaluate(b13, cf)
    assert "rules" not in capex_block(b13, g13.ctx)
    rows13 = {x["key"]: x["label"] for x in debt_block(b13, cf, g13)["bridge"]["rows_at_valuation"]}
    assert rows13["net_exercise_tax"] == "net_exercise_tax"


# ------------------------------------------------------------------ §13.5: ключ `forks.rederive` на дешёвом пути (такт)


def test_forks_rederive_key_dispatches_the_rederivation(book, cf, grid, monkeypatch):
    """Ключ книги 1.4 читается: развилки стресса и e-com идут через перевывод с удержанием своих ключей (перевывод —
    заглушкой; настоящий — `tests/test_book_14_forks.py`, вне такта); без ключа перевывода нет."""
    calls = []

    def stub(B, cf_, held):
        calls.append(held)
        return B, "с перевыводом (заглушка)"

    monkeypatch.setattr(C, "_fork_rederive", stub)
    monkeypatch.setattr(C, "_FORK_CACHE", {})
    b = U.band(book, cf, draws=8, n_workers=1)
    fk = {f["name"]: f for f in C.forks(book, cf, grid, b, b.stats()["median"], n_workers=1)}
    assert calls[0] == (("joint", "regime_prob"),) and "заглушка" in fk["stress"]["detail"]
    ecom = (("margin", "center", "C"), ("margin", "targets", "C"), ("margin", "range", "C"))
    assert calls[1:] == [ecom, ecom] and "заглушка" in fk["ecom_margin"]["detail"]
    calls.clear()
    C.forks(_drop(book, "forks", "rederive"), cf, grid, b, b.stats()["median"], n_workers=1)
    assert calls == []


# ------------------------------------------------------------------ приёмка 414: места вызова и удержание ключей


class _Seen(Exception):
    pass


@pytest.mark.parametrize("which", ["13", "14"])
def test_derive_passes_rest_and_land_to_the_term_check(which, book, cf, monkeypatch):
    """Порча 403 «сверка срока без остатка якоря» и её двойник «без земли» — в месте вызова (`derive`)."""
    import model.rederive as R
    B = book if which == "14" else _git_book(BOOK_13)
    seen = {}

    def spy(cf_, cal, life, rest=False, land=None):
        seen["args"] = (rest, land)
        raise _Seen

    monkeypatch.setattr(R, "da_check", spy)
    with pytest.raises(_Seen):
        R.derive(B, cf)
    land = opt(B, "capex", "anchor_land")
    assert seen["args"] == (True, None if land is None else float(land["da_half"]))


def test_fork_rederivation_holds_the_variant_keys(book, cf, monkeypatch):
    """§13.5: перевывод развилки не трогает её ключи (перевывод — заглушкой, дёшево; настоящий — test_book_14_forks)."""
    import model.rederive as R

    def fake(B, cf_):
        dip = float(B["capex"]["h1_dip"])
        return ({("joint", "regime_prob", "stress"): 0.0, ("capex", "h1_dip"): round(min(dip + 0.001, 0.0196), 10)},
                [], [], {})

    monkeypatch.setattr(R, "derive", fake)
    B = copy.deepcopy(book)
    B["joint"]["regime_prob"]["stress"] = 0.4
    out, note = C._fork_rederive(B, cf, (("joint", "regime_prob"),))
    assert out["joint"]["regime_prob"]["stress"] == 0.4 and out["capex"]["h1_dip"] == 0.0196
    assert "1 выведенных чисел изменилось" in note


# ------------------------------------------------------------------ §8, §13.2: EBITDA 2027 г. — на весах заголовка


def test_ebitda_2027_of_the_broker_gate_and_ev_ebitda_are_on_headline_weights(book, cf, grid):
    """Решение ведущего по приёмке 414: гейт брокеров и знаменатель EV/EBITDA 2027 г. точки — E_H(2027) = λ × E_свой +
    (1 − λ) × E_нейтр, как V0 точки; без ключа `joint.expectation` — слой «свой взгляд» (книга 1.3). Пара выпуска
    «модель — рынок» `fair_value.center_ev` — на той же базе, что знаменатель точки (одно правило в одной форме)."""
    from model import point as PT
    lam, an, mn = grid.point.lam, grid.layers["analytical"], grid.layers["macro_neutral"]
    e_h = lam * an.ebitda_2027 + (1 - lam) * mn.ebitda_2027
    gc = {x.name: x for x in C.gate_calcs(grid)}
    assert gc["broker_ebitda_2027"].value == e_h and round(e_h, 1) == 394.4
    assert gc["ev_ebitda_2027"].value == grid.point.v0_point / e_h and round(gc["ev_ebitda_2027"].value, 3) == 3.891
    assert PT.evaluate(book, cf)["ev_ebitda_2027_point"] == gc["ev_ebitda_2027"].value
    assert next(g for g in C.gates(grid) if g.name == "broker_ebitda_2027").title.startswith("EBITDA второго года (веса")
    g0 = evaluate(_drop(book, "joint", "expectation"), cf)
    gc0 = {x.name: x for x in C.gate_calcs(g0)}
    assert gc0["broker_ebitda_2027"].value == g0.layers["analytical"].ebitda_2027
    assert gc0["ev_ebitda_2027"].value == g0.point.v0_point / g0.layers["analytical"].ebitda_2027
    for B, g, e27 in ((book, grid, e_h), (_drop(book, "joint", "expectation"), g0, g0.layers["analytical"].ebitda_2027)):
        ce = _center_ev(B, g)
        assert ce["ebitda_2027"] == e27 and ce["ev_ebitda_2027_point"] == g.point.ev_ebitda_2027_point
        assert ce["ev_ebitda_2027_point"] == ce["v0_point"] / e27
        assert ce["ev_ebitda_2027_market"] == ce["v_star"] / e27 and ce["ev_ebitda_2027_median"] == ce["v0_median"] / e27


def _center_ev(B, g) -> dict:
    """Блок выпуска `fair_value.center_ev` на коротких рядах прогонов (база пары от прогонов не зависит)."""
    from model.payload import fair_value_block
    lo, hi = [2900.0, 3000.0, 3100.0], [3000.0, 3100.0, 3200.0]
    st = U.band_stats(lo, hi, g.point.lam, g.market_price)
    return fair_value_block(B, g, st, lo, hi, g.market_price)["center_ev"]


def test_path_value_converges_to_lt_by_lt_from(book):
    """§0.1, п. 4: после последнего года — линейный сход к LT к году LT_from, `LT` без `LT_from` — сразу. На книге 1.4
    ветка схода недостижима: `LT_from` есть только у `tax.it_rate` и равен последнему году + 1. Здесь — та же
    траектория книги с `LT_from` 2033, где сход достижим."""
    spec = dict(book["tax"]["it_rate"])
    last, lt = spec["2030"], spec["LT"]
    assert (last, lt, spec["LT_from"]) == (0.05, 0.15, 2031)
    assert path_value(spec, "2030H2") == last and path_value(spec, "2031H1") == lt
    spec["LT_from"] = 2033
    for p, k in (("2030H2", 0), ("2031H1", 1), ("2031H2", 1), ("2032H1", 2), ("2033H1", 3), ("2040H2", 3)):
        assert path_value(spec, p) == pytest.approx(last + (lt - last) * k / 3, rel=1e-15), p
    del spec["LT_from"]
    assert path_value(spec, "2031H1") == lt
