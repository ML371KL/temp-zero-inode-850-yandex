# -*- coding: utf-8 -*-
"""Ядро сервисов и книга 1.0 Яндекса (задача плана 5а): тождества, якорь, режимы, перевывод, интерфейс клетки,
точка, отсутствие чисел книги в коде.

β активов в книге 1.0 не выведена (рядов цен для правила семейства в фактах нет: `valuation.beta_u = null`), поэтому
клетки здесь считаются на копии книги с подставленной β — число теста, а не суждение модели: тождества и якорь от β не
зависят.
"""
from __future__ import annotations

import io
import json
import math
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "tools"))

from model.book import BookError, load_book, override  # noqa: E402
from model.book_schema import BLOCKS, CAPEX_LEVELS, REGIMES, WORLDS, validate_book  # noqa: E402
from model.core import Context, run_cell  # noqa: E402
from model.facts import core_facts, load_facts  # noqa: E402

TEST_BETA = 0.6          # β для проверок механики (не суждение книги)
TOL = 1e-9


@pytest.fixture(scope="module")
def cf():
    return core_facts(load_facts())


@pytest.fixture(scope="module")
def book():
    return load_book()


@pytest.fixture(scope="module")
def book_beta(book):
    return override(book, ["valuation.beta_u"], "value", TEST_BETA)


@pytest.fixture(scope="module")
def cells(book_beta, cf):
    ctx = Context(book_beta, cf)
    return ctx, {(w, r, c): run_cell(ctx, w, r, c) for w in WORLDS for r in REGIMES for c in CAPEX_LEVELS}


@pytest.fixture(scope="module")
def book_file(tmp_path_factory):
    import adopt_worlds
    p = tmp_path_factory.mktemp("book") / "assumptions.yaml"
    p.write_bytes((ROOT / "data/assumptions/assumptions.yaml").read_bytes())
    adopt_worlds.set_values(p, {("valuation", "beta_u"): TEST_BETA})
    return p


# ------------------------------------------------------------------------------------------ книга

def test_the_book_is_closed(book):
    validate_book(book)
    bad = dict(book, extra={"x": 1})
    with pytest.raises(BookError, match="незнакомые ключи"):
        validate_book(bad)
    with pytest.raises(BookError):
        validate_book(override(book, ["sbc.h1_share"], "value", 1.5))


def test_mean_of_regimes_is_the_center_rule(book):
    """§3.1, §13.1: среднее четырёх режимов каждого режимного суждения = правилу центра (цель маржи — центру,
    сдвиг роста — нулю), стресс ≤ медленный ≤ базовый ≤ сильный."""
    for b in BLOCKS:
        t = [book["margin"]["targets"][b][r] for r in REGIMES]
        assert abs(math.fsum(t) / 4 - book["margin"]["center"][b]) < TOL, b
        assert t == sorted(t), b
        s = [book["growth"]["shift"][b][r] for r in REGIMES]
        assert abs(math.fsum(s) / 4) < TOL, b
        assert s == sorted(s), b
    broken = override(book, ["margin.targets.C.base"], "value", book["margin"]["targets"]["C"]["base"] + 0.01)
    with pytest.raises(BookError, match="среднее режимов"):
        validate_book(broken)


def test_regimes_rule_keeps_the_center_on_skewed_ranges():
    from model.rederive import regimes
    for c, lo, hi in ((0.0, -0.04, 0.02), (0.08, 0.0, 0.15), (0.43, 0.31, 0.48), (0.5, 0.5, 0.5)):
        v = regimes(c, lo, hi)
        assert abs(math.fsum(v) / 4 - c) < 1e-12 and v == sorted(v)


def levels_book(book: dict, excludes_nwc: bool = True) -> dict:
    """Книга прежнего правила вложений (как книга 1.0) из книги с ключом `capex.rule: kappa`: доли уровней 9 / 11 / 13 %
    до 2030 г., сход к c_T к 2036 г., c_T по отдаче окна; `excludes_nwc` — ключ `terminal.reinvestment_excludes_nwc`."""
    import copy
    B = copy.deepcopy(book)
    C = B["capex"]
    for k in ("rule", "kappa_levels", "kappa_base_capex", "kappa_high_capex", "first_share", "floor_da"):
        C.pop(k)
    from model.book_schema import KAPPA_ONLY            # ключи книги 1.2, действующие только с правилом κ
    for path in KAPPA_ONLY:
        node = B
        for part in path[:-1]:
            node = node[part]
        node.pop(path[-1], None)
    B["joint"].pop("capex_prob_given_regime", None)
    C["levels"] = {"low": 0.09, "base": 0.11, "high": 0.13}
    C["converge_year"] = int(B["meta"]["last_period"][:4])
    if excludes_nwc:
        B["terminal"]["reinvestment_excludes_nwc"] = True
    validate_book(B)
    return B


def test_without_the_key_the_terminal_rule_is_the_previous_one(book, cf):
    """Ключ книги новой механики: без `capex.rule` — c_T по отдаче окна (§6.2); без
    `terminal.reinvestment_excludes_nwc` — прежнее правило с ΔNWC."""
    for nwc in (True, False):
        B = levels_book(book, nwc)
        x = run_cell(Context(B, cf), "H", "base", "base")
        t = x.terminal
        left = math.fsum(t["capex"][h] - t["da"][h] + (0.0 if nwc else t["dwc"][h]) for h in (0, 1))
        assert abs(left - x.g / B["terminal"]["roic"] * math.fsum(t["nopat"])) < 1e-6
        y30 = int(B["capex"]["levels_to"])
        for i, p in enumerate(x.periods):
            if int(p[:4]) <= y30:
                cx = x.group["capex"][i] + x.group["lease_new"][i]
                assert abs(cx - (0.11 + B["capex"]["lease_share"]) * x.group["revenue"][i]) < 1e-9


# ------------------------------------------------------------------------------- правило капиталоёмкости κ

def _r12(x, cf, book, i):
    """R12 на базе капиталоёмкости R° (§0, §4.9): книга 1.2 — без финсервисов, книга 1.1 — выручка группы."""
    from model.core import revenue_base
    rx = x.group.get("revenue_xfs", x.group["revenue"])
    prev = rx[i - 1] if i > 0 else revenue_base(book, cf, book["meta"]["anchor_period"])
    return rx[i] + prev


def test_kappa_first_half_is_the_share_of_the_year_minus_the_anchor(cells, cf, book_beta):
    """§4.9: капвложения 2026 г. = s × выручка 2026 г.: CAPEX(2026H2) = s × (R(2026H1) + R(2026H2)) − CAPEX(2026H1)."""
    a = book_beta["meta"]["anchor_period"]
    s = book_beta["capex"]["first_share"]
    for x in cells[1].values():
        r0 = x.group["revenue"][0]
        assert abs(x.group["capex"][0] - (s * (cf.rev["group"][a] + r0) - cf.capex[a])) < 1e-9
        assert abs(x.group["lease_new"][0] - book_beta["capex"]["lease_share"] * r0) < 1e-12


def test_kappa_capital_follows_the_line_and_stays_constant_after_2030(cells, cf, book_beta):
    """§4.9: FA / R12 идёт линейно от κ₁ (31.12.2026) к уровню клетки на конец 2030 г. и дальше постоянна; вложения не
    ниже φ × DA (граница в книге 1.1 не срабатывает)."""
    from model.core import kappa_line
    C = book_beta["capex"]
    y30 = int(C["levels_to"])
    for (w, r, c), x in cells[1].items():
        g = x.group
        k1 = g["fa"][0] / _r12(x, cf, book_beta, 0)
        assert abs(k1 - C["kappa_levels"]["low"]) < 1e-3          # κ₁ клетки ≈ низкий уровень (ожидание по мирам)
        for i in range(1, len(x.periods)):
            cx = g["capex"][i] + g["lease_new"][i]
            assert cx >= C["floor_da"] * g["da"][i] - 1e-9
            assert cx > C["floor_da"] * g["da"][i], (w, r, c, x.periods[i])
            k = g["fa"][i] / _r12(x, cf, book_beta, i)
            assert abs(k - kappa_line(book_beta, c, k1, i)) < 1e-12
            if int(x.periods[i][:4]) > y30:            # книга 1.3 (§4.9): на 30.06 — ниже линии на δ
                dip = C["h1_dip"] if C.get("season") == "h1_dip" and x.periods[i].endswith("H1") else 0.0
                assert abs(k - (C["kappa_levels"][c] - dip)) < 1e-12


def test_kappa_terminal_reinvests_growth_times_capital(cells, cf, book_beta):
    """§6.2: Σ (CX − DA) терминала = g × κ × R12(2036H2) = g × FA(2036H2); c_T — в замкнутом виде."""
    for (w, r, c), x in cells[1].items():
        t, g = x.terminal, x.group
        net = math.fsum(t["capex"][h] - t["da"][h] for h in (0, 1))
        rx = g.get("revenue_xfs", g["revenue"])         # R° (§0, §6.2)
        need = x.g * book_beta["capex"]["kappa_levels"][c] * (rx[-2] + rx[-1])
        assert abs(net - need) <= 1e-12 * need
        assert abs(net - x.g * g["fa"][-1]) <= 1e-9 * need
        if not book_beta["terminal"].get("gordon_dip"):     # книга 1.4 (§6.3): с провалом — система 2 × 2
            assert abs(t["capex"][0] - x.c_T * t["revenue"][0]) < 1e-9


def test_kappa_book_refuses_keys_of_the_previous_rule(book):
    """С ключом `capex.rule: kappa` ключи прежнего правила не действуют — книга их не принимает; без ключа — ключи
    правила κ не действуют."""
    import copy
    cases = []
    B = copy.deepcopy(book)
    B["capex"]["levels"] = {"low": 0.09, "base": 0.11, "high": 0.13}
    cases.append(B)
    B = copy.deepcopy(book)
    B["terminal"]["reinvestment_excludes_nwc"] = True
    cases.append(B)
    B = copy.deepcopy(book)
    B["capex"]["rule"] = "levels"
    cases.append(B)
    B = copy.deepcopy(book)
    B["capex"].pop("floor_da")
    cases.append(B)
    B = levels_book(book)
    B["capex"]["first_share"] = 0.11
    cases.append(B)
    B = copy.deepcopy(book)
    B["capex"]["kappa_levels"]["low"] = B["capex"]["kappa_levels"]["base"] + 0.01
    cases.append(B)
    for B in cases:
        with pytest.raises(BookError):
            validate_book(B)


def test_kappa_levels_are_derived_in_the_base_regime(book, cf):
    """§4.9, §17 (книга 1.2): клетка базового режима, миры слоя «свой взгляд»: средняя доля капвложений без новой
    аренды 2027–2030 к выручке без финсервисов R° базового уровня = доле окна (среднее 2025 г. и 12 мес.), высокого —
    зеркально низкому (среднее трёх уровней 25 / 50 / 25 = доле окна); низкий — κ на 31.12.2026."""
    from model.rederive import kappa_levels

    from model.facts import one_composition
    cf = one_composition(book, cf)                  # книга 1.4 (§0.5): факты одного состава

    def ro(keys):                                   # R° за период: R × Σ A–F / Σ A–G (§0)
        r = math.fsum(cf.rev["group"][q] for q in keys)
        return r * math.fsum(cf.rev[b][q] for b in "ABCDEF" for q in keys) / math.fsum(
            cf.rev[b][q] for b in "ABCDEFG" for q in keys)
    C = book["capex"]
    assert book["meta"]["balance_base"] == "ex_fs" and C["kappa_window"] == "ltm" and C["kappa_high"] == "mirror"
    ltm = ["2025Q4", "2026Q1", "2026Q2", "2026Q3"]
    window = 0.5 * (cf.capex["2025"] / ro(["2025"]) + math.fsum(cf.capex[q] for q in ltm) / ro(ltm))
    assert abs(C["kappa_base_capex"] - window) < 1e-9
    all_cells = C.get("kappa_target") == "all_cells"  # книга 1.3 (§4.9, Г3): доля окна — ожидание всех клеток
    low, lv, info = kappa_levels(book, cf, {"base": C["kappa_base_capex"], "high": None}, all_cells=all_cells)
    assert abs(low - C["kappa_levels"]["low"]) < 1e-9
    for c in ("base", "high"):
        assert abs(lv[c] - C["kappa_levels"][c]) < 1e-9
    if all_cells:
        d_b = info["d_b"]
        assert abs(info["expect"] - window) < 1e-9 and abs(info["share"]["base"] - d_b) < 1e-9
        assert abs(info["share"]["high"] - (2 * d_b - info["low_share"])) < 1e-9
        assert abs(C["kappa_high_capex"] - (2 * d_b - info["low_share"])) < 1e-9
    else:
        assert abs(info["share"]["base"] - window) < 1e-9 and abs(info["share"]["high"] - C["kappa_high_capex"]) < 1e-9
        assert abs(0.25 * info["low_share"] + 0.5 * info["share"]["base"] + 0.25 * info["share"]["high"] - window) < 1e-9
        g = info["group_shares"]                    # прогноз компании 11 % выручки группы — между низким и высоким
        assert g["low"] < 0.11 < g["high"]
    assert C["kappa_levels"]["low"] < C["kappa_levels"]["base"] < C["kappa_levels"]["high"]


def test_capex_level_is_conditional_on_the_regime(book, cf):
    """§3: P(клетки) = P(W) × P(ρ) × P(C | ρ); средние по режимам = 25 / 50 / 25; без ключа — P(C) во всех режимах."""
    from model.grid import evaluate
    J = book["joint"]
    assert J["capex_prob_given_regime"]["stress"]["high"] == 0 and J["capex_prob_given_regime"]["strong"]["low"] == 0
    for c in CAPEX_LEVELS:
        assert abs(math.fsum(J["regime_prob"][r] * J["capex_prob_given_regime"][r][c] for r in REGIMES)
                   - J["capex_prob"][c]) < 1e-12
    g = evaluate(book, cf)
    for x in g.cells:
        w, r, c = x.key.split("|")
        want = J["world_prob"][w] * J["regime_prob"][r] * J["capex_prob_given_regime"][r][c]
        assert abs(x.p["analytical"] - want) < 1e-15
    g0 = evaluate(levels_book(book), cf)
    for x in g0.cells:
        w, r, c = x.key.split("|")
        assert abs(x.p["analytical"] - J["world_prob"][w] * J["regime_prob"][r] * J["capex_prob"][c]) < 1e-15
    import copy
    B = copy.deepcopy(book)
    B["joint"]["capex_prob_given_regime"]["stress"] = {"low": 0.25, "base": 0.5, "high": 0.25}
    with pytest.raises(BookError):
        validate_book(B)


def test_rederive_is_a_fixed_point_of_the_book(book, cf):
    """Выведенные ключи книги — ровно выход правил §17 на фактах: руками не правлены, перевывод их не сдвигает."""
    from model.rederive import derive
    raw = yaml.safe_load((ROOT / "data/assumptions/assumptions.yaml").read_text(encoding="utf-8"))
    values, table, flags, _ = derive(raw, cf)
    assert len(values) == len(table) > 80
    for path, v in values.items():
        node = book
        for part in path:
            node = node[part]
        assert abs(node - v) < 1e-9, path


# ------------------------------------------------------------------------------------------ тождества

def test_blocks_and_exclusions_add_up_to_the_group_in_every_half_year(cells):
    _, C = cells
    for key, x in C.items():
        for i, _p in enumerate(x.periods):
            rev = math.fsum(x.blocks[b]["revenue"][i] for b in (*"ABCDEFG", "X"))
            eb = math.fsum(x.blocks[b]["ebitda"][i] for b in (*"ABCDEFG", "X")) + x.group["attack"][i]
            assert abs(rev - x.group["revenue"][i]) < 1e-9, (key, _p)
            assert abs(eb - x.group["ebitda"][i]) < 1e-9, (key, _p)


def test_the_flow_is_the_sum_of_its_lines(cells):
    _, C = cells
    for key, x in C.items():
        g = x.group
        for i in range(len(x.periods)):
            lines = (g["ebitda"][i] - g["sbc"][i] - g["tax"][i] - g["capex"][i] - g["lease_new"][i] - g["acq"][i]
                     - g["dwc"][i] - g["dkb"][i])
            assert abs(lines - g["fcff"][i]) < 1e-9, key
        t = x.terminal
        for h in (0, 1):
            assert abs(t["ebitda"][h] - t["sbc"][h] - t["tax"][h] - t["capex"][h] - t["dwc"][h] - t["dkb"][h]
                       - t["fcff"][h]) < 1e-9


def test_cell_value_is_its_parts_and_price_is_equity_over_divisor(cells):
    ctx, C = cells
    for x in C.values():
        assert abs(x.pv_explicit + x.pv_shield + x.pv_tv + x.pv_tv_shield - x.ev) < 1e-9
        assert 0.0 <= x.c_T <= 1.0
        eq = x.ev - x.claims["total"]
        assert abs(x.price - ctx.price_of(eq)) < 1e-9
        parts = [v for k, v in x.claims.items() if k.startswith("bridge.") or k in ("net_debt_nonbank", "rollover",
                                                                                  "dividends")]
        assert abs(math.fsum(parts) - x.claims["total"]) < 1e-9


def test_terminal_reinvestment_is_growth_over_return(book_beta, cf):
    """Прежнее правило §6.2: Σ (CX − DA) терминала = g / ROIC × Σ NOPAT (если c_T внутри (0; 1)); ΔNWC — отдельной
    строкой потока (ключ `terminal.reinvestment_excludes_nwc`); без ключа — Σ (CX − DA + ΔNWC)."""
    B = levels_book(book_beta)
    roic = B["terminal"]["roic"]
    k = 0.0 if B["terminal"].get("reinvestment_excludes_nwc") else 1.0
    ctx = Context(B, cf)
    for x in (run_cell(ctx, w, r, c) for w in WORLDS for r in REGIMES for c in CAPEX_LEVELS):
        t = x.terminal
        if 0 < x.c_T < 1:
            left = math.fsum(t["capex"][h] - t["da"][h] + k * t["dwc"][h] for h in (0, 1))
            assert abs(left - x.g / roic * math.fsum(t["nopat"])) < 1e-6


# ------------------------------------------------------------------------------------------ якорь

def test_the_core_stands_on_the_anchor_facts(cells, cf, book_beta):
    """Ядро на 1П2026 повторяет факты: A–G + исключения = группа 1П26 (устав, раздел 5); пути 2П26 растут от фактов
    2П25, маржа — от факта 1П26, оборотный капитал и амортизация — от фактов якоря."""
    a = cf.anchor
    tol = book_beta["checks"]["facts_identity_tol"]
    for m in ("rev", "ebitda"):
        s = getattr(cf, m)
        assert abs(math.fsum(s[b][a] for b in (*"ABCDEFG", "X")) - s["group"][a]) <= tol
    ctx, C = cells
    x = C[("H", "base", "base")]
    cpi = book_beta["worlds"]["H"]["cpi"][x.periods[0]]
    alpha = book_beta["growth"]["A"]["autoru_share"]
    for i, b in enumerate(BLOCKS):
        r0 = book_beta["path_2026h2"]["real_growth"][b]
        base = cf.rev[b]["2025H2"] * ((1 - alpha) if b == "A" else 1)
        assert abs(x.blocks[b]["revenue"][0] - base * (1 + r0) * (1 + cpi)) < 1e-9
    from model.core import nwc_fact                  # факт без банка по правилам книги (§4.10: вычет Авто.ру)
    assert abs(x.group["dwc"][0] - (x.group["nwc"][0] - nwc_fact(book_beta, cf, cf.facts_date))) < 1e-9
    sbc = book_beta["sbc"]
    assert abs(x.group["sbc"][0] - sbc["share"] * (1 - sbc["h1_share"]) * (cf.rev["group"][a] + x.group["revenue"][0])) < 1e-9
    assert x.claims["net_debt_nonbank"] == cf.nd_nonbank[cf.facts_date]
    assert abs(x.claims["net_debt_nonbank"] - (cf.nd_company + cf.bank_money)) < 1e-9


# ------------------------------------------------------------------------------------------ интерфейс

def test_cell_command_answers_the_interface(book_file):
    out = subprocess.run([sys.executable, "-B", "-m", "model.cell", "--world", "H", "--regime", "slow", "--capex",
                          "high", "--book", str(book_file), "--json"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8")
    assert out.returncode == 0, out.stderr
    j = json.loads(out.stdout)
    n = len(j["periods"])
    assert j["periods"][0] == "2026H2" and n == 21
    assert set(j["blocks"]) == {*"ABCDEFG", "X"}
    for b in j["blocks"].values():
        assert len(b["revenue"]) == len(b["ebitda"]) == n
    for k in ("revenue", "ebitda", "sbc", "da", "tax", "capex", "lease_new", "acq", "dwc", "dkb", "fcff", "interest",
              "shield"):
        assert len(j["group"][k]) == n, k
    for k in ("tv", "v0", "shares", "price"):
        assert isinstance(j[k], float), k
    assert "total" in j["claims"]


def test_cell_command_refuses_a_book_without_beta(tmp_path):
    import adopt_worlds
    p = tmp_path / "assumptions.yaml"
    p.write_bytes((ROOT / "data/assumptions/assumptions.yaml").read_bytes())
    text = p.read_text(encoding="utf-8")
    beta = yaml.safe_load(text)["valuation"]["beta_u"]
    p.write_text(text.replace(f"beta_u: {beta}", "beta_u: null", 1), encoding="utf-8")
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["valuation"]["beta_u"] is None
    out = subprocess.run([sys.executable, "-B", "-m", "model.cell", "--world", "N", "--regime", "base", "--capex",
                          "base", "--book", str(p), "--json"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8")
    assert out.returncode == 2 and "beta_u" in out.stderr


def test_point_lies_between_the_layers(book_beta, cf):
    from model.point import evaluate
    res = evaluate(book_beta, cf)
    lam = book_beta["joint"]["lambda"]
    assert abs(res["point"] - (res["low"] + lam * (res["high"] - res["low"]))) < 1e-9
    assert set(res["worlds"]) == set(WORLDS) and len(res["cells"]) == 36
    assert 0 < res["terminal_share"] < 1 and res["ev_ebitda_2027_point"] > 0


# ------------------------------------------------------------------------------------------ числа книги в коде

# константы методики (§0, §3.1, §5, §6, §7) и разряды печати
ALLOWED = {0, 1, 2, 3, 4, 6, 8, 0.5, 0.25, 0.75, 0.01, 100, 1000, 1e-4, 1e-12}


def _book_numbers(node, out):
    if isinstance(node, dict):
        for v in node.values():
            _book_numbers(v, out)
    elif isinstance(node, list):
        for v in node:
            _book_numbers(v, out)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out.add(float(node))


@pytest.mark.parametrize("module", ["model/core.py", "model/cell.py", "model/point.py", "model/facts.py"])
def test_no_book_numbers_in_core_code(book, module):
    nums = set()
    _book_numbers(book, nums)
    src = (ROOT / module).read_text(encoding="utf-8")
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NUMBER:
            v = float(eval(tok.string))  # noqa: S307 — литерал числа
            if v not in ALLOWED and (v in nums or v >= 1900):
                found.append((tok.start[0], tok.string))
    assert not found, f"{module}: числа книги или годы в коде: {found}"


def test_market_series_equal_build_from_primary():
    """Ряды β (`data/market/beta_inputs.json`) — сборка `ops/tools/build_market.py` из первички, не числа руками; без
    первички рядом (CI) — сверка состава и хэшей входов в самом файле."""
    import build_market
    data = json.loads(build_market.OUT.read_text(encoding="utf-8"))
    assert set(data["files_sha256"]) == {*build_market.STOCK_FILES, build_market.INDEX_FILE, "data/market/dividends.csv"}
    if build_market.DEFAULT_SRC.exists():
        assert build_market.OUT.read_text(encoding="utf-8") == build_market.build(build_market.DEFAULT_SRC)
    else:
        assert len(data["stock"]) > 100 and len(data["index"]) > 100



# ------------------------------------------------------------------------------------------ ось суждения 6 (§4.5, §9)

def _at_book(book, x=None):
    import copy
    B = copy.deepcopy(book)
    if x is None:
        B["f"]["at"].pop("position", None)
    else:
        B["f"]["at"]["position"] = float(x)
    validate_book(B)
    return B


def test_axis6_at_zero_is_the_previous_core_bit_for_bit(book, cf):
    for cell in (("H", "base", "base"), ("M", "stress", "high"), ("N", "strong", "low")):
        a = run_cell(Context(_at_book(book, 0.0), cf), *cell).to_json()
        b = run_cell(Context(_at_book(book), cf), *cell).to_json()
        assert a == b, cell


def test_axis6_edges_are_perpetual_expense_and_zero_from_the_edge_year(book, cf):
    """x = −1 — низ: E_at = −φ × R во всех полугодиях с 2027H1 и в терминале; x = +1 — верх: путь центра до
    `high_zero_from`, 0 с него; в 2026H2 F — факт квартала (§4.2), ось его не трогает."""
    at = book["f"]["at"]
    phi, z0, eo = at["perpetual_share"], at["high_zero_from"], book["f"]["other_share"]
    mid = run_cell(Context(_at_book(book, 0.0), cf), "H", "base", "base")
    low = run_cell(Context(_at_book(book, -1.0), cf), "H", "base", "base")
    high = run_cell(Context(_at_book(book, 1.0), cf), "H", "base", "base")
    for i, p in enumerate(low.periods):
        R = low.group["revenue"][i]
        f_low, f_high, f_mid = (x.blocks["F"]["ebitda"][i] for x in (low, high, mid))
        if i == 0:
            assert f_low == f_high == f_mid
            continue
        assert abs(f_low - (eo * R - phi * R)) < 1e-9, p
        at_mid = f_mid - eo * mid.group["revenue"][i]
        assert abs(f_high - eo * R - (at_mid if int(p[:4]) < z0 else 0.0)) < 1e-9, p
    RT = low.terminal["revenue"]
    assert low.terminal["ebitda"][0] < mid.terminal["ebitda"][0]                       # вечный расход и в терминале
    assert abs((mid.terminal["ebitda"][0] - low.terminal["ebitda"][0]) - phi * RT[0]) < 1e-6


def test_more_autonomous_expense_never_raises_the_price(book, cf):
    from model.point import evaluate
    prices = [evaluate(_at_book(book, x), cf)["point"] for x in (-1.0, -0.5, 0.0, 0.5, 1.0)]
    assert prices == sorted(prices)
    cell = [run_cell(Context(_at_book(book, x), cf), "M", "slow", "base").price for x in (-1.0, 0.0, 1.0)]
    assert cell == sorted(cell)
