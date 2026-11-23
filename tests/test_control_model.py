"""Второй счёт (`tests/independent_model.py`) — самопроверки и сверка с ядром (docs/MODEL.md §16).

* Без ядра: тождества клетки (A–G + X = группа, поток = сумма строк, EV = сумма частей), якорь 1П2026 = факты,
  среднее режимов = правило центра, установившаяся амортизация терминала = сумма когорт, «ухудшение не повышает
  цену», режим «на книге» воспроизводит свой счёт, интерфейс клетки печатает поля команды ядра. Независимость:
  второй счёт не импортирует ядро и не держит чисел фактов вне блока суждений.
* С ядром (метка `docs`; пока команды клетки ядра `model/cell.py` нет — пропуск с причиной): клетки через
  `python -m model.cell ... --json` против второго счёта на книге ядра (`--book`) и на своём перевыводе. Допуски:
  строки ≤ 10 % (пол знаменателя — 1 % выручки группы полугодия), V0 ≤ 3 %, требования ≤ 1 %. Совпадение бит в бит
  — подозрение на списывание, а не успех. Корень ядра — переменная `CONTROL_CORE_ROOT` (по умолчанию — эта копия).
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import independent_model as cm

SRC = cm.ROOT / "tests" / "independent_model.py"
ASOF = "2026-11-20"

ALLOWED_IMPORTS = {"__future__", "argparse", "copy", "datetime", "json", "math", "random", "sys", "pathlib", "statistics",
                   "yaml"}

# Числа-литералы второго счёта вне блока суждений JUDGMENTS и почему они не допущения и не факты.
STRUCTURAL = {
    0: "ноль", 1: "единица", 2: "полугодий в году; 2L", 3: "узел кривой 3 года (§0.2)", 4: "режимов (§3.1); пятница — день недели 4 (конец недели β, §17)",
    5: "узел кривой 5 лет; 5/6 — месяцы Авто.ру в 1П26 (§4.3)", 6: "длина ключа полугодия; 5/6",
    7: "месяц начала 2-го полугодия; дней в неделе (β)", 8: "u_k = (2k − 1)/8 (§3.1); 8/12 — месяцы Авто.ру в «12 мес.» (§17)",
    9: "столбец печати", 10: "узел кривой 10 лет (§0.2)", 12: "месяцев в году", 19: "столбец печати",
    20: "столбец печати", 31: "2L ≤ 30: сетка L до 15 лет (§17)", 100: "проценты", 1000: "млрд → млн (цена)",
    2024: "окно и полные годы окна (§0)", 2025: "окно, «12 мес.», ставка IT 2025 г. в подборе s (§17)",
    2026: "полугодие 2026H2 в подборе L (§17)", 2027: "путь A с 2027 г. (§4.3)",
    2030: "сдвиг режима 2027–2030 (§4.3), уровень капвложений до 2030 (§4.9)",
    0.5: "половина", 0.25: "четверть", 0.75: "три четверти (Гордон, §6.3)", 0.9: "P90 (§0.4)",
    0.1: "допуск якоря 0,1 млрд (§13.1)", 0.0001: "защита g = r − 0,0001 (§6)", 1e-12: "точность (§6.2, §13.1)",
    1e-09: "точность тождеств", 1e-06: "точность корня c_T",
    0.005: "ухудшение §13.3: выплаты акциями +0,5 п.п.",
    0.01: "ухудшение §13.3: маржа −1 п.п., κ +0,01; книга 1.3: отрезок D_b — доля окна ± 0,01 (§4.9)",
    0.02: "ухудшение §13.3: кривая +2 п.п.", 0.05: "ухудшение §13.3: налог +5 п.п.; книга 1.3: отрезок δ [0; 0,05] (§4.9)",
    50: "книга 1.3: не больше 50 кругов неподвижной точки δ и уровней κ (§4.9)",
    0.03: "книга 1.3: допуск самопроверки амортизации когорт года H при провале κ (прочтение 27)",
}

# Клетки сверки с ядром: углы сетки и база.
CORE_CELLS = [("N", "base", "base"), ("H", "stress", "low"), ("M", "strong", "high"),
              ("N", "slow", "high"), ("H", "base", "base"), ("M", "stress", "base")]

ROW_TOL, V0_TOL, CLAIMS_TOL = 0.10, 0.03, 0.01          # §16: второй счёт на своём перевыводе
# На книге ядра входы одни и те же — числа обязаны совпасть до точности счёта (отчёт проверяющего 112).
CORE_BOOK_TOL = 1e-9


@pytest.fixture(scope="module")
def model():
    """Свой счёт книги 1.3 (перевывод с неподвижной точкой δ и уровней κ — около минуты)."""
    F = cm.Facts()
    P, T = cm.build_params(F, version="1.3")
    return F, P, T


@pytest.fixture(scope="module")
def model14(model):
    """Свой счёт книги 1.4 по тексту методики 3f8e99a + 166eff9 (перевывод — около минуты)."""
    F = model[0]
    P, T = cm.build_params(F, version="1.4")
    return F, P, T


@pytest.fixture(scope="module")
def model12(model):
    """Свой счёт книги 1.2 (без ключей книги 1.3)."""
    F = model[0]
    P, T = cm.build_params(F, version="1.2")
    return F, P, T


@pytest.fixture(scope="module")
def pt(model):
    F, P, _ = model
    return cm.point(P, F, ASOF)


# ------------------------------------------------------------------------------------------------ независимость

def test_control_imports_nothing_from_core():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods <= ALLOWED_IMPORTS, f"лишние импорты: {sorted(mods - ALLOWED_IMPORTS)}"
    assert "model" not in mods


def test_control_has_no_fact_numbers_outside_judgments():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id in ("JUDGMENTS", "JUDGMENTS_11", "JUDGMENTS_13", "JUDGMENTS_14") for t in targets):
            skip.update(id(n) for n in ast.walk(node.value))
    bad = sorted({node.value for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                  and not isinstance(node.value, bool) and id(node) not in skip
                  and node.value not in STRUCTURAL})
    assert not bad, f"числа вне блока суждений без объяснения: {bad}"


# ------------------------------------------------------------------------------------------------ самопроверки

@pytest.mark.parametrize("version", ["1.4", "1.3", "1.2", "1.1"])
def test_selfcheck_is_green(model, model14, version):
    F, P, T = model14 if version == "1.4" else model
    if version not in ("1.3", "1.4"):
        P, T = cm.build_params(F, version=version)
    res = cm.selfcheck(P, F, ASOF, T)
    bad = [f"{name} [{note}]" for name, ok, note in res if not ok]
    assert not bad, "\n".join(bad)
    assert len(res) >= 14


@pytest.mark.parametrize("c,a,b", [(0.0, -0.03, 0.02), (0.08, 0.02, 0.15), (0.446, 0.36, 0.48), (0.5, 0.5, 0.5)])
def test_regime_mean_is_center(c, a, b):
    v = cm.regime_values(c, a, b)
    assert abs(sum(v.values()) / 4 - c) <= 1e-12
    assert v["stress"] <= v["slow"] <= v["base"] <= v["strong"]


def test_anchor_is_facts(model, pt):
    F, P, _ = model
    c = pt["cells"][0]
    # первое прогнозное полугодие растёт от фактов 2П25 (A — без Авто.ру), 2027H1 — от фактов 1П26
    W = P["worlds"][c["world"]]
    for i, b in enumerate(cm.FLOW):
        base = F.r(b, "2025H2") * ((1 - P["growth"]["A"]["autoru_share"]) if b == "A" else 1)
        want = base * (1 + P["path_2026h2"]["real_growth"][b]) * (1 + cm.traj(W["cpi"], 2026, 2))
        assert math.isclose(c["blocks"][b]["revenue"][0], want, rel_tol=1e-12)
    assert c["claims"]["net_debt_nonbank"] == pytest.approx(cm.val(F.bal["2026-06-30"]["net_debt_nonbank"]), abs=1e-9)
    eq = F.eq[P["meta"]["facts_date"]]
    want_n = cm.val(eq["shares_outstanding"]) + P["valuation"]["net_exercise_k"] * cm.val(eq["options_exercisable"])
    assert c["shares"] == pytest.approx(want_n, abs=1e-12)


@pytest.mark.parametrize("L", [6.0, 6.5, 7.0, 15.0])
@pytest.mark.parametrize("g", [0.0, 0.0556, 0.09])
def test_terminal_da_is_cohort_sum(L, g):
    c1, c2 = 100.0, 130.0
    twol = int(round(2 * L))
    # вложения на геометрическом пути: полугодие T_h года −k равно c_h / (1 + g)^k
    hist = []          # hist[j] — когорта j полугодий назад от T1 (j = 1 — 2036H2)
    for j in range(1, twol + 2):
        k = (j + 1) // 2
        hist.append(c2 / (1 + g) ** k if j % 2 == 1 else c1 / (1 + g) ** k)
    da1 = sum(hist[:twol]) / twol
    da2 = (c1 + sum(hist[:twol - 1])) / twol
    got1, got2 = cm.steady_da(c1, c2, g, L)
    assert math.isclose(got1, da1, rel_tol=1e-12) and math.isclose(got2, da2, rel_tol=1e-12)


def _bump(P, fn):
    Q = copy.deepcopy(P)
    fn(Q)
    return Q


TRAP_DRAWS = 40          # тест: медиана полосы на 40 прогонах; малая полоса (400) — команда `traps` второго счёта


@pytest.mark.parametrize("name", list(cm.WORSENINGS))
def test_worsening_does_not_raise_point(model, pt, name):
    """§13.3 и решение архитектора: «ухудшение не повышает цену» — по точке; клетки — советующий флаг."""
    F, P, _ = model
    Q = copy.deepcopy(P)
    cm.WORSENINGS[name](Q)
    worse = cm.point(Q, F, ASOF)
    assert worse["point"] <= pt["point"] + 1e-9


def test_worsening_does_not_raise_median(model):
    """То же по медиане полосы (те же случайные числа)."""
    F, P, _ = model
    res = cm.traps(P, F, ASOF, draws=TRAP_DRAWS)
    bad = {k: v for k, v in res.items() if not (v["ok_point"] and v["ok_median"])}
    assert not bad, bad


def test_book_mode_reproduces_own_count(model, tmp_path):
    import yaml
    F, P, _ = model
    path = tmp_path / "book.yaml"
    path.write_text(yaml.safe_dump(json.loads(json.dumps(P)), allow_unicode=True), encoding="utf-8")
    P2, T2 = cm.build_params(F, cm.load_book(path))
    assert not T2["book"]["missing"]
    a = cm.cell(P, F, "H", "slow", "high", ASOF)
    b = cm.cell(P2, F, "H", "slow", "high", ASOF)
    assert a["price"] == b["price"] and a["v0"] == b["v0"]


@pytest.mark.parametrize("mode", ["drop_points", "drop_returns"])
def test_beta_rule_is_stable_across_splice_readings(model, mode):
    """β по правилу §17 (ПРОЧТЕНИЕ 16): два прочтения склейки дают β активов в пределах 0,005 друг от друга."""
    F, P, _ = model
    b, info = cm.derive_beta(F, P, skip_mode=mode)
    assert info["beta.weeks"] > 140 and 0 < info["beta.debt_to_value"] < 1
    assert abs(b - P["valuation"]["beta_u"]) < 0.005


def test_asset_life_is_weighted_class_life(model):
    """Срок L — сроки классов МСФО по первоначальной стоимости, к ближайшему кратному 0,5 (§17)."""
    _, P, T = model
    assert abs(P["capex"]["asset_life_years"] - T["asset_life_weighted"]) <= 0.25
    assert (2 * P["capex"]["asset_life_years"]) % 1 == 0


def test_cell_cli_prints_core_interface(model):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cm.main(["cell", "--world", "N", "--regime", "base", "--capex", "base", "--asof", ASOF, "--json"])
    assert rc == 0
    c = json.loads(buf.getvalue())
    assert c["periods"][0] == "2026H2" and c["periods"][-1] == "2036H2"
    for b in ("A", "B", "C", "D", "E", "F", "G", "X"):
        assert set(c["blocks"][b]) >= {"revenue", "ebitda"}
    for k in ("revenue", "ebitda", "sbc", "da", "tax", "capex", "lease_new", "acq", "dwc", "dkb", "fcff",
              "interest", "shield"):
        assert len(c["group"][k]) == len(c["periods"]), k
    for k in ("tv", "v0", "shares", "price"):
        assert math.isfinite(c[k])
    assert "total" in c["claims"]


# ------------------------------------------------------------------------------------------------ сверка с ядром

CORE_ROOT = Path(os.environ.get("CONTROL_CORE_ROOT", str(cm.ROOT)))


_CORE_CACHE: dict = {}


def _core_cell(world, regime, capex, book=None):
    key = (world, regime, capex, str(book))
    if key in _CORE_CACHE:
        return _CORE_CACHE[key]
    if not (CORE_ROOT / "model" / "cell.py").exists():
        pytest.skip(f"команды клетки ядра нет ({CORE_ROOT / 'model' / 'cell.py'}): ядро — задача 5а; сверка "
                    "второго счёта ждёт его (CONTROL_CORE_ROOT — корень другой копии)")
    cmd = [sys.executable, "-m", "model.cell", "--world", world, "--regime", regime, "--capex", capex,
           "--asof", ASOF, "--json"]
    if book:
        cmd += ["--book", str(book)]
    out = subprocess.run(cmd, cwd=CORE_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    _CORE_CACHE[key] = json.loads(out.stdout)
    return _CORE_CACHE[key]


def _core_quick(book, draws):
    """Быстрые числа ядра (`model.quick`, чёрный ящик): медиана, полоса и точка на книге."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "quick.json"
        cmd = [sys.executable, "-B", "-m", "model.quick", "--draws", str(draws), "--json", str(out_path)]
        if book:
            cmd += ["--book", str(book)]
        out = subprocess.run(cmd, cwd=CORE_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-2000:]
        return json.loads(out_path.read_text(encoding="utf-8"))


def compare(core: dict, mine: dict, tol: float | None = None) -> dict:
    """Расхождения второго счёта с ядром по допускам §16 (`tol` — один допуск строк, V0 и требований вместо них).
    Возвращает {'bad': [...], 'notes': [...], 'bitwise': bool}."""
    row_tol, v0_tol, claims_tol = (tol, tol, tol) if tol is not None else (ROW_TOL, V0_TOL, CLAIMS_TOL)
    bad, notes, same = [], [], []
    assert core["periods"] == mine["periods"], (core["periods"][:3], mine["periods"][:3])
    rev = mine["group"]["revenue"]

    def rows(name, a, b):
        for i, (x, y) in enumerate(zip(a, b)):
            same.append(x == y)
            den = max(abs(x), 0.01 * abs(rev[i]))
            if abs(x - y) > row_tol * den:
                bad.append(f"{name} {mine['periods'][i]}: ядро {x:.3f}, второй счёт {y:.3f}")

    for b in ("A", "B", "C", "D", "E", "F", "G", "X"):
        for k in ("revenue", "ebitda"):
            rows(f"{b}.{k}", core["blocks"][b][k], mine["blocks"][b][k])
    g = mine["group"]
    for k in ("revenue", "ebitda", "sbc", "da", "tax", "acq", "dwc", "dkb", "fcff", "interest", "shield"):
        rows(f"group.{k}", core["group"][k], g[k])
    # вложения: capex — без новых договоров аренды, lease_new — они (model/README.md, «Интерфейс клетки»)
    rows("group.capex", core["group"]["capex"], g["capex"])
    rows("group.lease_new", core["group"]["lease_new"], g["lease_new"])
    for k in ("tv",):
        same.append(core[k] == mine[k])
        if abs(core[k] - mine[k]) > row_tol * abs(core[k]):
            bad.append(f"{k}: ядро {core[k]:.1f}, второй счёт {mine[k]:.1f}")
    same.append(core["v0"] == mine["v0"])
    if abs(core["v0"] - mine["v0"]) > v0_tol * abs(core["v0"]):
        bad.append(f"v0: ядро {core['v0']:.1f}, второй счёт {mine['v0']:.1f}")
    ct, mt = core["claims"]["total"], mine["claims"]["total"]
    same.append(ct == mt)
    if abs(ct - mt) > claims_tol * abs(ct):
        bad.append(f"claims.total: ядро {ct:.2f}, второй счёт {mt:.2f}")
    notes.append(f"цена: ядро {core['price']:.1f}, второй счёт {mine['price']:.1f}")
    return {"bad": bad, "notes": notes, "bitwise": all(same)}


POSITIONS = [0.0, -1.0, 1.0]          # ось суждения 6 (§4.5): центр, низ «вечный расход», верх «ноль с 2028»


@pytest.fixture(scope="module")
def core_books(tmp_path_factory):
    """Книги ядра с положением на пути расхода автономных технологий x (`f.at.position`)."""
    import yaml
    src = CORE_ROOT / "data" / "assumptions" / "assumptions.yaml"
    if not src.exists():
        return {}
    base = cm.load_book(src)
    out = {}
    for x in POSITIONS:
        b = copy.deepcopy(base)
        b.setdefault("f", {}).setdefault("at", {})["position"] = x
        path = tmp_path_factory.mktemp("book") / f"book_x{x:+.0f}.yaml"
        path.write_text(yaml.safe_dump(b, allow_unicode=True, sort_keys=False), encoding="utf-8")
        out[x] = path
    return out


@pytest.fixture(scope="module")
def own_for_core(model, model14, core_books):
    """Свой перевывод той же версии, что книга ядра (§17: книга без ключей новой версии — прежние правила)."""
    F, P, T = model
    version = cm.book_version(cm.load_book(core_books[0.0])) if core_books else "1.3"
    if version == "1.3":
        return F, P, T
    if version == "1.4":
        return model14
    Pv, Tv = cm.build_params(F, version=version)
    return F, Pv, Tv


def _with_position(P, x):
    Q = copy.deepcopy(P)
    Q["f"]["at"]["position"] = x
    return Q


ALL_CELLS = [(w, r, c) for w in cm.WORLDS for r in cm.REGIMES for c in cm.CAPEX_LEVELS]


@pytest.mark.docs
@pytest.mark.parametrize("world,regime,capex", ALL_CELLS)
def test_all_cells_match_core_on_core_book(model, core_books, world, regime, capex):
    """Все 36 клеток (x = 0): второй счёт на книге ядра против ядра до 1e-9 (строки, V0, требования)."""
    core = _core_cell(world, regime, capex, core_books.get(0.0))
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_books[0.0]))
    res = compare(core, cm.cell(P, F, world, regime, capex, ASOF), tol=CORE_BOOK_TOL)
    assert not res["bad"], "\n".join(res["bad"][:20] + res["notes"])


@pytest.mark.docs
@pytest.mark.parametrize("x", POSITIONS)
@pytest.mark.parametrize("world,regime,capex", CORE_CELLS)
def test_cell_matches_core_on_core_book(model, core_books, world, regime, capex, x):
    """Проход клетки: второй счёт на выведенных значениях книги ядра против ядра."""
    core = _core_cell(world, regime, capex, core_books.get(x))
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_books[x]))
    res = compare(core, cm.cell(P, F, world, regime, capex, ASOF), tol=CORE_BOOK_TOL)
    assert not res["bitwise"], "совпадение с ядром бит в бит — подозрение на общий код"
    assert not res["bad"], "\n".join(res["bad"][:20] + res["notes"])


@pytest.mark.docs
@pytest.mark.parametrize("x", POSITIONS)
@pytest.mark.parametrize("world,regime,capex", CORE_CELLS)
def test_cell_matches_core_on_own_derivation(own_for_core, core_books, world, regime, capex, x):
    """Перевывод и проход вместе: второй счёт на своих выведенных ключах против ядра."""
    core = _core_cell(world, regime, capex, core_books.get(x))
    F, P, _ = own_for_core
    res = compare(core, cm.cell(_with_position(P, x), F, world, regime, capex, ASOF))
    assert not res["bitwise"], "совпадение с ядром бит в бит — подозрение на общий код"
    assert not res["bad"], "\n".join(res["bad"][:20] + res["notes"])


def core_point(P, book_path):
    """Точка ядра: 36 клеток команды клетки ядра, сложенные по слоям книги (§8)."""
    cells = [dict(c, capex_level=c["capex"])
             for c in (_core_cell(w, r, k, book_path) for w in cm.WORLDS for r in cm.REGIMES for k in cm.CAPEX_LEVELS)]
    ly = cm.layers(P, cells)
    lam = P["joint"]["lambda"]
    return ly, ly["macro_neutral"]["price"] + lam * (ly["analytical"]["price"] - ly["macro_neutral"]["price"])


@pytest.mark.docs
@pytest.mark.parametrize("x", POSITIONS)
def test_point_matches_core_cells(own_for_core, core_books, x):
    """Точка: 36 клеток ядра против точки второго счёта на книге ядра (до 1e-9) и на своём перевыводе (V0 ≤ 3 %)."""
    _core_cell("N", "base", "base")            # пропуск, если ядра нет
    F, P_own, _ = own_for_core
    P, _ = cm.build_params(F, cm.load_book(core_books[x]))
    core_layers, cpoint = core_point(P, core_books[x])
    for P_mine, v0_tol, claims_tol in ((P, CORE_BOOK_TOL, CORE_BOOK_TOL), (_with_position(P_own, x), V0_TOL, CLAIMS_TOL)):
        mine = cm.point(P_mine, F, ASOF)
        for name in cm.LAYERS:
            a, b = core_layers[name], mine["layers"][name]
            assert abs(a["v0"] - b["v0"]) <= v0_tol * abs(a["v0"]), name
            assert abs(a["claims"] - b["claims"]) <= claims_tol * abs(a["claims"]), name
        assert abs(cpoint - mine["point"]) <= v0_tol * abs(cpoint), (cpoint, mine["point"])


@pytest.fixture(scope="module")
def core_quick_book(core_books):
    _core_cell("N", "base", "base")            # пропуск, если ядра нет
    return _core_quick(core_books[0.0], TRAP_DRAWS)


@pytest.mark.docs
def test_point_matches_core_point(model, core_books, core_quick_book):
    """Точка ядра (`model.quick`, его веса клеток) против точки второго счёта на книге ядра до 1e-9."""
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_books[0.0]))
    mine = cm.point(P, F, ASOF)
    core = core_quick_book["point"]
    for k_core, k_mine in (("central", "point"), ("low", "low"), ("high", "high")):
        assert abs(core[k_core] - mine[k_mine]) <= CORE_BOOK_TOL * abs(core[k_core]), (k_core, core[k_core], mine[k_mine])


@pytest.mark.docs
def test_band_matches_core_band(model, core_books, core_quick_book):
    """Полоса §9 на тех же случайных числах: квантили второго счёта на книге ядра = ядру (40 прогонов) до 1e-9."""
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_books[0.0]))
    mine = cm.band(P, F, TRAP_DRAWS, ASOF)
    for k in ("p10", "p25", "median", "p75", "p90", "mean", "p_below"):
        a, b = core_quick_book["stats"][k], mine[k]
        assert abs(a - b) <= CORE_BOOK_TOL * max(1.0, abs(a)), (k, a, b)


@pytest.mark.docs
def test_kappa_levels_match_core_book(own_for_core, core_books):
    """Уровни κ своего перевывода (§4.9, §17) против книги ядра: до точности записи книги (10 знаков), не бит в бит;
    книга 1.2 — и выведенная цель высокого уровня `kappa_high_capex` (зеркально низкому)."""
    _core_cell("N", "base", "base")
    _, P, _ = own_for_core
    book = cm.load_book(core_books[0.0])
    for lv in cm.CAPEX_LEVELS:
        a, b = book["capex"]["kappa_levels"][lv], P["capex"]["kappa_levels"][lv]
        assert abs(a - b) <= 1e-9, (lv, a, b)
        if cm.book_version(book) != "1.4":       # книга 1.4 (§4.9, 3f8e99a): уровни — 10 знаков с обеих сторон
            assert a != b
    for key in ("kappa_base_capex", "kappa_high_capex", "h1_share", "h1_dip"):     # книга 1.3 — и T, δ (§4.9)
        if key in book["capex"] or key in P["capex"]:
            assert abs(book["capex"][key] - P["capex"][key]) <= 1e-9, (key, book["capex"][key], P["capex"][key])
    assert abs(book["terminal"]["roic"] - P["terminal"]["roic"]) <= 1e-9
    if "kappa_base_cell_capex" in book["capex"]:          # книга 1.4 (§14): D_b — перевывод пишет в книгу
        assert abs(book["capex"]["kappa_base_cell_capex"] - own_for_core[2]["kappa.D_b"]) <= 1e-9


@pytest.mark.docs
def test_all_derived_match_core_book(own_for_core, core_books):
    """Все выведенные числа книги ядра (§17) против своего перевывода той же версии: до 1e-9 (книга пишет 10 знаков)."""
    _core_cell("N", "base", "base")
    F, P, _ = own_for_core
    book = cm.load_book(core_books[0.0])
    keys = cm._leaf_paths(cm.derive(F, P)[0]) + [f"capex.kappa_levels.{k}" for k in cm.CAPEX_LEVELS]
    keys += [k for k in ("capex.kappa_high_capex", "capex.h1_share", "capex.h1_dip") if cm.dget(P, k, None) is not None]
    n = 0
    for k in keys:
        a, b = cm.dget(P, k), cm.dget(book, k)
        if isinstance(a, dict):
            pairs = [(f"{k}.{kk}", a[kk], b[kk]) for kk in a]
        elif isinstance(a, list):
            pairs = [(f"{k}[{i}]", x, y) for i, (x, y) in enumerate(zip(a, cm._norm_range(b)))]
        else:
            pairs = [(k, a, b)]
        for name, x, y in pairs:
            n += 1
            assert abs(x - y) <= 1e-9, (name, x, y)
    assert n >= 90


@pytest.mark.docs
@pytest.mark.parametrize("name", list(cm.WORSENINGS))
def test_trap_band_matches_core_band_on_worsened_book(model, core_books, tmp_path, name):
    """Ловушка по медиане: полоса второго счёта на ухудшенной книге ядра = быстрым числам ядра на той же книге."""
    import yaml
    _core_cell("N", "base", "base")
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_books[0.0]))
    Q = copy.deepcopy(P)
    cm.WORSENINGS[name](Q)
    book = cm.load_book(core_books[0.0])
    for path in ("worlds.N.zero_curve", "worlds.H.zero_curve", "worlds.M.zero_curve", "margin.targets",
                 "capex.kappa_levels", "sbc.share", "tax.general", "tax.it_rate"):
        cm.dset(book, path, copy.deepcopy(cm.dget(Q, path)))
    if name == "margins-1pp":        # книга ядра требует среднее режимов = центр (§13.1); клетка центр не читает
        for b in cm.FLOW:
            book["margin"]["center"][b] -= 0.01
    path = tmp_path / f"book_{name}.yaml"
    path.write_text(yaml.safe_dump(book, allow_unicode=True, sort_keys=False), encoding="utf-8")
    core = _core_quick(path, TRAP_DRAWS)
    mine_pt, mine_b = cm.point(Q, F, ASOF), cm.band(Q, F, TRAP_DRAWS, ASOF)
    assert abs(core["point"]["central"] - mine_pt["point"]) <= CORE_BOOK_TOL * abs(mine_pt["point"])
    assert abs(core["stats"]["median"] - mine_b["median"]) <= CORE_BOOK_TOL * abs(mine_b["median"])


FORK_PAIRS = [("it_tax", 1, "it_tax_low"), ("it_tax", 2, "it_tax_high"), ("stress", 1, "stress"),
              ("ecom_margin", 1, "ecom_breakeven"), ("ecom_margin", 2, "ecom_ozon"), ("bank_money", 1, "bank_cash"),
              ("autonomous", 1, "at_low"), ("autonomous", 2, "at_high")]


@pytest.mark.docs
@pytest.mark.slow
def test_forks_and_gates_match_core_release(model, core_books, tmp_path):
    """Развилки §13.5 (малая полоса, 400 прогонов) и гейты §13.2: второй счёт на книге ядра против быстрого выпуска
    ядра (`ops/build_release.py --book --fast`, чёрный ящик). Допуск — 1e-9 доли; выпуск печатает 9 значащих цифр,
    поэтому у цены моста и значений гейтов пол — точность печати."""
    _core_cell("N", "base", "base")
    out = tmp_path / "release.json"
    run = subprocess.run([sys.executable, "-B", "ops/build_release.py", "--book", "--fast", "--out", str(out)],
                         cwd=CORE_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    rel = json.loads(out.read_text(encoding="utf-8"))
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_books[0.0]))
    mine = cm.forks(P, F, ASOF)
    assert abs(rel["headline"]["central"] - mine["headline"]) <= 0.005 + CORE_BOOK_TOL * abs(mine["headline"])
    core_f = {f["name"]: f for f in rel["checks"]["forks"]}
    for name, i, key in FORK_PAIRS:
        a = core_f[name]["variants"][i]["price"]
        assert abs(a - mine[key]) <= max(CORE_BOOK_TOL * abs(a), 5e-7), (key, a, mine[key])
    g = cm.gates(P, F, cm.point(P, F, ASOF))
    core_g = {x["name"]: x for x in rel["checks"]["gates"]}
    close = lambda a, b: abs(a - b) <= 5e-9 * max(1.0, abs(a))
    for name in ("ebitda_2026", "tax_rate_2026_27", "terminal_share", "margin_range", "capex_range"):
        assert close(core_g[name]["low"], g[name]["min"]) and close(core_g[name]["high"], g[name]["max"]), name
    for name in ("broker_ebitda_2027", "roic_increment", "terminal_share_point"):
        assert close(core_g[name]["value"], g[name]["value"]), (name, core_g[name]["value"], g[name]["value"])
    # §8 (81c35fa): V0 точки — по §7.3 с D слоя «свой взгляд» (прочтение 23 снято текстом)
    assert close(core_g["ev_ebitda_2027"]["value"], g["ev_ebitda_2027"]["value"]), (core_g["ev_ebitda_2027"]["value"],
                                                                                 g["ev_ebitda_2027"]["value"])
    fl = g["flow_9m26"]
    if fl.get("value") is not None:              # книга 1.2: проверка потока 9М26 считается (§13.2)
        assert close(core_g["flow_9m26"]["value"], fl["value"]), (core_g["flow_9m26"], fl["value"])
        assert core_g["flow_9m26"]["mass"] == fl["mass"], (core_g["flow_9m26"], fl["mass"])
    else:
        assert core_g["flow_9m26"].get("value") is None, core_g["flow_9m26"]
    for name in ("ebitda_2026", "tax_rate_2026_27", "broker_ebitda_2027", "roic_increment", "terminal_reinvest",
                 "ev_ebitda_2027", "terminal_share", "terminal_share_point", "margin_range", "capex_range",
                 "real_rate", "equity_cushion"):
        assert abs(core_g[name]["mass"] - g[name]["mass"]) <= 1e-12, (name, core_g[name]["mass"], g[name]["mass"])


def test_axis6_position_orders_price(model):
    """Ось 6: низ (вечный расход) ≤ центр ≤ верх (ноль с 2028); x = 0 — то же, что книга без ключа."""
    F, P, _ = model
    pts = {x: cm.point(_with_position(P, x), F, ASOF)["point"] for x in (-1.0, -0.5, 0.0, 0.5, 1.0)}
    assert pts[-1.0] < pts[-0.5] < pts[0.0] < pts[0.5] < pts[1.0]
    Q = copy.deepcopy(P)
    del Q["f"]["at"]["position"]
    assert cm.cell(Q, F, "N", "base", "base", ASOF)["price"] == cm.cell(P, F, "N", "base", "base", ASOF)["price"]


@pytest.mark.docs
def test_book_10_without_kappa_key_matches_core_point():
    """Книга 1.0 (без ключей правила κ и уровня при режиме): второй счёт считает прежним правилом — точка ядра
    913ee3e 3 248,0843683629 ₽ (отчёт 203: книга 1.0 после правки бит в бит)."""
    from tests.old_books import old_book
    F = cm.Facts()
    P, _ = cm.build_params(F, old_book("1.0"))           # копия книги из коммита 913ee3e
    assert P["capex"].get("rule") is None and "capex_prob_given_regime" not in P["joint"]
    assert abs(cm.point(P, F, ASOF)["point"] - 3248.0843683629164) <= 1e-9 * 3248.0843683629164


# ------------------------------------------------------------------------------------------------ книга 1.2 и 1.1

@pytest.fixture(scope="module")
def core_book_11():
    """Книга 1.1 (main 079bad5; копия — `tests/old_books.py`) — прежняя сверка: ядро без ключей книги 1.2 считает её
    как раньше."""
    from tests.old_books import path
    return path("1.1")


@pytest.mark.docs
@pytest.mark.parametrize("world,regime,capex", ALL_CELLS)
def test_all_cells_match_core_on_book_11(model, core_books, core_book_11, world, regime, capex):
    """Книга 1.1 после правки: все 36 клеток второго счёта на книге 1.1 против ядра на ней же до 1e-9."""
    if core_books and cm.book_version(cm.load_book(core_books[0.0])) == "1.1":
        pytest.skip("книга ядра — 1.1: та же сверка, что test_all_cells_match_core_on_core_book")
    core = _core_cell(world, regime, capex, core_book_11)
    F, _, _ = model
    book = cm.load_book(core_book_11)
    assert cm.book_version(book) == "1.1"
    P, _ = cm.build_params(F, book)
    res = compare(core, cm.cell(P, F, world, regime, capex, ASOF), tol=CORE_BOOK_TOL)
    assert not res["bad"], "\n".join(res["bad"][:20] + res["notes"])


@pytest.mark.docs
def test_book_11_point_as_before(model, core_book_11):
    """Книга 1.1 считается как раньше: точка на книге 1.1 = 3 074,024393196355 ₽, свой перевывод 1.1 — 3 074,024392350024
    (отчёт 204); уровни κ своего перевывода 1.1 — прежние."""
    F, _, _ = model
    P, _ = cm.build_params(F, cm.load_book(core_book_11))
    assert abs(cm.point(P, F, ASOF)["point"] - 3074.024393196355) <= 1e-9 * 3074.0
    P11, _ = cm.build_params(F, version="1.1")
    assert abs(cm.point(P11, F, ASOF)["point"] - 3074.024392350024) <= 1e-9 * 3074.0
    for lv, k in (("low", 0.30368504687919), ("base", 0.32329646246420), ("high", 0.35169064092731)):
        assert abs(P11["capex"]["kappa_levels"][lv] - k) <= 1e-12, lv


def test_book12_keys_switch_rules(model12):
    """Каждый ключ книги 1.2 включает своё правило: без ключей — книга 1.1 (§17); версия книги — по ключам."""
    F, P, _ = model12
    J11 = cm.judgments("1.1")
    assert cm.book_version(J11) == "1.1" and cm.book_version(cm.judgments("1.2")) == "1.2"
    for key in cm.BOOK12_KEYS:
        assert cm.dget(J11, key, None) is None, key
        assert cm.dget(P, key, None) is not None, key
    axes = {ax["key"]: ax for ax in J11["valuation"]["uncertainty"]["axes"]}
    assert axes["sbc"]["range"] == "sbc.range" and axes["it_tax"]["range"] == "tax.it_rate_lt_range"
    assert axes["terminal_growth"]["paths"] == ["terminal.real_growth"]
    assert all(r["fact"] not in cm.BOOK12_BRIDGE for r in J11["bridge"]["include"])
    assert J11["capex"]["kappa_high_capex"] == 0.11


def test_offset_edges_move_with_center(model):
    """§9 (книга 1.2): края осей 8 и 9 — сдвиги от центра; ухудшение §13.3 сдвигает всю ось."""
    F, P, _ = model
    axes = {ax["key"]: ax for ax in P["valuation"]["uncertainty"]["axes"]}
    a, b = cm.axis_edges(P, axes["sbc"])
    c = P["sbc"]["share"]
    assert math.isclose(a, c - 0.0083, abs_tol=1e-15) and math.isclose(b, c + 0.0117, abs_tol=1e-15)
    Q = copy.deepcopy(P)
    cm.WORSENINGS["sbc+0.5pp"](Q)
    a2, b2 = cm.axis_edges(Q, axes["sbc"])
    assert math.isclose(a2 - a, 0.005, abs_tol=1e-15) and math.isclose(b2 - b, 0.005, abs_tol=1e-15)
    a, b = cm.axis_edges(P, axes["it_tax"])
    assert math.isclose(a, 0.05, abs_tol=1e-15) and math.isclose(b, 0.25, abs_tol=1e-15)
    assert axes["terminal_growth"]["paths"] == ["terminal.real_growth", "growth.lt_real"]
    assert P["growth"]["lt_real"] == P["terminal"]["real_growth"]


@pytest.mark.parametrize("date", ["2026-06-30", "2025-12-31"])
def test_nwc_lines_reproduce_nwc_fact(model, date):
    """Оценка оборотного капитала по строкам ОФП (§13.2) на датах с полным раскрытием и своими долями x, y = факт
    `nwc_nonbank` (состав §4.10) — проверка прочтения формулы, которой считается 30.09.2026."""
    F, _, _ = model
    assert abs(cm.nwc_from_lines(F, date, date) - cm.val(F.bal[date]["nwc_nonbank"])) <= 1e-9


@pytest.mark.parametrize("date", ["2026-06-30", "2025-12-31"])
def test_nwc_lines_with_income_tax_in_nwc(model, date):
    """Ключ `nwc.income_tax_place: nwc` (§4.10, §13.2, 40f36e7): по строкам ОФП без авансов по налогу в x и с y = 1 —
    то же, что факт − налог к уплате + авансы."""
    F, _, _ = model
    J = {"nwc": {"income_tax_place": "nwc"}}
    assert abs(cm.nwc_from_lines(F, date, date, tax_in_nwc=True) - cm.nwc_fact(F, J, date)) <= 1e-9


@pytest.mark.docs
@pytest.mark.parametrize("place", ["nwc", "bridge"])
def test_income_tax_place_matches_core(model, core_books, tmp_path, place):
    """Место налога на прибыль к уплате (40f36e7): книга 1.2 с `nwc` и `bridge` — перевывод ядра (`model.rederive
    --book`, чёрный ящик) и быстрые числа ядра против второго счёта на той же книге до 1e-9; доли оборотного — свои."""
    _core_cell("N", "base", "base")
    src = cm.load_book(core_books[0.0])
    if cm.book_version(src) != "1.2":
        pytest.skip("книга ядра — не 1.2")
    text = (CORE_ROOT / "data" / "assumptions" / "assumptions.yaml").read_text(encoding="utf-8")
    assert text.count("  income_tax_place: none ") == 1
    path = tmp_path / f"book_tax_{place}.yaml"
    path.write_text(text.replace("  income_tax_place: none ", f"  income_tax_place: {place} "), encoding="utf-8")
    run = subprocess.run([sys.executable, "-B", "-m", "model.rederive", "--book", str(path)], cwd=CORE_ROOT,
                         capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    F, _, _ = model
    book = cm.load_book(path)
    P, _ = cm.build_params(F, book)
    J = cm.judgments("1.2")
    J["nwc"]["income_tax_place"] = place
    own = cm.derive(F, cm.deep_merge(J, cm.worlds_from_record()))[0]
    for key in ("share_h1", "share_h2"):
        assert abs(book["nwc"][key] - own["nwc"][key]) <= 1e-9, (key, book["nwc"][key], own["nwc"][key])
    core = _core_quick(path, TRAP_DRAWS)
    mine_pt, mine_b = cm.point(P, F, ASOF), cm.band(P, F, TRAP_DRAWS, ASOF)
    assert abs(core["point"]["central"] - mine_pt["point"]) <= CORE_BOOK_TOL * abs(mine_pt["point"])
    assert abs(core["stats"]["median"] - mine_b["median"]) <= CORE_BOOK_TOL * abs(mine_b["median"])


# ------------------------------------------------------------------------------------------------ книга 1.3

SMALL_DRAWS = 400        # малая полоса §9 (семя книги 20261123)


def test_book12_numbers_as_before(model12):
    """Без ключей книги 1.3 второй счёт считает книгу 1.2 как раньше (отчёт 302): своя точка 3 188,1237307523 ₽,
    медиана малой полосы 2 981,575 ₽, уровни κ прежние."""
    F, P, _ = model12
    assert cm.book_version(P) == "1.2"
    assert abs(cm.point(P, F, ASOF)["point"] - 3188.123730752277) <= 1e-9 * 3188.0
    assert abs(cm.band(P, F, SMALL_DRAWS, ASOF)["median"] - 2981.575) <= 1e-9 * 2981.0
    for lv, k in (("low", 0.32500958453460066), ("base", 0.37003836689290925), ("high", 0.41506714925152477)):
        assert abs(P["capex"]["kappa_levels"][lv] - k) <= 1e-12, lv


def test_book13_is_book12_plus_keys():
    """Книга 1.3 = книга 1.2 + ключи «Ключи книги 1.3» (§17), без `tax.base_rule`; правки книги без ключа — коридор
    налога, граница оси 9, место налога к уплате, строка моста. Убрав их, получаем книгу 1.2."""
    J12, J13 = cm.judgments("1.2"), cm.judgments("1.3")
    assert cm.book_version(J13) == "1.3" and cm.book_version(J12) == "1.2"
    for key in cm.BOOK13_KEYS:
        assert cm.dget(J12, key, None) is None and cm.dget(J13, key, None) is not None, key
    assert cm.dget(J13, "tax.base_rule", None) is None and J12["tax"]["base_rule"] == "f_other"
    axes = {ax["key"]: ax for ax in J13["valuation"]["uncertainty"]["axes"]}
    assert axes["it_tax"]["bounds"] == [0.0, 0.25]
    assert J13["checks"]["tax_2026_27"] == [0.202, 0.282] and J13["nwc"]["income_tax_place"] == "bridge"
    assert [r["fact"] for r in J13["bridge"]["include"]][-1] == "onca_net_lease_investment"
    back = copy.deepcopy(J13)
    for key in cm.BOOK13_KEYS:
        parts = key.split(".")
        cm.dget(back, ".".join(parts[:-1])).pop(parts[-1])
    for path in ("tax.base_rule", "checks.tax_2026_27", "nwc.income_tax_place", "bridge.include",
                 "valuation.uncertainty.axes"):
        cm.dset(back, path, copy.deepcopy(cm.dget(J12, path)))
    if back.get("bank") == {}:
        back.pop("bank")
    assert back == J12


def test_book13_own_numbers(model):
    """Свой счёт книги 1.3 по тексту 1ea268e (часть 1 задачи 402): точка, малая полоса, уровни κ, δ, T, D_b. Ориентир
    проверки решений (12в, без щита хвоста когорт) — медиана 2 906,875, точка 3 101,486: медиана совпала, точка ниже на
    0,012 ₽ — цена щита хвоста когорт (ключ `terminal.shield_cohort`)."""
    F, P, T = model
    assert abs(cm.point(P, F, ASOF)["point"] - 3101.473847401081) <= 1e-9 * 3101.0
    assert abs(cm.band(P, F, SMALL_DRAWS, ASOF)["median"] - 2906.875) <= 1e-9 * 2906.0
    for lv, k in (("low", 0.320677891853522), ("base", 0.3518096842384248), ("high", 0.3829414766236366)):
        assert abs(P["capex"]["kappa_levels"][lv] - k) <= 1e-11, lv
    assert abs(P["capex"]["h1_dip"] - 0.017915898519640906) <= 1e-11
    assert abs(P["capex"]["h1_share"] - 0.3303157399549002) <= 1e-12
    assert abs(T["kappa.D_b"] - 0.11245352417508883) <= 1e-11
    assert abs(P["capex"]["kappa_high_capex"] - (2 * T["kappa.D_b"] - T["kappa.share_at_low"])) <= 1e-15
    Q = copy.deepcopy(P)
    del Q["terminal"]["shield_cohort"]
    assert abs(cm.point(Q, F, ASOF)["point"] - 3101.486) <= 0.0005


def test_book13_cell_rules(model):
    """Правила книги 1.3 на клетке: остаток якоря ≈ 87,3 млрд (волна 2: 87,29), провал κ на 30.06, прирост капитала
    банка 2026H2 от факта, налог нетто-исполнения и долгосрочная аренда в мосте (§4.6, §4.9, §7.1)."""
    F, P, _ = model
    c = cm.cell(P, F, "N", "base", "base", ASOF)
    kp = c["kappa"]
    assert abs(kp["anchor_rest"] - 87.29) <= 0.05
    assert kp["h1_dip"] == P["capex"]["h1_dip"] > 0
    d = "2026-06-30"
    kappa = P["bank"]["capital_to_revenue"]
    exp = kappa * (c["blocks"]["G"]["revenue"][0] + F.r("G", "2026H1")) - F.b(d, "bank_capital_f123")
    assert abs(c["group"]["dkb"][0] - exp) <= 1e-12
    nxt = 0.111 * cm.val(F.eq[d]["options_exercisable"]) * 3277.5 / 1000
    assert abs(c["claims"]["bridge.net_exercise_tax"] - nxt) <= 1e-12
    assert c["claims"]["bridge.onca_net_lease_investment"] == -0.974
    assert c["claims"]["bridge.bs_income_tax_payable"] + c["claims"]["bridge.oca_income_tax_prepaid"] == \
        pytest.approx(14.073 - 4.091, abs=1e-12)
    coh = c["terminal"]["cohorts"]
    assert len(coh["t_S"]) == 2 * P["terminal"]["da_cohort_years"]
    assert all(0.0 <= x <= P["tax"]["general"] for x in coh["t_S"])


def test_book13_gates(model, pt):
    """Гейты книги 1.3 своего счёта: налог 2026–27 в 20,2–28,2 %; отдача прироста по τ_g проходит; капитал < 0 — только
    в клетках с весом 0 (H и M × стресс × высокие), гейт не срабатывает и флага не ставит (Г11); поток 9М26 без потоков
    активов моста: фин. вложения +10,053, займы +0,924, курсовая +1,225, разрыв ≈ +16,9 (± 25)."""
    F, P, _ = model
    g = cm.gates(P, F, pt)
    lo, hi = P["checks"]["tax_2026_27"]
    tx = g["tax_rate_2026_27"]
    assert tx["mass"] == 0 and lo <= tx["min"] <= tx["max"] <= hi
    assert g["roic_increment"]["mass"] == 0.0
    assert abs(g["roic_increment"]["value"] - 0.29332930287332054) <= 1e-9      # налог окна по 8b6f1f8 (§4.4)
    # без ключа `joint.expectation` E(2027) — слой «свой взгляд» (книга 1.3 прежняя)
    assert abs(g["broker_ebitda_2027"]["value"] - 392.7911701734833) <= 1e-9
    assert abs(g["ev_ebitda_2027"]["value"] - 3.7224443461981447) <= 1e-12
    ec = g["equity_cushion"]
    assert ec["mass"] == 0.0 and ec["status"] == "проходит" and not ec["cells"]
    assert sorted(ec["cells_zero_prob"]) == ["H|stress|high", "M|stress|high"]
    assert ec["min_equity_weighted"] > 0
    assert g["terminal_share"]["status"] == "проходит"
    fl = g["flow_9m26"]
    assert (fl["fin_9m"], fl["loan_9m"], fl["fx_9m"]) == pytest.approx((10.053, 0.924, 1.225), abs=1e-12)
    assert abs(fl["value"] - 16.93743813036739) <= 1e-9 and fl["mass"] == 0.0
    assert abs(fl["nwc_2026_09_30"] - (-38.10138734799209)) <= 1e-9


@pytest.mark.parametrize("date", ["2026-06-30", "2025-12-31"])
def test_nwc_lines_cash_move_at_shares_date(model, date):
    """Оборотный 30.09 по движению денег (§13.2, книга 1.3): ПОА − X с X = Σ строк x на дате долей и нулевым движением
    — то же, что факт оборотного (проверка прочтения формулы на датах с полным раскрытием)."""
    F, _, _ = model
    assert abs(cm.nwc_from_lines(F, date, date, cash_move=0.0) - cm.val(F.bal[date]["nwc_nonbank"])) <= 1e-9


def test_control_facts_supplement(model):
    """Строки книги 1.3, снятые вторым счётом (`tests/control_facts.json`): суммы 9М26 = проверке решений
    (`out/p3-decisions.md`); если лист фактов несёт строку под тем же именем — значения совпадают."""
    F, _, _ = model
    sup = json.loads(cm.CONTROL_FACTS.read_text(encoding="utf-8"))
    bc = {q: cm.bridge_cash(F, q) for q in cm.Q9M}
    assert sum(b["fin"] for b in bc.values()) == pytest.approx(10.053, abs=1e-12)
    assert sum(b["loan"] for b in bc.values()) == pytest.approx(0.924, abs=1e-12)
    assert sum(b["fx"] for b in bc.values()) == pytest.approx(1.225, abs=1e-12)
    main = cm.Facts(extra=None)
    for date, rows in sup["balance"].items():
        for k, v in rows.items():
            if main.has(date, k):
                assert abs(main.b(date, k) - cm.val(v)) <= 1e-9, (date, k)
    # лист ядра (ba3765d) пишет движение денег со знаком ОДДС и под своими именами; свои — величины без знака
    sheet = main.grp.get("cash_2026", {})
    names = {"fin_assets_invested": ("fin_assets_purchased", -1), "fin_assets_disposed": ("fin_assets_disposed", 1),
             "loans_issued": ("loans_issued", -1), "loans_repaid": ("loans_repaid", 1), "fx_on_cash": ("fx_cash", 1)}
    for k, rows in sup["cash_2026"].items():
        name, sign = names[k]
        if name in sheet:
            for q, v in rows.items():
                assert abs(sign * cm.val(sheet[name][q]) - cm.val(v)) <= 1e-9, (k, q)


def test_limited_liability_never_lowers(model):
    """Цена ограниченной ответственности (Г11) — пол капитала: точка и медиана не ниже базы (40 прогонов)."""
    F, P, _ = model
    ll = cm.limited_liability(P, F, ASOF, TRAP_DRAWS)
    for k in ("point.cell", "point.run", "median.cell", "median.run"):
        assert ll[k] >= -1e-9, (k, ll[k])


def test_gordon_on_seasonal_steady_state_is_continuous(model):
    """Флаг 2 отчёта 402 (диагностика вне книги): с провалом κ строки Гордона на установившемся состоянии того же правила κ
    (FA на 30.06 — (κ_C − δ) × R12, на 31.12 — κ_C × R12) непрерывны с хвостом когорт года H, а нынешние — нет (≈ 2 %)."""
    F, P, _ = model
    Q = copy.deepcopy(P)
    Q["_diagnostic"] = {"gordon_kappa_season": True}

    def gap(X):
        c = cm.cell(X, F, "N", "base", "base", ASOF)
        t, coh = c["terminal"], c["terminal"]["cohorts"]
        steady = t["rows"]["T1"]["DA"] + t["rows"]["T2"]["DA"]
        return abs((coh["DA"][-2] + coh["DA"][-1]) / (1 + t["g"]) ** (len(coh["DA"]) // 2 - 1) / steady - 1), c
    g0, c0 = gap(P)
    g1, c1 = gap(Q)
    assert g0 > 0.01 and g1 < 1e-6
    rows = c1["terminal"]["rows"]
    assert abs(sum(rows[k]["CX"] - rows[k]["DA"] for k in rows) - c1["terminal"]["g"] * c1["kappa"]["level"]
               * c1["kappa"]["r12_end"]) <= 1e-9


# ------------------------------------------------------------------------------------------------ книга 1.4

def test_book14_is_book13_plus_keys():
    """Книга 1.4 = книга 1.3 + ключи «Ключи книги 1.4» (§17); правки без ключа — коридор налога 2026–27 и граница оси 9
    по ключу книги (`bounds_from`). Убрав их, получаем книгу 1.3."""
    J13, J14 = cm.judgments("1.3"), cm.judgments("1.4")
    assert cm.book_version(J14) == "1.4" and cm.book_version(J13) == "1.3"
    for key in cm.BOOK14_KEYS:
        assert cm.dget(J13, key, None) is None and cm.dget(J14, key, None) is not None, key
    axes = {ax["key"]: ax for ax in J14["valuation"]["uncertainty"]["axes"]}
    assert axes["it_tax"]["bounds_from"] == [None, "tax.general"] and axes["it_tax"]["bounds"] == [0.0, 0.25]
    assert J14["checks"]["tax_2026_27"] == [0.208, 0.288]
    back = copy.deepcopy(J14)
    for key in cm.BOOK14_KEYS:
        parts = key.split(".")
        cm.dget(back, ".".join(parts[:-1])).pop(parts[-1])
    for path in ("checks.tax_2026_27", "valuation.uncertainty.axes"):
        cm.dset(back, path, copy.deepcopy(cm.dget(J13, path)))
    back = {k: v for k, v in back.items() if v != {}}
    assert back == J13


def test_book14_own_numbers(model14):
    """Свой счёт книги 1.4 по тексту 3f8e99a + 166eff9 (часть 1 задачи 413): точка 3 204,26 ₽, медиана малой полосы
    3 009,90 ₽; уровни κ 0,3211 / 0,3462 / 0,3713, δ 0,01760, доля IT 0,5044, делитель 398,30 млн — как ориентир ядра."""
    F, P, T = model14
    assert abs(cm.point(P, F, ASOF)["point"] - 3204.261633442663) <= 1e-9 * 3204.0
    assert abs(cm.band(P, F, SMALL_DRAWS, ASOF)["median"] - 3009.9) <= 1e-9 * 3009.0
    for lv, k in (("low", 0.3210524645), ("base", 0.3461950936), ("high", 0.3713377228)):
        assert P["capex"]["kappa_levels"][lv] == k, lv          # §4.9 (3f8e99a): 10 знаков после каждого круга
    assert P["capex"]["h1_dip"] == 0.0176017062
    assert abs(P["capex"]["h1_share"] - 0.33032838416677257) <= 1e-12
    assert abs(T["kappa.D_b"] - 0.1124975876880378) <= 1e-11
    assert abs(P["tax"]["it_share"] - 0.5044203391342155) <= 1e-12
    assert abs(T["kappa.window_share"] - 0.11122475018743166) <= 1e-12


def test_book14_one_composition(model14):
    """§0.5: факты одного состава — A минус эффект, исключения (выручка) и «прочие» F (EBITDA) плюс; группа та же."""
    F, P, _ = model14
    G = cm.one_composition(F, P)
    assert cm.one_composition(G, P) is G and cm.one_composition(F, P) is G
    for p, e in (("2024Q4", 4.3), ("2024H2", 4.3), ("2024", 4.3), ("2025Q1", 8.7), ("2025H1", 8.7), ("2025", 8.7),
                 ("2025Q2", 0.0), ("2026H1", 0.0)):
        assert abs(F.r("A", p) - G.r("A", p) - e) <= 1e-9 and abs(F.e("A", p) - G.e("A", p) - e) <= 1e-9, p
        assert abs(G.r("X", p) - F.r("X", p) - e) <= 1e-9 and abs(G.e("F", p) - F.e("F", p) - e) <= 1e-9, p
        assert abs(G.e("F_o", p) - F.e("F_o", p) - e) <= 1e-9 and G.e("X", p) == F.e("X", p), p
        assert G.r("group", p) == F.r("group", p) and G.e("group", p) == F.e("group", p), p


def test_book14_cell_rules(model14):
    """Правила книги 1.4 на клетке: земля по факту (остаток 78,7 млрд, Σ DA_L = L_0 к 2063H1 сквозь когорты), банк по
    τ_g, делитель и налог нетто-исполнения с признанной частью, Гордон с провалом непрерывен (§4.9, §4.11, §6.3, §7)."""
    F, P, _ = model14
    c = cm.cell(P, F, "N", "base", "base", ASOF)
    kp, d = c["kappa"], "2026-06-30"
    assert abs(kp["anchor_rest"] - 78.67) <= 0.01 and kp["anchor_land"] == F.b(d, "rou_land")
    coh = c["terminal"]["cohorts"]
    no_land = cm.cell(dict(P, capex=dict(P["capex"], anchor_land={"da_half": 0.0})), F, "N", "base", "base", ASOF)
    land_tail = sum(a - b for a, b in zip(coh["DA"], no_land["terminal"]["cohorts"]["DA"]))
    assert land_tail != 0
    eq = cm.val(F.eq[d]["options_exercisable"]) + 0.535 * (cm.val(F.eq[d]["options_outstanding"])
                                                            - cm.val(F.eq[d]["options_exercisable"]))
    assert abs(c["options_divisor"] - eq) <= 1e-12 and abs(c["shares"] - 398.30) <= 0.005
    assert abs(c["claims"]["bridge.net_exercise_tax"] - 0.111 * eq * 3277.5 / 1000) <= 1e-12
    g = c["group"]
    for i, lab in enumerate(c["periods"]):
        tb = cm.tau_bar(P, int(lab[:4]))
        pg = max(0.0, c["blocks"]["G"]["ebitda"][i])
        assert g["pg"][i] == pg
        exp = tb * max(0.0, g["tax_base"][i] - pg) + 0.25 * pg
        assert abs(g["tax"][i] - exp) <= 1e-12 * max(1.0, abs(exp)), lab
    t = c["terminal"]
    steady = t["rows"]["T1"]["DA"] + t["rows"]["T2"]["DA"]
    assert t["gordon_dip"] and abs((coh["DA"][-2] + coh["DA"][-1]) / (1 + t["g"]) ** (len(coh["DA"]) // 2 - 1)
                                   / steady - 1) <= 1e-6


def test_book14_land_runs_out_in_2063(model14):
    """Право пользования землёй (§4.9): DA_L(k) = min(d_L, max(0, L_0 − d_L × (k − 1))) — 73 полных полугодия и остаток
    в 74-м (2063H1); сумма — L_0."""
    F, P, _ = model14
    l0, dl = F.b("2026-06-30", "rou_land"), P["capex"]["anchor_land"]["da_half"]
    da = [min(dl, max(0.0, l0 - dl * (k - 1))) for k in range(1, 200)]
    last = max(k for k, x in enumerate(da, start=1) if x > 0)
    assert last == 74 and cm.label(*cm.shift_half(2026, 2, last - 1)) == "2063H1"
    assert abs(sum(da) - l0) <= 1e-12


def test_book14_gates(model14):
    """Гейты книги 1.4 своего счёта: налог 2026–27 в 20,8–28,8 %; отдача прироста (капитал одного сезона, веса
    заголовка) проходит; капитал < 0 — только в клетке с весом 0 (M × стресс × высокие); поток 9М26 без движения
    требований моста: NP 14,12 → 11,04, аренда 1,033, удержанный налог 2,376; разрыв ≈ +12,7 (± 25)."""
    F, P, _ = model14
    g = cm.gates(P, F, cm.point(P, F, ASOF))
    lo, hi = P["checks"]["tax_2026_27"]
    tx = g["tax_rate_2026_27"]
    assert tx["mass"] == 0 and lo <= tx["min"] <= tx["max"] <= hi
    assert g["roic_increment"]["mass"] == 0.0
    assert abs(g["roic_increment"]["value"] - 0.37066076900784406) <= 1e-9
    ec = g["equity_cushion"]
    assert ec["mass"] == 0.0 and ec["status"] == "проходит" and ec["cells_zero_prob"] == ["M|stress|high"]
    fl = g["flow_9m26"]
    assert (fl["lease_9m"], fl["withheld_tax"]) == pytest.approx((1.033, 2.376), abs=1e-12)
    assert abs(fl["np_2025_12_31"] - 14.12) <= 1e-9
    assert abs(fl["value"] - 12.745006706268812) <= 1e-9 and fl["mass"] == 0.0
    # поправка методики 4083f9d (§8, §13.2): EBITDA 2027 г. гейта брокеров и знаменатель EV/EBITDA — на весах заголовка
    assert abs(g["broker_ebitda_2027"]["value"] - 394.35170475544726) <= 1e-9 and g["broker_ebitda_2027"]["mass"] == 0
    assert abs(g["ev_ebitda_2027"]["value"] - 3.890508570154271) <= 1e-12


def test_book14_bounds_from_follows_book_rate(model14):
    """Граница оси 9 (§9, книга 1.4): верхняя — `tax.general` книги прогона; при общей ставке 30 % край IT — 30 %."""
    _, P, _ = model14
    ax = next(a for a in P["valuation"]["uncertainty"]["axes"] if a["key"] == "it_tax")
    assert cm.axis_bounds(P, ax) == [0.0, 0.25]
    Q = copy.deepcopy(P)
    cm.WORSENINGS["tax+5pp"](Q)
    assert cm.axis_bounds(Q, ax) == [0.0, pytest.approx(0.30)]
    assert cm.axis_value(Q, ax, 0.999) == pytest.approx(0.30)
