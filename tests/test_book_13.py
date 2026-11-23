# -*- coding: utf-8 -*-
"""Книга 1.3 (правка этапа 3-2): каждый ключ книги — своё правило методики (docs/MODEL.md §17, «Ключи книги 1.3»);
без ключей — книга 1.2 бит в бит; закреплённые числа книги 1.3; порчи общего кода сектора из приёмки 303
(рабочий файл, раздел 4, ошибки ниже порога, п. 2). Книга 1.3 — из коммита 469d33d: с ключами книги 1.4 тот же код
считает её бит в бит."""
from __future__ import annotations

import copy
import math

import pytest

from model import checks as C
from model import point as PT
from model import uncertainty as U
from model.book_schema import KEYS_1_3, opt, validate_book
from model.core import Context, _cohort_da, anchor_rest, kappa_line, run_cell
from model.facts import core_facts, load_facts, need
from model.grid import evaluate
from model.payload import capex_block
from tests.test_book_12 import BOOK_11, BOOK_12, _git_book

BOOK_13 = "469d33d"                                         # коммит книги 1.3 (main до правки этапа 4)
POINT_12, MEDIAN_12 = 3188.1237296550626, 2981.575          # книга 1.2 (c150e62), малая полоса — 400, семя книги
POINT_13, MEDIAN_13 = 3101.473848133197, 2906.875           # книга 1.3 (правка этапа 3-2)
FLOW_12, FLOW_13 = 14.674944670, 16.937438089               # разница потоков 9М26, млрд ₽
LTM = ["2025Q4", "2026Q1", "2026Q2", "2026Q3"]


@pytest.fixture(scope="module")
def cf():
    return core_facts(load_facts())


@pytest.fixture(scope="module")
def book():
    """Книга 1.3 из её коммита (живая книга — 1.4, тесты `tests/test_book_14.py`)."""
    return _git_book(BOOK_13)


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


# ------------------------------------------------------------------ без ключей — книга 1.2 бит в бит; числа 1.3


def test_book_12_point_median_and_flow_bit_for_bit(cf):
    """Книга 1.2 (c150e62) без ключей книги 1.3: точка, медиана малой полосы и поток 9М26 — прежние числа."""
    A = _git_book(BOOK_12)
    validate_book(A)
    g = evaluate(A, cf)
    assert g.point.central == POINT_12
    b = U.band(A, cf, valuation_date=g.ctx.v, market_price=g.market_price, draws=U.small_draws(A), n_workers=1)
    assert b.stats()["median"] == MEDIAN_12
    assert abs(next(x for x in C.gate_calcs(g) if x.name == "flow_9m26").value - FLOW_12) < 1e-6


def test_book_13_point_median_and_flow_are_pinned(book, cf, grid):
    assert abs(grid.point.central - POINT_13) < 1e-9
    b = U.band(book, cf, valuation_date=grid.ctx.v, market_price=grid.market_price, draws=U.small_draws(book),
               n_workers=1)
    assert b.stats()["median"] == MEDIAN_13
    assert abs(next(x for x in C.gate_calcs(grid) if x.name == "flow_9m26").value - FLOW_13) < 1e-6


def test_book_13_carries_every_key(book):
    assert book["meta"]["version"] == "1.3"
    for path in KEYS_1_3:
        assert opt(book, *path) is not None, path
    assert opt(book, "tax", "base_rule") is None                         # Г6: база налога книги 1.1
    assert book["checks"]["tax_2026_27"] == [0.202, 0.282]               # Г5
    assert book["nwc"]["income_tax_place"] == "bridge"                   # Г8
    for k in ("it_contrib_4q25",):
        assert k in book["margin"]
    assert "onca_net_lease_investment" in [x["fact"] for x in book["bridge"]["include"]]


def test_unknown_values_of_the_keys_are_refused(book):
    from model.book_schema import BookError
    for path in KEYS_1_3:
        B = copy.deepcopy(book)
        node = B
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = "nope"
        with pytest.raises(BookError):
            validate_book(B)


# ------------------------------------------------------------------ Г1: остаток капитала якоря


def test_anchor_rest_is_written_off_as_a_cohort(book, cf):
    """§4.9 (Г1): REST = FA(якорь) − DA_якоря × Σ S(k)/S(0) − CX(якорь) (≈ 87,3 млрд) списывается долями 1/(2L) в
    2L полугодиях с 2026H2; без ключа DA та же без слагаемого."""
    ctx = Context(book, cf)
    rest = anchor_rest(ctx)
    assert 87.0 < rest < 87.6
    n2 = int(round(2 * ctx.life))
    da_on, cx = _cohort_da(ctx, 21)
    da_off, _ = _cohort_da(Context(_drop(book, "capex", "anchor_rest"), cf), 21)
    cx_all = dict(cx)
    cx_all.update({f"{y}H{h}": 10.0 for y in range(2026, 2038) for h in (1, 2)})
    cx_all[book["meta"]["anchor_period"]] = cx[book["meta"]["anchor_period"]]
    diffs = [da_on(k, cx_all) - da_off(k, cx_all) for k in range(1, n2 + 3)]
    assert all(abs(d - rest / n2) < 1e-12 for d in diffs[:n2]) and diffs[n2:] == [0.0, 0.0]
    # капитал якоря списан целиком: форма выбывания + когорта якоря + остаток = FA(якорь)
    hist, ia = ctx._cx_hist, None
    from model.book import period_index
    from model.core import _pname
    ia = period_index(book["meta"]["anchor_period"])
    S = [math.fsum(hist[_pname(ia - j)] for j in range(1, n2 - k + 1)) for k in range(n2)]
    runoff = math.fsum(cf.da[book["meta"]["anchor_period"]] * S[k] / S[0] for k in range(1, n2))
    assert abs(runoff + hist[book["meta"]["anchor_period"]] + rest - cf.fa_anchor) < 1e-9


# ------------------------------------------------------------------ Г2: провал κ на 30.06


def test_h1_dip_lowers_kappa_on_30_06_only(book, cf):
    """§4.9 (Г2): цель κ полугодий I — линия − δ; II — линия; после 2030 г. FA / R12 = κ_C − δ на 30.06."""
    C_ = book["capex"]
    dip = float(C_["h1_dip"])
    assert 0.0 < dip < 0.05
    B0 = _drop(_drop(_drop(book, "capex", "season"), "capex", "h1_dip"), "capex", "h1_share")
    for i in range(1, 21):
        d = kappa_line(B0, "base", 0.3, i) - kappa_line(book, "base", 0.3, i)
        assert d == (dip if i % 2 == 1 else 0.0) or abs(d - (dip if i % 2 == 1 else 0.0)) < 1e-15


def test_h1_share_of_capex_equals_the_target(book, cf):
    """§4.9, §17 (Г2): средняя за 2027–2030 доля I полугодия во вложениях CX клеток (W, base, base) с весами слоя
    «свой взгляд» = `capex.h1_share`; цель — среднее фактов 2024, 2025 и 2026 г. (2026H2 — ядро)."""
    from model.book_schema import WORLDS
    pw = {w: float(book["joint"]["world_prob"][w]) for w in WORLDS}
    ctx = Context(book, cf)
    h1, yr, h2_26 = {}, {}, 0.0
    for w in WORLDS:
        x = run_cell(ctx, w, "base", "base")
        h2_26 += pw[w] * (x.group["capex"][0] + x.group["lease_new"][0])
        for i, p in enumerate(x.periods):
            y = int(p[:4])
            if 2027 <= y <= 2030:
                c = x.group["capex"][i] + x.group["lease_new"][i]
                yr[y] = yr.get(y, 0.0) + pw[w] * c
                if p.endswith("H1"):
                    h1[y] = h1.get(y, 0.0) + pw[w] * c
    got = math.fsum(h1[y] / yr[y] for y in yr) / 4

    def cxf(p):
        return cf.capex[p] + cf.lease_add[p]
    facts = [cxf(f"{y}H1") / (cxf(f"{y}H1") + cxf(f"{y}H2")) for y in (2024, 2025)]
    facts.append(cxf("2026H1") / (cxf("2026H1") + h2_26))
    assert abs(book["capex"]["h1_share"] - math.fsum(facts) / 3) < 1e-9
    assert abs(got - book["capex"]["h1_share"]) < 1e-8


def test_terminal_cohorts_carry_the_dip(book, cf):
    """§6.3 (Г2): терминал когортами с провалом κ на 30.06 — TV меняется; без ключа — книга без провала."""
    x1 = _cell(book, cf)
    x0 = _cell(_drop(_drop(_drop(book, "capex", "season"), "capex", "h1_dip"), "capex", "h1_share"), cf)
    assert abs(x1.tv - x0.tv) > 1e-6


# ------------------------------------------------------------------ Г4, Г7: гейт отдачи и отдача окна


def test_window_roic_is_at_the_general_rate_with_the_it_contribution(book, cf):
    """§17 `terminal.roic` (Г4, Г7): NOPAT 12 мес. = E − x − SBC − DA − τ_g × max(0, база §4.11 с поправкой блоков A,
    B, F на доли x) / FA якоря."""
    from model.rederive import tax_base
    itc = book["margin"]["it_contrib_4q25"]
    x = float(itc["amount"])
    adj = {b: x * float(itc[b]) for b in ("A", "B", "F_o")}
    E12 = math.fsum(cf.ebitda["group"][q] for q in LTM)
    sbc = math.fsum(cf.sbc[q] for q in LTM)
    da = math.fsum(cf.da[q] for q in LTM)
    tg = float(book["tax"]["general"])
    nop = E12 - x - sbc - da - tg * max(0.0, tax_base(cf, LTM, None, adj))
    assert abs(book["terminal"]["roic"] - nop / cf.fa_anchor) < 1e-9
    # поправка базы: A и B прибыльны — база ниже на x_A + x_B; F убыточен — без изменений
    assert abs(tax_base(cf, LTM) - tax_base(cf, LTM, None, adj) - adj["A"] - adj["B"]) < 1e-9


def test_margin_centres_and_other_share_without_the_4q25_contribution(book, cf):
    """§4.4 (Г7): центры A, B и доля «прочих» F — «12 мес.» минус доли добавки взносов IT 4К25."""
    itc = book["margin"]["it_contrib_4q25"]
    x = float(itc["amount"])

    def s(series):
        return math.fsum(series[q] for q in LTM)
    assert abs(book["margin"]["center"]["A"] - (s(cf.ebitda["A"]) - x * itc["A"]) / s(cf.rev["A"])) < 1e-9
    assert abs(book["margin"]["center"]["B"] - (s(cf.ebitda["B"]) - x * itc["B"]) / s(cf.rev["B"])) < 1e-9
    assert abs(book["f"]["other_share"] - (s(cf.ebitda["F_o"]) - x * itc["F_o"]) / s(cf.rev["group"])) < 1e-9


def test_roic_gate_without_the_key_uses_the_cell_rates(book, cf, grid):
    """§13.2 (Г4): с ключом NOPAT клеток — по τ_g; без ключа — NOPAT ядра (ставка ячейки) — значения различаются."""
    g1 = next(g for g in C.gate_calcs(grid) if g.name == "roic_increment")
    g0 = next(g for g in C.gate_calcs(evaluate(_drop(book, "checks", "roic_tax"), cf)) if g.name == "roic_increment")
    assert abs(g1.value - g0.value) > 1e-4 and "по общей ставке" in g1.reason and "общей ставке" not in g0.reason
    assert not g1.hits


def test_it_axis_is_capped_at_the_general_rate(book):
    """§9, §11 (Г4): ось налога IT после 2030 г. и окно обратного DCF — не выше 25 %."""
    ax = next(a for a in U.band_axes(book) if a.key == "it_tax")
    assert ax.bounds[1] == book["tax"]["general"] and ax.value(0.999) == 0.25
    rv = next(a for a in U.reverse_axes(book) if a["key"] == "it_tax")
    assert rv["search"][1] <= 0.25


# ------------------------------------------------------------------ Г8: мост и капитал банка


def test_bridge_claims_of_book_13(book, cf, grid):
    cl = next(c for c in grid.cells if c.key == "H|base|base").result.claims
    nxt = 0.111 * cf.options_vested_mln * book["meta"]["market_price"] / 1000.0
    assert abs(cl["bridge.net_exercise_tax"] - nxt) < 1e-12 and abs(nxt - 4.837) < 0.01
    assert abs(cl["bridge.onca_net_lease_investment"] + 0.974) < 1e-12
    payable = need(cf.F, "balance.2026-06-30.bs_income_tax_payable")
    prepaid = need(cf.F, "balance.2026-06-30.oca_income_tax_prepaid")
    assert abs(cl["bridge.bs_income_tax_payable"] - payable) < 1e-12
    assert abs(cl["bridge.oca_income_tax_prepaid"] + prepaid) < 1e-12 and abs(payable - prepaid - 9.98) < 0.01


def test_bank_capital_increment_of_2026h2_is_from_the_fact(book, cf):
    """§4.6 (Г8): ΔKb(2026H2) = κ × (R_G(2026H2) + R_G(якорь)) − K_bank,факт(якорь); дальше — правило."""
    x1, x0 = _cell(book, cf), _cell(_drop(book, "bank", "anchor_capital"), cf)
    k = float(book["bank"]["capital_to_revenue"])
    rg = x1.blocks["G"]["revenue"][0] + cf.rev["G"][book["meta"]["anchor_period"]]
    assert abs(x1.group["dkb"][0] - (k * rg - cf.bank_capital[cf.facts_date])) < 1e-12
    assert x1.group["dkb"][1:] == x0.group["dkb"][1:] and x1.group["dkb"][0] != x0.group["dkb"][0]


# ------------------------------------------------------------------ Г9, Г11: проверки


def test_flow_9m26_without_bridge_asset_flows(book, cf, grid):
    """§13.2 (Г9): F_факт без потоков фин. вложений, займов и курсовой разницы; исключённые строки ПОА на 30.09 — по
    движению денег III кв."""
    g1 = C.flow_9m26(book, cf, grid)
    g0 = C.flow_9m26(_drop(book, "checks", "flow_9m26_bridge"), cf, grid)

    def q(k, p):
        return need(cf.F, f"group.cash_2026.{k}.{p}")
    qs = ["2026Q1", "2026Q2", "2026Q3"]
    fin = {p: q("fin_assets_disposed", p) + q("fin_assets_purchased", p) for p in qs}
    loan = {p: q("loans_repaid", p) + q("loans_issued", p) for p in qs}
    flows = math.fsum(fin.values()) + math.fsum(loan.values()) + math.fsum(q("fx_cash", p) for p in qs)
    assert abs(flows - (10.053 + 0.924 + 1.225)) < 1e-9
    _est, sh = C.nwc_quarter_estimate(book, cf, "2026-09-30", cf.facts_date)
    excl = ["oca_net_lease_investment", "oca_fin_investments", "oca_loans_employees", "oca_loans_third",
            "oca_loans_related", "oca_income_tax_prepaid"]
    ex_end = math.fsum(need(cf.F, f"balance.2026-06-30.{k}") for k in excl) - fin["2026Q3"] - loan["2026Q3"]
    oca = need(cf.F, "balance.2026-09-30.bs_other_c_assets")
    assert abs((g1.value - g0.value) - (flows - (oca * sh["x"] - ex_end))) < 1e-9
    assert abs(g1.value) < 25.0 and not g1.hits


def test_zero_weight_cells_do_not_fire(book, cf, grid):
    """§13.2 (Г11): клетки с весом 0 по точке гейтов капитала и доли терминала не срабатывают (печатаются); без
    ключа — срабатывают со статусом «флаг»."""
    g1 = next(g for g in C.gates(grid, explanations={}) if g.name == "equity_cushion")
    assert not g1.fired and g1.status == "ok" and "клетки с весом 0" in g1.detail and "M|stress|high" in g1.detail
    g0 = next(g for g in C.gates(evaluate(_drop(book, "checks", "zero_weight_cells"), cf), explanations={})
              if g.name == "equity_cushion")
    assert g0.fired and g0.status == "flag" and g0.mass == 0.0
    # клетки с весом > 0 срабатывают по-прежнему: узкий коридор доли терминала
    B = copy.deepcopy(book)
    B["checks"]["terminal_share_cell"] = [0.15, 0.45]
    g2 = evaluate(B, cf)
    t = next(g for g in C.gates(g2, explanations={}) if g.name == "terminal_share")
    lam = g2.point.lam
    pp = {c.key: lam * c.p["analytical"] + (1 - lam) * c.p["macro_neutral"] for c in g2.cells}
    assert t.fired and all(pp[k] > 0 for k in t.cells) and abs(t.mass - math.fsum(pp[k] for k in t.cells)) < 1e-15


def test_shield_of_the_cohort_tail(book, cf):
    """§6.4 (ниже порога, п. 1): щит первых H лет — на ставке когорт; без ключа — Гордон щита от T_1."""
    x1, x0 = _cell(book, cf), _cell(_drop(book, "terminal", "shield_cohort"), cf)
    assert x1.tv == x0.tv and x1.tv_shield != x0.tv_shield
    assert abs(x1.tv_shield - x0.tv_shield) < 1e-2 * x0.tv_shield
    B = copy.deepcopy(book)
    B["terminal"]["da_cohort_years"] = 1                     # короткий хвост: щит хвоста ближе к прежнему
    x2, x3 = _cell(B, cf), _cell(_drop(B, "terminal", "shield_cohort"), cf)
    assert abs(x2.tv_shield - x3.tv_shield) < abs(x1.tv_shield - x0.tv_shield) + 1e-9


# ------------------------------------------------------------------ порчи общего кода (приёмка 303)


def test_terminal_share_of_the_point_is_one_form(book, cf, grid):
    """§8, §13.2: доля терминала точки — одна форма в гейте, в «порядке величин» и в point.py."""
    gate = next(g for g in C.gate_calcs(grid) if g.name == "terminal_share_point").value
    mag = next(r for r in C.magnitudes(book, grid, None) if r["name"] == "terminal_share")["value"]
    pt = PT.evaluate(book, cf)["terminal_share"]
    an = grid.layers["analytical"].terminal_share
    assert abs(gate - mag) < 1e-12 and abs(gate - pt) < 1e-12
    assert abs(gate - an) > 1e-6


def test_release_capex_block_names_the_kappa_base(book):
    """Выпуск (docs/PAYLOAD.md): база κ, окно и правило высокого уровня; книга 1.1 — null."""
    blk = capex_block(book)
    assert (blk["balance_base"], blk["kappa_window"], blk["kappa_high"]) == ("ex_fs", "ltm", "mirror")
    b11 = capex_block(_git_book(BOOK_11))
    assert (b11["balance_base"], b11["kappa_window"], b11["kappa_high"]) == (None, None, None)


def test_in_range_tolerance_of_the_reverse_dcf():
    """§11: решение на краю диапазона — внутри (допуск 1e-9 размаха)."""
    assert U.in_range(1 + 1e-12, -1, 1) and U.in_range(-1 - 1e-12, -1, 1)
    assert not U.in_range(1.01, -1, 1) and not U.in_range(-1.01, -1, 1)
