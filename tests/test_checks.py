"""Проверки выпуска §13 (`model/checks.py`; устав, раздел 5): инварианты, гейты с объяснениями, ловушки, «ухудшение
не повышает цену», развилки с ценой, выпуск; тест литералов — нет чисел книги и годов в коде модели."""

from __future__ import annotations

import copy
import datetime as dt
import io
import math
import tokenize
from pathlib import Path

import pytest

from model import checks as C
from model.book import override
from model.facts import core_facts
from model.core import _tau
from model.grid import evaluate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cf(book, facts):
    return core_facts(facts, book["meta"]["anchor_period"])


@pytest.fixture(scope="module")
def bare_gates(grid):
    return C.gates(grid, explanations={})


@pytest.fixture(scope="module")
def grid12(book, facts):
    """Сетка, на которой гейты срабатывают, как на книге 1.2: без ключа `checks.zero_weight_cells` (капитал клеток с
    весом 0 — «флаг») и с узким коридором отдачи прироста (± 5 %: по ожиданию — вне, масса 1). Книга 1.3 гейтов не
    включает (§13.2)."""
    B = copy.deepcopy(book)
    B["checks"].pop("zero_weight_cells", None)
    B["checks"]["roic_tol"] = 0.05
    return evaluate(B, facts)


# ------------------------------------------------------------ инварианты


def test_invariants_of_section_13_1_hold(grid):
    inv = C.invariants(grid)
    assert {"probabilities", "group_identity", "fcff_identity", "ev_identity", "claims_identity", "finite",
            "regime_center", "facts_window"} == {i["name"] for i in inv}
    assert all(i["ok"] for i in inv), [i for i in inv if not i["ok"]]


def test_regime_center_catches_a_shifted_regime(book, cf):
    g = evaluate(override(book, ["margin.targets.C.strong"], "shift", 1e-6), cf)
    assert not next(i for i in C.invariants(g) if i["name"] == "regime_center")["ok"]


# ----------------------------------------------------------------- гейты


def test_all_gates_of_section_13_2_are_in_place(bare_gates):
    assert [g.name for g in bare_gates] == list(C.GATES)
    for g in bare_gates:
        assert g.status in C.STATUSES
        if g.status == "not_computed":
            assert g.mass is None and not g.fired and g.detail
        else:
            assert g.mass is not None and 0 <= g.mass <= 1 and (g.value is None or math.isfinite(g.value))


def test_a_fired_gate_without_explanation_blocks(grid12):
    """Сработавший гейт без объяснения роняет сборку; если сработали только клетки с вероятностью 0 — статус «флаг»,
    сборку не роняет (§13.2; книга 1.2)."""
    bare_gates = C.gates(grid12, explanations={})
    fired = [g for g in bare_gates if g.fired]
    assert fired, "на книге гейты срабатывают (отдача прироста по ожиданию, капитал клетки)"
    zero = [g for g in fired if g.mass == 0.0 and g.cells and not g.cells[0].startswith(("слой", "точка", "книга"))]
    assert all(g.status == "flag" and not g.blocking for g in zero)
    hard = [g for g in fired if g not in zero]
    assert hard and all(g.status == "unexplained" and g.blocking for g in hard)
    reasons = C.blocking_reasons([], bare_gates)
    assert len(reasons) == len(hard) and all("нет объяснения" in r for r in reasons)


def test_explanations_file_covers_every_fired_gate(grid, book_date):
    """Книга 1.3: гейты не срабатывают; объяснение гейта капитала держит цену ограниченной ответственности (Г11)."""
    gl = C.gates(grid, today=book_date)
    assert not [g.name for g in gl if g.blocking]
    assert {g.name for g in gl if g.fired} <= set(C.load_gate_explanations())
    assert set(C.load_gate_explanations()) == {"equity_cushion"}


def test_expired_explanation_or_mass_outside_blocks(grid12, book_date):
    name = "roic_increment"                        # книга 1.2: сработал по ожиданию, масса 1
    spec = {name: {"explanation": "x", "expected_mass": (1.0, 1.0), "valid_until": book_date}}
    assert next(g for g in C.gates(grid12, spec, book_date) if g.name == name).status == "explained"
    old = {name: {**spec[name], "valid_until": book_date - dt.timedelta(days=1)}}
    assert next(g for g in C.gates(grid12, old, book_date) if g.name == name).status == "expired"
    narrow = {name: {**spec[name], "expected_mass": (0.0, 0.0)}}
    g = next(g for g in C.gates(grid12, narrow, book_date) if g.name == name)
    assert g.status == "mass_outside" and g.blocking


def test_explanations_file_rejects_unknown_gate_and_missing_keys(tmp_path):
    bad = tmp_path / "g.yaml"
    bad.write_text("nope: {explanation: x, expected_mass: [0, 1], valid_until: 2030-01-01}\n", encoding="utf-8")
    with pytest.raises(C.GateExplanationError, match="незнакомый гейт"):
        C.load_gate_explanations(bad)
    bad.write_text("roic_increment: {explanation: x}\n", encoding="utf-8")
    with pytest.raises(C.GateExplanationError, match="нет ключа"):
        C.load_gate_explanations(bad)


def test_flow_9m26_is_not_computed_without_the_key(book, cf):
    """Книга 1.1 (без ключа `checks.flow_9m26_capex`): гейт не считается — оборотного «без банка» на конец квартала в
    фактах нет (его оценивает только правило книги 1.2)."""
    end = C._quarter_end(book["meta"]["observation_quarter"])
    assert end not in cf.nwc
    B = copy.deepcopy(book)
    B["checks"].pop("flow_9m26_capex")
    g = C.flow_9m26(B, cf, None)
    assert not g.computed and end in g.reason


def test_flow_9m26_with_the_key_is_the_season_rule(book, cf, grid):
    """§13.2, книга 1.2: F_мод − F_факт по строкам датабука 30.09 и долям 30.06; сезонная доля 3К окна; коридор
    оборотного ± 5 млрд сдвигает разницу ровно на ∓ 5."""
    g = C.flow_9m26(book, cf, grid)
    assert g.computed and g.layer and g.corridor == (-25.0, 25.0)
    assert abs(g.low - (g.value - 5.0)) < 1e-9 and abs(g.high - (g.value + 5.0)) < 1e-9
    assert abs(g.value) < 25.0 and not g.hits
    est, sh = C.nwc_quarter_estimate(book, cf, "2026-09-30", cf.facts_date)
    assert abs(est - (-43.8)) < 0.05 and abs(sh["x"] - 0.5212) < 1e-4 and abs(sh["y"] - 0.7825) < 1e-4


def _roiic(grid, book, cf):
    roic, tol = float(book["terminal"]["roic"]), float(book["checks"]["roic_tol"])
    yl = int(book["meta"]["last_period"][:4])
    nop, fa = {}, {}
    g4 = book["checks"].get("roic_tax") == "general"      # книга 1.3 (§13.2): обе стороны по τ_g
    gen = book["tax"].get("general_blocks")               # книга 1.4 (§13.2): часть G — уже по τ_g
    one = book["checks"].get("roic_one_season")           # книга 1.4: IC конца — 30.06 последнего года
    tg, tl = float(book["tax"]["general"]), _tau(book, yl)
    for c in grid.cells:
        r = c.result
        if g4:
            pg = [math.fsum(max(0.0, r.blocks[b]["ebitda"][i]) for b in gen) if gen else 0.0
                  for i in range(len(r.periods))]
            nop[c.key] = math.fsum(e - ((t - tg * q) * tg / tl + tg * q)
                                   for p, e, t, q in zip(r.periods, r.group["ebit"], r.group["tax"], pg)
                                   if int(p[:4]) == yl)
        else:
            nop[c.key] = math.fsum(v for p, v in zip(r.periods, r.group["nopat"]) if int(p[:4]) == yl)
        fa[c.key] = r.group["fa"][-2] if one else r.group["fa"][-1]
    cells = {k: (nop[k] - roic * cf.fa_anchor) / (fa[k] - cf.fa_anchor) for k in nop}
    lam = grid.point.lam                                  # книга 1.4 (`joint.expectation`): веса заголовка
    p = ({c.key: lam * c.p["analytical"] + (1 - lam) * c.p["macro_neutral"] for c in grid.cells}
         if book["joint"].get("expectation") == "headline" else {c.key: c.p["analytical"] for c in grid.cells})
    e = ((math.fsum(p[k] * nop[k] for k in nop) - roic * cf.fa_anchor)
         / (math.fsum(p[k] * fa[k] for k in fa) - cf.fa_anchor))
    return cells, e, roic * (1 - tol), roic * (1 + tol)


def test_roic_increment_is_accepted_on_the_expectation_cells_only_advise(grid, book, cf):
    """Правило κ (§13.2): база — ОС + НМА + право пользования без помещений, без оборотного капитала; приёмка — по
    ожиданию слоя «свой взгляд» (масса 1), клетки — советующий флаг в детали, сборку не держат."""
    assert C.kappa_rule(book)
    cells, e, lo, hi = _roiic(grid, book, cf)
    g = next(g for g in C.gate_calcs(grid) if g.name == "roic_increment")
    assert g.layer and abs(g.value - e) < 1e-12
    assert bool(g.hits) == (not lo <= e <= hi)
    n_out = sum(1 for v in cells.values() if not lo <= v <= hi)
    assert f"вне коридора {n_out} из {len(cells)}" in g.reason


def test_roic_increment_without_the_key_is_per_cell(book, cf):
    """Книга без ключа κ (прежнее правило): ROIIC каждой клетки, масса — клетки."""
    from tests.test_core_services import levels_book
    B = levels_book(book)
    g2 = evaluate(B, cf)
    cells, _e, lo, hi = _roiic(g2, B, cf)
    g = next(g for g in C.gate_calcs(g2) if g.name == "roic_increment")
    assert not g.layer and {k for k, v in cells.items() if not lo <= v <= hi} == {k for k, _ in g.hits}


def test_terminal_reinvest_is_an_identity_under_the_kappa_rule(grid):
    """Правило κ: Σ (CX − DA) терминала = g × FA(2036H2) — гейт не срабатывает, отношение 1 до 1e-9."""
    g = next(g for g in C.gate_calcs(grid) if g.name == "terminal_reinvest")
    assert not g.hits and abs(g.low - 1) < 1e-9 and abs(g.high - 1) < 1e-9


def test_terminal_reinvest_is_the_return_on_end_capital_against_the_window(book, cf):
    """Прежнее правило: CX − DA = g / ROIC × NOPAT_T ⇒ гейт ≡ NOPAT_T / FA конца прогноза ≥ ROIC окна."""
    from tests.test_core_services import levels_book
    B = levels_book(book)
    g2 = evaluate(B, cf)
    roic = float(B["terminal"]["roic"])
    g = next(g for g in C.gate_calcs(g2) if g.name == "terminal_reinvest")
    hit = {k for k, _ in g.hits}
    for c in g2.cells:
        r = c.result
        ratio = math.fsum(r.terminal["nopat"]) / r.group["fa"][-1] / roic
        if abs(ratio - 1) > 1e-6:
            assert (c.key in hit) == (ratio < 1), c.key


def test_corridors_of_the_book_are_computed(bare_gates, grid, book):
    """Коридоры книги 1.1 считаются: маржа, вложения, реальная ставка, V0/D; доля терминала — клетка 15–85 %,
    приёмка 40–75 % по точке (λ-смесь долей слоёв)."""
    by = {g.name: g for g in bare_gates}
    for name in ("margin_range", "capex_range", "real_rate", "equity_cushion", "terminal_share",
                 "terminal_share_point"):
        assert by[name].status != "not_computed", name
    assert by["terminal_share"].corridor == tuple(book["checks"]["terminal_share_cell"])
    lo, hi = grid.layers["macro_neutral"].terminal_share, grid.layers["analytical"].terminal_share
    assert abs(by["terminal_share_point"].value - (lo + grid.point.lam * (hi - lo))) < 1e-12
    assert by["terminal_share_point"].corridor == tuple(book["checks"]["terminal_share"])


# ---------------------------------------------------------------- ловушки


def test_traps_of_section_13_3_hold(grid):
    tl = C.traps(grid)
    assert [t["name"] for t in tl] == ["sbc_once", "bank_not_in_debt", "one_lease", "one_base", "worsening"]
    assert all(t["ok"] for t in tl), [t for t in tl if not t["ok"]]


def test_one_base_trap_catches_a_hand_edited_share(book, cf):
    g = evaluate(override(book, ["nwc.share_h1"], "shift", 1e-4), cf)
    t = C.trap_one_base(g)
    assert not t["ok"] and "nwc.share_h1" in t["detail"]


@pytest.mark.parametrize("name", ["curve", "margin", "investment", "sbc", "tax"])
def test_worsening_does_not_raise_the_price(grid, book, cf, name):
    """Устав, раздел 5: кривая +2 п.п., цели маржи −1 п.п., выше капвложения (по правилу κ — κ всех уровней +0,01),
    выплаты акциями +0,5 п.п., налог +5 п.п. — точка строго дешевле; клетки — советующий флаг (решение
    архитектора: в клетке с отрицательным потоком терминала рост ставки уменьшает отрицательную стоимость)."""
    books = C.worsened_books(book)
    if name == "investment":
        assert set(books) & {"kappa", "capex"} == ({"kappa"} if C.kappa_rule(book) else {"capex"})
        name = "kappa" if C.kappa_rule(book) else "capex"
    g2 = evaluate(books[name], cf, valuation_date=grid.ctx.v, market_price=grid.market_price)
    assert g2.point.central < grid.point.central
    base = {c.key: c.result.price for c in grid.cells}
    up = [c.key for c in g2.cells if c.result.price > base[c.key] + C.WORSEN_TOL]
    assert all(c.p["analytical"] <= 0.0125 for c in g2.cells if c.key in up)      # только крайние клетки стресса


def test_worsened_median_is_the_band_of_the_worsened_book(book, cf):
    """§13.3, §9: медиана ухудшенной книги — полоса этой книги на тех же случайных числах: ухудшение, лежащее на оси
    полосы (выплаты акциями, ставка IT после 2030 г.), доходит до прогонов — как быстрые числа на ухудшенной книге."""
    from model import uncertainty as U
    b = U.band(book, cf, draws=24, n_workers=1)
    for name in ("sbc", "tax"):
        B = C.worsened_books(book)[name]
        want = U.band(B, cf, draws=24, n_workers=1).stats()["median"]
        got = C._worse_median(B, cf, b, 1)
        assert got == want and got < b.stats()["median"], name


def test_worsening_trap_checks_the_median_and_only_flags_cells(grid, book, cf):
    """§13.3: с полосой ловушка сравнивает и медиану (те же случайные числа); клетки — флаг, не провал."""
    from model import uncertainty as U
    b = U.band(book, cf, draws=24, n_workers=1)
    t = C.trap_worsening(grid, b, b.stats()["median"], n_workers=1)
    assert t["ok"] and "медиана" in t["detail"]


def test_worsening_trap_catches_an_improvement(grid, monkeypatch):
    monkeypatch.setitem(C.WORSEN_PP, "curve", -C.WORSEN_PP["curve"])
    t = C.trap_worsening(grid)
    assert not t["ok"] and "кривая" in t["detail"]


# ---------------------------------------------------- порядок величин, развилки


@pytest.fixture(scope="module")
def release_book(book):
    """Книга выпусков и развилок такта: книга 1.4 без `forks.rederive` (решение ведущего по правилам: потолок такта
    180 с; развилки с перевыводом — `tests/test_book_14_forks.py` и сборка выпуска; ключ на дешёвом пути —
    `tests/test_book_14.py::test_forks_rederive_key_dispatches_the_rederivation`)."""
    import copy

    B = copy.deepcopy(book)
    B["forks"].pop("rederive", None)
    return B


def test_magnitudes_and_forks_are_named_with_price(grid, release_book, cf):
    book = release_book
    from model import uncertainty as U

    b = U.band(book, cf, draws=24, n_workers=1)
    head = b.stats()["median"]
    rows = C.magnitudes(book, grid, head)
    assert [r["name"] for r in rows] == ["market", "brokers_price", "paper_price", "ev_ebitda_2027", "terminal_share"]
    fk = {f["name"]: f for f in C.forks(book, cf, grid, b, head, n_workers=1)}
    assert set(fk) == {"it_tax", "stress", "ecom_margin", "bank_money", "autonomous"}
    price = {n: [v["price"] for v in f["variants"]] for n, f in fk.items()}
    assert price["it_tax"][0] == 0 and price["it_tax"][1] > 0 > price["it_tax"][2]      # 5 % дороже, 25 % дешевле
    assert price["stress"][1] < 0
    assert price["ecom_margin"][1] < 0 < price["ecom_margin"][2]
    dD = fk["bank_money"]["variants"][1]["value"] - cf.bank_money
    want = -dD * (1 - grid.ctx.g_gov) * 1000 / grid.ctx.n_eff
    assert math.isclose(price["bank_money"][1], want, rel_tol=1e-9)
    assert fk["autonomous"]["status"] == "ok"
    assert price["autonomous"][0] == 0 and price["autonomous"][1] < 0 < price["autonomous"][2]   # вечный расход дешевле


# --------------------------------------------------------------------- выпуск


@pytest.fixture(scope="module")
def release(release_book, facts):
    from model import payload as P
    from model import uncertainty as U

    try:
        return P.build_payload(book=release_book, facts=facts, fast=True, draws=24, n_workers=1)
    finally:
        U.close_pool()


def test_release_carries_gates_flags_traps_and_forks(release):
    ck = release["checks"]
    assert [g["name"] for g in ck["gates"]] == list(C.GATES)
    assert all(isinstance(g["fired"], bool) and g["status"] in C.STATUSES for g in ck["gates"])
    assert [f["name"] for f in ck["flags"]] == ["book_update", "dividend_register", "report_fact", "price_fallback",
                                                "stale_release"]
    assert all(isinstance(f["raised"], bool) for f in ck["flags"])
    assert all(t["ok"] for t in ck["traps"]) and ck["forks"] and ck["magnitudes"]


def test_validate_catches_an_unexplained_gate_or_a_failed_trap(release):
    from model import payload as P

    bad = copy.deepcopy(release)
    g = bad["checks"]["gates"][0]                  # книга 1.3: гейты не срабатывают — порча «сработал без объяснения»
    g["fired"], g["status"] = True, "unexplained"
    assert any("checks.gates" in p for p in P.validate(bad))
    bad = copy.deepcopy(release)
    bad["checks"]["traps"][0]["ok"] = False
    assert any("checks.traps" in p for p in P.validate(bad))


def test_checks_command_prints_the_release(release, tmp_path, capsys):
    import json

    path = tmp_path / "latest.json"
    path.write_text(json.dumps(release, ensure_ascii=False), encoding="utf-8")
    assert C.main(["--release", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Гейты" in out and "roic_increment" in out and "ИТОГ: блокирующих нет" in out


# -------------------------------------------------------------- литералы


# Числа, которые не считаются числами книги: структурные (как в tests/test_core_services.py).
ALLOWED = {0, 1, 2, 3, 4, 5, 6, 8, 0.5, 0.25, 0.75, 0.01, 100, 1000, 1e-4, 1e-12}   # 5 — знак периода «2026H1»[5]
# Именованные постоянные кода, совпавшие с числом книги или похожие на год: (модуль, литерал) → что это.
NAMED = {
    ("model/book.py", "7"): "H2_START_MONTH — месяц начала второго полугодия",
    ("model/payload.py", "0.10"): "MAX_V0_JUMP — защита заголовка §13.6",
    ("model/payload.py", "7"): "STALE_DAYS — свежесть живых входов",
    ("model/rederive.py", "7"): "дней в неделе (недельные ряды β)",
    ("model/uncertainty.py", "0.10"): "квантиль P10 (§9)",
    ("model/rederive.py", "0.05"): "DIP_SEARCH_HI — верх поиска провала κ (§4.9, книга 1.3)",
    ("model/rederive.py", "50"): "FIXED_POINT_ROUNDS — предел кругов «δ — уровни κ» (§4.9, книга 1.3)",
    ("model/book_schema.py", "2000"): "нижняя граница года в схеме",
    ("model/book_schema.py", "2100"): "верхняя граница года в схеме",
    ("model/uncertainty.py", "40"): "FAST_SUBSAMPLE, BISECTION_STEPS, HISTOGRAM_MAX_BINS — постоянные полосы",
    ("model/payload.py", "40"): "HISTOGRAM_MAX_BINS — гистограмма выпуска",
}


def _book_numbers(node, out: set) -> set:
    if isinstance(node, dict):
        for v in node.values():
            _book_numbers(v, out)
    elif isinstance(node, list):
        for v in node:
            _book_numbers(v, out)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out.add(float(node))
    return out


MODULES = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "model").glob("*.py"))


def test_named_constants_still_exist():
    """Разрешённое не устаревает: каждая пара (модуль, литерал) ещё есть в коде."""
    for module, lit in NAMED:
        toks = {t.string for t in tokenize.generate_tokens(io.StringIO(
            (ROOT / module).read_text(encoding="utf-8")).readline) if t.type == tokenize.NUMBER}
        assert lit in toks, (module, lit)


@pytest.mark.parametrize("module", MODULES)
def test_no_book_numbers_or_years_in_model_code(book, module):
    nums = _book_numbers(book, set())
    src = (ROOT / module).read_text(encoding="utf-8")
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.NUMBER or (module, tok.string) in NAMED:
            continue
        v = float(eval(tok.string))  # noqa: S307 — литерал числа
        year = v == int(v) and 1900 <= v <= 2100
        if v not in ALLOWED and (v in nums or year):
            found.append((tok.start[0], tok.string))
    assert not found, f"{module}: числа книги или годы в коде: {found}"
