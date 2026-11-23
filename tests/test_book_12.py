# -*- coding: utf-8 -*-
"""Книга 1.2 (правка этапа 3): каждый ключ книги — своё правило методики (docs/MODEL.md §17, «Ключи книги 1.2»);
без ключей — книга 1.1 и 1.0 бит в бит; порчи, которые прежние тесты не ловили (рабочий файл, раздел 4, ошибки ниже
порога, п. 4)."""
from __future__ import annotations

import copy
import math

import pytest

from model import checks as C
from model.book import load_book
from model.book_schema import BLOCKS, validate_book
from model.core import Context, _tau, nwc_fact, revenue_base, run_cell
from model.facts import core_facts, load_facts, need
from model.grid import evaluate
from model.rederive import derive, _with
from tests.old_books import old_book

ROOT = C.ROOT
BOOK_11, BOOK_10 = "1.1", "1.0"   # книги 1.1 (079bad5) и 1.0 (913ee3e): бит в бит — точки ядра этих коммитов
BOOK_12 = "1.2"                   # книга 1.2 (c150e62, main до правки этапа 3-2); копии — tests/old_books.py
POINT_11, MEDIAN_11 = 3074.024393196355, 2871.775
POINT_10, MEDIAN_10 = 3248.0843683629164, 3145.9


@pytest.fixture(scope="module")
def cf():
    return core_facts(load_facts())


@pytest.fixture(scope="module")
def book():
    """Книга 1.2 — из коммита c150e62 (книга 1.3 — tests/test_book_13.py): тесты ключей книги 1.2 на её числах."""
    A = old_book(BOOK_12)
    validate_book(A)
    return A


def _cell(B, cf, w="H", r="base", c="base"):
    return run_cell(Context(B, cf), w, r, c)


def _drop(B: dict, *path: str) -> dict:
    B = copy.deepcopy(B)
    node = B
    for part in path[:-1]:
        node = node[part]
    node.pop(path[-1])
    return B


# ------------------------------------------------------------------ без ключей — прежние книги бит в бит

@pytest.mark.parametrize("rev, point", [(BOOK_11, POINT_11), (BOOK_10, POINT_10)])
def test_previous_books_point_bit_for_bit(cf, rev, point):
    A = old_book(rev)
    validate_book(A)
    assert evaluate(A, cf).point.central == point


def test_book_11_small_band_bit_for_bit(cf):
    """Малая полоса книги 1.1 (400 прогонов, семя книги) — медиана 2 871,775 ₽, как до правки."""
    from model import quick as Q, uncertainty as U
    A = old_book(BOOK_11)
    try:
        r = Q.run(A, cf)
    finally:
        U.close_pool()
    assert r["stats"]["median"] == pytest.approx(MEDIAN_11, abs=1e-9)
    assert r["point"]["central"] == POINT_11


def test_book_11_rederive_is_unchanged(cf):
    """Перевывод книги 1.1 новым кодом пишет те же числа (ключей 1.2 нет — прежние правила)."""
    A = old_book(BOOK_11)
    V, *_ = derive(A, cf)
    for path, v in V.items():
        node = A
        for part in path:
            node = node[part]
        assert abs(float(node) - v) <= 1e-10, path


# ------------------------------------------------------------------ ключи книги 1.2

def test_book_12_carries_every_key(book):
    from model.book_schema import KEYS_1_2, opt
    assert book["meta"]["version"] == "1.2"
    for path in KEYS_1_2:
        assert opt(book, *path) is not None, path
    for path in (("financing", "shield_cap"), ("bridge", "dividend_paid"), ("terminal", "da_cohort_years"),
                 ("sbc", "range_offset"), ("tax", "it_rate_lt_offset"), ("nwc", "less_bridge"), ("sbc", "h1_years")):
        assert opt(book, *path) is not None, path
    assert book["nwc"]["income_tax_place"] == "none"


def test_balance_base_is_revenue_without_fs(book, cf):
    """§0, ключ `meta.balance_base: ex_fs`: R° = R − (1 − x_R) R_G = R × Σ A–F / Σ A–G в каждом полугодии; без ключа
    строки R° нет."""
    x = _cell(book, cf)
    xr = book["excl"]["revenue_share"]
    for i, ro in enumerate(x.group["revenue_xfs"]):
        R, RG, RF = x.group["revenue"][i], x.blocks["G"]["revenue"][i], x.blocks["F"]["revenue"][i]
        S = math.fsum(x.blocks[b]["revenue"][i] for b in BLOCKS)
        assert abs(ro - (R - (1 - xr) * RG)) < 1e-9
        assert abs(ro - R * (S - RG + RF) / (S + RF)) < 1e-9
    a = book["meta"]["anchor_period"]
    want = cf.rev["group"][a] * math.fsum(cf.rev[b][a] for b in "ABCDEF") / math.fsum(cf.rev[b][a] for b in "ABCDEFG")
    assert abs(revenue_base(book, cf, a) - want) < 1e-12
    assert "revenue_xfs" not in _cell(_drop(book, "meta", "balance_base"), cf).group


def test_nwc_and_kappa_follow_the_base(book, cf):
    """§4.10, §4.9: NWC(p) = n_h × R°_ann(p); κ₁ = FA / R12 на базе R°."""
    x = _cell(book, cf)
    g, a = x.group, book["meta"]["anchor_period"]
    ro = g["revenue_xfs"]
    n1, n2 = book["nwc"]["share_h1"], book["nwc"]["share_h2"]
    for i, p in enumerate(x.periods):
        prev = ro[i - 1] if i else revenue_base(book, cf, a)
        assert abs(g["nwc"][i] - (n1 if p.endswith("H1") else n2) * (ro[i] + prev)) < 1e-9


def test_raidtech_growth_on_revenue_and_autoru_multiplier(book, cf):
    """§4.2: `path_2026h2.b_growth: revenue` — рост B по выручке 3К26; `autoru_form: multiply` — 1 / (1 − α)."""
    V, *_ = derive(book, cf)
    cpi, al = cf.cpi["2026Q3"], book["growth"]["A"]["autoru_share"]
    gB = cf.rev["B"]["2026Q3"] / cf.rev["B"]["2025Q3"]
    assert abs(V[("path_2026h2", "real_growth", "B")] - (gB / (1 + cpi) - 1)) < 1e-10
    gA = cf.rev["A"]["2026Q3"] / cf.rev["A"]["2025Q3"]
    assert abs(V[("path_2026h2", "real_growth", "A")] - (gA / (1 - al) / (1 + cpi) - 1)) < 1e-10
    V0, *_ = derive(_drop(_drop(book, "path_2026h2", "b_growth"), "growth", "A", "autoru_form"), cf)
    gtv = cf.gtv_B["2026Q3"] / cf.gtv_B["2025Q3"]
    assert abs(V0[("path_2026h2", "real_growth", "B")] - (gtv / (1 + cpi) - 1)) < 1e-10
    assert abs(V0[("path_2026h2", "real_growth", "A")] - ((gA + al) / (1 + cpi) - 1)) < 1e-10


def test_exclusions_share_ltm_sbc_season_and_cpi_12m(book, cf):
    """§4.1 `excl.revenue_period: ltm`; §4.8 `sbc.h1_years: [2025]`; §0.3 `growth.A.cpi_12m: monthly`."""
    V, *_ = derive(book, cf)
    ltm = ["2025Q4", "2026Q1", "2026Q2", "2026Q3"]
    s = math.fsum(cf.rev[b][q] for b in "ABCDEFG" for q in ltm)
    assert abs(V[("excl", "revenue_share")] - (-math.fsum(cf.rev["X"][q] for q in ltm) / s)) < 1e-10
    assert abs(V[("sbc", "h1_share")] - cf.sbc["2025H1"] / cf.sbc["2025"]) < 1e-10
    assert abs(cf.cpi["12m_2026Q3"] - 0.061096) < 1e-9
    V0, *_ = derive(_drop(book, "growth", "A", "cpi_12m"), cf)
    assert V0[("growth", "A", "y2027_real")] != V[("growth", "A", "y2027_real")]


def test_tax_base_with_corporate_expenses_and_shield_cap(book, cf):
    """§4.11 `tax.base_rule: f_other`: база = Σ прибыльных A–E, G + E_o − DA + E_X + E_atk (E_X — тоже: порча «налог
    без исключений»); §4.13 `financing.shield_cap`: t_S ≤ τ_g, щит терминала — Гордон без роста (порча «щит с
    ростом»)."""
    x = _cell(book, cf, "M", "stress", "low")
    g = x.group
    eo = book["f"]["other_share"]
    for i, p in enumerate(x.periods):
        E_o = 2.0 * cf.ebitda["F_o"]["2026Q3"] if i == 0 else eo * g["revenue"][i]
        pos = math.fsum(e for e in (x.blocks[b]["ebitda"][i] for b in BLOCKS) if e > 0)
        base = pos + E_o - g["da"][i] + x.blocks["X"]["ebitda"][i] + g["attack"][i]
        assert abs(g["tax"][i] - _tau(book, int(p[:4])) * max(0.0, base)) <= 1e-12 * max(1.0, g["tax"][i])
        assert g["tax_rate_cash"][i] <= book["tax"]["general"] + 1e-15
    t, r = x.terminal, x.r
    assert abs(x.tv_shield - (t["shield"][0] * (1 + r) ** 0.75 + t["shield"][1] * (1 + r) ** 0.25) / r) < 1e-9
    nocap = _cell(_drop(book, "financing", "shield_cap"), cf, "M", "stress", "low")
    capped = [i for i, ts in enumerate(nocap.group["tax_rate_cash"]) if ts > book["tax"]["general"]]
    assert capped, "без предела ставка щита где-то выше 25 % (ошибка 9)"


def test_axes_edges_are_offsets_and_gdp_is_one_axis(book):
    """§9: края осей 8 и 9 — сдвиги от центра (сдвиг центра двигает края); ось 10 ставит значение в оба ключа."""
    from model.book import override
    from model import uncertainty as U
    ax = {a.key: a for a in U.band_axes(book)}
    s, t = book["sbc"], book["tax"]
    assert (ax["sbc"].low, ax["sbc"].high) == (s["share"] + s["range_offset"][0], s["share"] + s["range_offset"][1])
    lt = t["it_rate"]["LT"]
    assert (ax["it_tax"].low, ax["it_tax"].high) == (lt + t["it_rate_lt_offset"][0], lt + t["it_rate_lt_offset"][1])
    B = override(book, ["sbc.share"], "value", s["share"] + 0.005)
    b = {a.key: a for a in U.band_axes(B)}["sbc"]
    assert abs((b.low - ax["sbc"].low) - 0.005) < 1e-15 and abs((b.high - ax["sbc"].high) - 0.005) < 1e-15
    assert ax["terminal_growth"].paths == ("terminal.real_growth", "growth.lt_real")
    B = override(book, list(ax["terminal_growth"].paths), "value", 0.02)
    assert B["terminal"]["real_growth"] == B["growth"]["lt_real"] == 0.02
    bad = copy.deepcopy(book)
    bad["growth"]["lt_real"] = 0.016
    with pytest.raises(Exception, match="одно суждение"):
        validate_book(bad)


def test_terminal_cohorts_converge_and_follow_the_rule(book, cf):
    """§6.3 `terminal.da_cohort_years`: TV = Σ 2H полугодий когортами и правилом κ + Гордон × ((1+g)/(1+r))^H;
    к 40 годам амортизация когорт почти сошлась — 60 лет меняют стоимость терминала меньше чем на 1e-4 (≈ 0,15 ₽)."""
    x40 = _cell(book, cf)
    B60 = copy.deepcopy(book)
    B60["terminal"]["da_cohort_years"] = 60
    x60 = _cell(B60, cf)
    assert abs(x60.tv - x40.tv) <= 1e-4 * abs(x40.tv)
    x0 = _cell(_drop(book, "terminal", "da_cohort_years"), cf)
    t, g, r = x0.terminal, x0.g, x0.r
    gordon = (t["fcff"][0] * (1 + r) ** 0.75 + t["fcff"][1] * (1 + r) ** 0.25) / (r - g)
    assert abs(x0.tv - gordon) < 1e-9 and abs(x40.tv - gordon) > 1.0


def test_bridge_lines_dividend_and_autoru_out_of_nwc(book, cf):
    """§7.1, §4.10 (ошибка 8): чистые инвестиции в аренду — актив моста; дивиденд — выплата ОДДС 41,758;
    остаток за Авто.ру вычтен из оборотного якоря (3,5)."""
    x = _cell(book, cf)
    cl = x.claims
    assert abs(cl["bridge.oca_net_lease_investment"] + need(cf.F, "balance.2026-06-30.oca_net_lease_investment")) < 1e-12
    assert abs(cl["dividends"] - 41.758) < 1e-9
    assert abs(nwc_fact(book, cf, cf.facts_date) - (cf.nwc[cf.facts_date] - 3.5)) < 1e-12
    assert nwc_fact(book, cf, "2025-12-31") == cf.nwc["2025-12-31"]          # на 31.12.2025 остатка нет
    B = _drop(book, "bridge", "dividend_paid")
    assert abs(_cell(B, cf).claims["dividends"] - 42.0) < 1e-9


@pytest.mark.parametrize("place", ["none", "nwc", "bridge"])
def test_income_tax_place(book, cf, place):
    """§4.10, ключ `nwc.income_tax_place`: none — вне оборотного и моста; nwc — внутри оборотного на обеих датах;
    bridge — налог к уплате (обязательство) и авансы (актив) в мосте на якорь."""
    B = copy.deepcopy(book)
    B["nwc"]["income_tax_place"] = place
    validate_book(B)
    tp = {d: need(cf.F, f"balance.{d}.bs_income_tax_payable") for d in ("2025-12-31", "2026-06-30")}
    adv = {d: need(cf.F, f"balance.{d}.oca_income_tax_prepaid") for d in tp}
    base = {d: nwc_fact(book, cf, d) for d in tp}
    for d in tp:
        want = base[d] - tp[d] + adv[d] if place == "nwc" else base[d]
        assert abs(nwc_fact(B, cf, d) - want) < 1e-12
    cl = _cell(B, cf).claims
    if place == "bridge":
        assert abs(cl["bridge.bs_income_tax_payable"] - tp["2026-06-30"]) < 1e-12
        assert abs(cl["bridge.oca_income_tax_prepaid"] + adv["2026-06-30"]) < 1e-12
    else:
        assert "bridge.bs_income_tax_payable" not in cl and "bridge.oca_income_tax_prepaid" not in cl
    bad = copy.deepcopy(book)
    bad["nwc"]["income_tax_place"] = "elsewhere"
    with pytest.raises(Exception, match="допустимо"):
        validate_book(bad)


def test_income_tax_place_moves_the_point_by_sign(book, cf):
    """Цена места: в оборотном (отсрочка на росте) точка выше, в мосте (требование) — ниже, чем вне обоих мест."""
    pts = {}
    for place in ("none", "nwc", "bridge"):
        B = copy.deepcopy(book)
        B["nwc"]["income_tax_place"] = place
        V, *_ = derive(B, cf)
        pts[place] = evaluate(_with(B, V), cf).point.central
    assert pts["bridge"] < pts["none"] < pts["nwc"]


def test_cell_gate_mass_is_by_the_point(book, cf):
    """§13.2 `checks.cell_mass: point`: масса гейта капитала клеток — λ-смесь вероятностей слоёв; без ключа — слой
    «свой взгляд»; сработали только клетки с вероятностью 0 и объяснения нет — статус «флаг»."""
    grid = evaluate(book, cf)
    lam = grid.point.lam
    eq = next(g for g in C.gates(grid, explanations={}) if g.name == "equity_cushion")
    p = {c.key: lam * c.p["analytical"] + (1 - lam) * c.p["macro_neutral"] for c in grid.cells}
    assert eq.fired and abs(eq.mass - math.fsum(p[k] for k in eq.cells)) < 1e-15
    assert eq.mass == 0.0 and eq.status == "flag" and not eq.blocking
    B = _drop(book, "joint", "capex_prob_given_regime")      # стресс с высокими вложениями получает вес > 0
    g2 = evaluate(B, cf)
    e2 = next(g for g in C.gates(g2, explanations={}) if g.name == "equity_cushion")
    p2 = {c.key: lam * c.p["analytical"] + (1 - lam) * c.p["macro_neutral"] for c in g2.cells}
    pa = {c.key: c.p["analytical"] for c in g2.cells}
    assert e2.mass > 0 and abs(e2.mass - math.fsum(p2[k] for k in e2.cells)) < 1e-15 and e2.blocking
    assert abs(e2.mass - math.fsum(pa[k] for k in e2.cells)) > 1e-6      # клетки миров H и M: точка ≠ слой
    e3 = next(g for g in C.gates(evaluate(_drop(B, "checks", "cell_mass"), cf), explanations={})
              if g.name == "equity_cushion")
    assert abs(e3.mass - math.fsum(pa[k] for k in e3.cells)) < 1e-15


def test_period_and_base_of_every_share(book, cf):
    """Ошибка 4, «такие же»: каждая доля книги — к своей базе и за свой период §17 (12 мес., окно, полные годы),
    независимо от перевывода."""
    ltm = ["2025Q4", "2026Q1", "2026Q2", "2026Q3"]
    win = [f"{y}Q{q}" for y in (2024, 2025) for q in (1, 2, 3, 4)] + ["2026Q1", "2026Q2", "2026Q3"]

    def sm(series, keys):
        return math.fsum(series[k] for k in keys)
    R12 = sm(cf.rev["group"], ltm)
    want = {
        ("f", "revenue_share"): sm(cf.rev["F"], ltm) / R12,
        ("f", "other_share"): sm(cf.ebitda["F_o"], ltm) / R12,
        ("excl", "revenue_share"): -sm(cf.rev["X"], ltm) / math.fsum(sm(cf.rev[b], ltm) for b in "ABCDEFG"),
        ("excl", "ebitda_share"): sm(cf.ebitda["X"], win) / math.fsum(sm(cf.rev[b], win) for b in "ABCDEFG"),
        ("sbc", "share"): sm(cf.sbc, ltm) / R12,
        ("bank", "capital_to_revenue"): cf.bank_capital["2026-09-30"] / sm(cf.rev["G"], ltm),
        ("capex", "lease_share"): cf.lease_add["ltm"] / (cf.rev["group"]["2025H2"] + cf.rev["group"]["2026H1"]),
        ("nwc", "share_h1"): nwc_fact(book, cf, "2026-06-30") / (revenue_base(book, cf, "2026H1")
                                                                 + revenue_base(book, cf, "2025H2")),
        ("nwc", "share_h2"): nwc_fact(book, cf, "2025-12-31") / (revenue_base(book, cf, "2025H2")
                                                                 + revenue_base(book, cf, "2025H1")),
    }
    for path, v in want.items():
        node = book
        for part in path:
            node = node[part]
        assert abs(node - v) <= 1e-10, path


# ------------------------------------------------------------------ порчи, которых тесты не ловили

def test_search_base_2027h1_without_autoru(book, cf):
    """§4.3: R_A(2027H1) = R_A(2026H1) × (1 − α × 5/6) × (1 + real) × (1 + ИПЦ) (порча «база A 1П27»)."""
    from model.core import _base_growth, _shift
    from model.book import path_value
    x = _cell(book, cf, "N", "slow", "base")
    i = x.periods.index("2027H1")
    al = book["growth"]["A"]["autoru_share"]
    real = _base_growth(book, "A", 2027, 2026) + _shift(book, "A", "slow", 2027)
    cpi = path_value(book["worlds"]["N"]["cpi"], "2027H1")
    want = cf.rev["A"]["2026H1"] * (1 - al * 5 / 6) * (1 + real) * (1 + cpi)
    assert abs(x.blocks["A"]["revenue"][i] - want) < 1e-9


def test_exclusions_on_the_sum_with_f(book, cf):
    """§4.1: R_X = −x_R × (S + R_F), E_X = x_E × (S + R_F) (порча «база исключений»)."""
    x = _cell(book, cf)
    xr, xe = book["excl"]["revenue_share"], book["excl"]["ebitda_share"]
    for i in range(len(x.periods)):
        S = math.fsum(x.blocks[b]["revenue"][i] for b in BLOCKS)
        RF = x.blocks["F"]["revenue"][i]
        assert abs(x.blocks["X"]["revenue"][i] + xr * (S + RF)) < 1e-9
        assert abs(x.blocks["X"]["ebitda"][i] - xe * (S + RF)) < 1e-12


def test_kappa_floor_binds_and_capital_returns_to_the_line(book, cf):
    """§4.9: CX(p) := max(CX, φ × DA) до FA(p); граница сработала — предупреждение, следующее полугодие возвращает
    капитал на линию (готовый тест приёмки 205 к порче «граница φ × DA отключена»; R12 — на базе R°)."""
    from model.core import kappa_line
    B = copy.deepcopy(book)
    B["capex"]["kappa_levels"]["low"] = 0.15
    x = run_cell(Context(B, cf), "N", "stress", "low")
    g, phi = x.group, float(B["capex"]["floor_da"])
    cx = [a + b for a, b in zip(g["capex"], g["lease_new"])]
    on = [i for i in range(len(cx)) if abs(cx[i] - phi * g["da"][i]) <= 1e-9 * g["da"][i]]
    assert on, "граница не сработала ни в одном полугодии"
    assert all(c >= phi * d - 1e-9 for c, d in zip(cx, g["da"]))
    assert any("нижней границе" in w for w in x.warnings)
    rx = g["revenue_xfs"]
    k1 = g["fa"][0] / (revenue_base(B, cf, B["meta"]["anchor_period"]) + rx[0])
    i = max(on) + 1
    assert abs(g["fa"][i] / (rx[i] + rx[i - 1]) - kappa_line(B, "low", k1, i)) < 1e-12


def test_worsening_trap_fails_when_only_the_median_rises(monkeypatch, book, cf):
    """§13.3: ловушка держит и точку, и медиану; рост одной медианы — провал (готовый тест приёмки 205)."""
    grid = evaluate(book, cf)
    monkeypatch.setattr(C, "worsened_books", lambda A_: {"sbc": A_})
    monkeypatch.setattr(C, "_worse_median", lambda B, cf_, band, n: 1001.0)
    t = C.trap_worsening(grid, band=object(), headline=1000.0, n_workers=1)
    assert not t["ok"]
