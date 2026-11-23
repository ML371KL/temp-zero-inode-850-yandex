"""Проверки выпуска на ядре сервисов (docs/MODEL.md §13; устав, раздел 5).

* **Инвариант** (§13.1) — арифметика; нарушение — поломка, сборка не выходит.
* **Гейт** (§13.2) — правдоподобие; масса — суммарная вероятность клеток слоя «свой взгляд», где условие нарушено
  (гейт слоя или точки — масса 1). Сработавший гейт требует объяснения в `data/assumptions/gate_explanations.yaml`
  со сроком `valid_until` и коридором ожидаемой массы `expected_mass`; нет объяснения, срок истёк или масса вне
  коридора — сборка падает. Гейт, который нечем посчитать (нет коридора в книге или факта), — статус
  `not_computed` с причиной; сборку он не роняет.
* **Ловушка** (§13.3) — механика по именам устава: выплаты акциями один раз, банк не в долге, одна форма аренды,
  одна база долей, «ухудшение не повышает цену». Провал — сборка не выходит.
* **Порядок величин** (§13.4) и **развилки с ценой** (§13.5) — печать, не гейт.
* **Флаг** (§13.6) — совещательная плашка; публикацию не блокирует.

Коридоры и пороги — только из книги (`checks`, `forks`); числа методики (сдвиги «ухудшения» §13.3) — в процентных
пунктах ниже. Годы — от `meta.first_period` и `meta.last_period` книги.

`python -m model.checks [--release ФАЙЛ]` — таблица проверок готового выпуска (по умолчанию `var/release/latest.json`).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

import yaml

from model.book import BOOK_DIR, ROOT, override, period_of, today as _today
from model.book_schema import ALL_BLOCKS, BLOCKS, CAPEX_LEVELS, REGIMES, WORLDS, opt
from model.journal import half_label

fsum = math.fsum

GATE_EXPLANATIONS = BOOK_DIR / "gate_explanations.yaml"
DEFAULT_RELEASE = ROOT / "var" / "release" / "latest.json"
PROB_TOL = 1e-12                # §13.1: вероятности и среднее режимов
IDENTITY_TOL = 1e-9             # тождества строк: относительный допуск с полом на малых величинах
PAYLOAD_MAX_BYTES = 500_000     # потолок выпуска, байт компактного JSON (§13.1)
BP = 10_000                     # базисных пунктов в единице ставки
PP = 100                        # процентных пунктов в единице доли
WORSEN_TOL = 1e-6               # «ухудшение не повышает»: допуск сравнения цен, ₽
# §13.3 «ухудшение не повышает цену»: сдвиги в процентных пунктах.
WORSEN_PP = {"curve": 2, "margin": -1, "capex": 1, "sbc": 0.5, "tax": 5}
WORSEN_KAPPA = 0.01             # «ухудшение» по правилу κ: κ всех уровней +0,01 вместо «капвложения +1 п.п.» (§13.3)
SHOW = 6                        # сколько нарушений печатать в детали гейта


def round_to_step(value: float, step: float) -> float:
    """Печать: округление к шагу, половина — вверх."""
    return math.floor(value / step + 0.5) * step


def printed_ok(value: float, printed: float, step: float) -> bool:
    """Инвариант печати: напечатанное = округлению точного к шагу."""
    return printed == round_to_step(value, step)


def payload_size_ok(n_bytes: int) -> bool:
    return n_bytes <= PAYLOAD_MAX_BYTES


def _years(A: dict) -> tuple[int, int]:
    """(год первого прогнозного полугодия, год последнего)."""
    return int(A["meta"]["first_period"][:4]), int(A["meta"]["last_period"][:4])


def _at(res, years: Iterable[int]) -> list[int]:
    ys = set(years)
    return [i for i, p in enumerate(res.periods) if int(p[:4]) in ys]


def _fmt(x: float | None, nd: int = 1) -> str:
    if x is None:
        return "—"
    return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def _pct(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else _fmt(x * PP, nd) + " %"


# ------------------------------------------------------------ инварианты


def _inv(name: str, title: str, ok: bool, detail: str) -> dict:
    return {"name": name, "title": title, "ok": bool(ok), "detail": detail}


def invariants(grid) -> list[dict]:
    """§13.1: тождества сетки (`model.grid.invariants`), среднее режимов = правилу центра, факты окна."""
    from model.grid import invariants as grid_invariants
    from model.rederive import DECIMALS

    A, cf = grid.ctx.A, grid.ctx.cf
    out = list(grid_invariants(grid))
    # Допуск среднего режимов: §13.1 называет 1e-12, но перевывод пишет цели и центр с DECIMALS знаками — среднее
    # четырёх округлённых целей отходит от округлённого центра до 10^−DECIMALS. Допуск — шаг округления книги.
    center_tol = max(PROB_TOL, 10.0 ** -DECIMALS)
    pr = {r: float(A["joint"]["regime_prob"][r]) for r in REGIMES}
    worst, where = 0.0, "—"
    for b in BLOCKS:
        for name, node, center in (("margin.targets", A["margin"]["targets"][b], float(A["margin"]["center"][b])),
                                   ("growth.shift", A["growth"]["shift"][b], 0.0)):
            dev = abs(fsum(pr[r] * float(node[r]) for r in REGIMES) - center)
            if dev > worst:
                worst, where = dev, f"{name}.{b}"
    out.append(_inv("regime_center", "Среднее режимов каждого режимного суждения = правилу центра",
                    worst <= center_tol, f"наибольшее отклонение {worst:.3g} ({where}); допуск {center_tol:g} "
                                         f"(шаг округления перевывода)"))
    anchor, tol = cf.anchor, float(A["checks"]["facts_identity_tol"])
    parts = []
    ok = True
    for label, src in (("выручка", cf.rev), ("EBITDA", cf.ebitda)):
        gap = fsum(float(src[b][anchor]) for b in (*ALL_BLOCKS, "X")) - float(src["group"][anchor])
        ok = ok and abs(gap) <= tol
        parts.append(f"{label} {gap:+.4f}")
    out.append(_inv("facts_window", f"Факты окна: A–G + исключения = группа {half_label(anchor)} до {_fmt(tol)} млрд",
                    ok, "; ".join(parts) + " млрд"))
    return out


# ----------------------------------------------------------------- гейты

GATES = ("ebitda_2026", "tax_rate_2026_27", "broker_ebitda_2027", "roic_increment", "terminal_reinvest",
         "ev_ebitda_2027", "terminal_share", "terminal_share_point", "margin_range", "capex_range", "real_rate",
         "equity_cushion", "flow_9m26")
GATE_TITLES = {
    "ebitda_2026": "EBITDA группы первого прогнозного года в коридоре устава",
    "tax_rate_2026_27": "Налог первых двух прогнозных лет (за вычетом щита) к EBIT − I",
    "broker_ebitda_2027": "EBITDA второго года (слой «свой взгляд») против брокеров",
    "roic_increment": "Отдача прироста капитала против отдачи окна (база без оборотного капитала)",
    "terminal_reinvest": "Вложения терминала: капвложения − амортизация ≥ g × (ОС + НМА)",
    "ev_ebitda_2027": "EV/EBITDA второго года точки",
    "terminal_share": "Доля PV терминала в EV клетки",
    "terminal_share_point": "Доля PV терминала точки",
    "margin_range": "Маржа группы полугодия",
    "capex_range": "Капвложения к выручке года",
    "real_rate": "Реальная ставка терминала мира",
    "equity_cushion": "Капитал клетки > 0 и запас V0/D слоя",
    "flow_9m26": "Поток без банка 9 мес. против изменения чистого долга без банка",
}
STATUSES = ("ok", "explained", "unexplained", "expired", "mass_outside", "not_computed", "flag")
# гейты клеток, у которых с ключом `checks.cell_mass: point` масса — по точке (§13.2, книга 1.2)
CELL_MASS_GATES = ("equity_cushion", "terminal_share")


class GateExplanationError(ValueError):
    """Файл объяснений гейтов не читается однозначно."""


@dataclass(frozen=True)
class GateCalc:
    """Гейт до объяснений: что нарушено, значение, коридор."""

    name: str
    computed: bool
    hits: tuple = ()            # ((клетка или «слой …» / «точка», что нарушено), …)
    layer: bool = False         # гейт слоя или точки: масса 1
    value: float | None = None  # значение слоя «свой взгляд» или точки
    low: float | None = None    # наименьшее по клеткам
    high: float | None = None   # наибольшее по клеткам
    corridor: tuple | None = None
    unit: str = ""
    reason: str = ""            # почему не считается / как считается
    title: str | None = None    # подпись гейта, если она зависит от ключа книги (книга 1.4); None — GATE_TITLES


def _cells(grid):
    return [(c.key, c.p["analytical"], c.result) for c in grid.cells]


def headline_ebitda_2027(grid) -> float:
    """§8, §13.2 (книга 1.4, `joint.expectation: headline`): E_H(2027) = λ × E_свой + (1 − λ) × E_нейтр."""
    lam = grid.point.lam
    return lam * grid.layers["analytical"].ebitda_2027 + (1.0 - lam) * grid.layers["macro_neutral"].ebitda_2027


def _cell_weights(grid) -> dict:
    """Веса клеток ожидания в гейтах (§13.2): слой «свой взгляд»; книга 1.4 (ключ `joint.expectation: headline`) —
    веса заголовка λ × P_analytical + (1 − λ) × P_macro_neutral."""
    if opt(grid.ctx.A, "joint", "expectation") == "headline":
        lam = grid.point.lam
        return {c.key: lam * c.p["analytical"] + (1.0 - lam) * c.p["macro_neutral"] for c in grid.cells}
    return {c.key: c.p["analytical"] for c in grid.cells}


def _expected(grid, values: dict) -> float:
    w = _cell_weights(grid)
    return fsum(w[c.key] * values[c.key] for c in grid.cells)


def _cell_gate(grid, name: str, values: dict, lo: float, hi: float, unit: str, show, reason: str = "") -> GateCalc:
    hits = tuple((k, show(v)) for k, v in values.items() if not lo <= v <= hi)
    vs = list(values.values())
    return GateCalc(name, True, hits, False, _expected(grid, values), min(vs), max(vs), (lo, hi), unit, reason)


def kappa_rule(A: dict) -> bool:
    """Ключ книги `capex.rule: kappa` — правило капиталоёмкости (§4.9)."""
    return A["capex"].get("rule") == "kappa"


def _no_corridor(name: str, key: str, value=None, low=None, high=None, unit="") -> GateCalc:
    return GateCalc(name, False, value=value, low=low, high=high, unit=unit,
                    reason=f"коридора `checks.{key}` в книге нет — гейт не считается (§13.2 называет его в книге)")


def _general_cell(A: dict, res, i: int) -> float:
    """§4.11, §13.2 (книга 1.4, ключ `tax.general_blocks`): pg(p) клетки — часть базы блоков общей ставки на её строках
    полугодия i (доля DA — как в ядре, F одним блоком)."""
    from model.core import _general_part

    bl = res.blocks
    Eb = {b: bl[b]["ebitda"][i] for b in bl if b not in ("F", "X")}
    Rb = {b: bl[b]["revenue"][i] for b in Eb}
    e, sh = _general_part(A, Eb, Rb, bl["F"]["ebitda"][i], bl["F"]["revenue"][i])
    return e - res.group["da"][i] * sh


def gate_calcs(grid) -> list[GateCalc]:
    """Гейты §13.2 на сетке: значения, коридоры, нарушения."""
    A, cf = grid.ctx.A, grid.ctx.cf
    K = A["checks"]
    y0, yl = _years(A)
    out = []

    # EBITDA первого прогнозного года: факт закрытых полугодий года + прогноз (устав: 2026 г. — 300–350 млрд).
    e26 = {}
    for key, _p, res in _cells(grid):
        fact = fsum(float(cf.ebitda["group"][p]) for p in (f"{y0}H1", f"{y0}H2") if p not in res.periods)
        e26[key] = fact + fsum(res.group["ebitda"][i] for i in _at(res, (y0,)))
    lo, hi = (float(x) for x in K["ebitda_2026"])
    out.append(_cell_gate(grid, "ebitda_2026", e26, lo, hi, "млрд ₽", lambda v: f"{_fmt(v)} млрд"))

    # Налог: Σ (tax_u − S) / Σ (EBIT − I) за полугодия первых двух прогнозных лет.
    tr = {}
    for key, _p, res in _cells(grid):
        g, ii = res.group, _at(res, (y0, y0 + 1))
        tr[key] = (fsum(g["tax"][i] - g["shield"][i] for i in ii) / fsum(g["ebit"][i] - g["interest"][i] for i in ii))
    lo, hi = (float(x) for x in K["tax_2026_27"])
    out.append(_cell_gate(grid, "tax_rate_2026_27", tr, lo, hi, "доля", _pct))

    # EBITDA второго года слоя «свой взгляд» против брокеров (масса 1).
    an = grid.layers["analytical"]
    ref, tol = float(K["ebitda_2027_brokers"]), float(K["ebitda_2027_tol"])
    e27, who, title = an.ebitda_2027, f"слой {an.name}", None
    if opt(A, "joint", "expectation") == "headline":    # книга 1.4 (§8, §13.2): ожидание с весами заголовка
        e27, who = headline_ebitda_2027(grid), "веса заголовка"
        title = "EBITDA второго года (веса заголовка) против брокеров"
    dev = e27 / ref - 1.0
    hits = ((who, f"{_fmt(e27)} млрд, отклонение {_pct(dev)}"),) if abs(dev) > tol else ()
    out.append(GateCalc("broker_ebitda_2027", True, hits, True, e27, None, None,
                        (ref * (1.0 - tol), ref * (1.0 + tol)), "млрд ₽",
                        f"брокеры {_fmt(ref)} млрд ± {_pct(tol, 0)}; отклонение {_pct(dev)}"
                        + ("; ожидание с весами заголовка" if title else ""), title))

    # Отдача прироста: (NOPAT последнего года − NOPAT 12 мес.) / (FA конца − FA якоря); NOPAT 12 мес. = ROIC × FA якоря
    # (тот же вывод, §17); база — ОС + НМА + право пользования без помещений, без оборотного капитала (раздел 4).
    roic, rtol = float(A["terminal"]["roic"]), float(K["roic_tol"])
    nopat12, fa0 = roic * cf.fa_anchor, cf.fa_anchor
    g4 = opt(A, "checks", "roic_tax") == "general"     # книга 1.3 (§13.2, решение Г4): обе стороны по τ_g
    if g4:
        from model.core import _tau
        tg = float(A["tax"]["general"])
    ri, nops, fas = {}, {}, {}
    gen = g4 and opt(A, "tax", "general_blocks")       # книга 1.4 (§13.2): часть блоков общей ставки — уже по τ_g
    season1 = bool(opt(A, "checks", "roic_one_season"))  # книга 1.4 (§13.2): IC конца на 30.06 последнего года
    for key, _p, res in _cells(grid):
        if gen:
            def pg(i, res=res):
                return _general_cell(A, res, i)
            nops[key] = fsum(res.group["ebit"][i] - ((res.group["tax"][i] - tg * pg(i)) * tg / _tau(A, yl)
                                                     + tg * pg(i)) for i in _at(res, (yl,)))
        elif g4:
            nops[key] = fsum(res.group["ebit"][i] - res.group["tax"][i] * tg / _tau(A, yl) for i in _at(res, (yl,)))
        else:
            nops[key] = fsum(res.group["nopat"][i] for i in _at(res, (yl,)))
        fas[key] = res.group["fa"][-2] if season1 else res.group["fa"][-1]
        dfa = fas[key] - fa0
        ri[key] = (nops[key] - nopat12) / dfa if dfa else math.inf
    rlo, rhi = roic * (1.0 - rtol), roic * (1.0 + rtol)
    if kappa_rule(A):
        # Правило κ (§13.2): приёмка — по ожиданию слоя «свой взгляд», клетки — советующий флаг (сборку не держат).
        dfa_e = _expected(grid, fas) - fa0
        x = (_expected(grid, nops) - nopat12) / dfa_e if dfa_e else math.inf
        cells_out = [k for k, v in ri.items() if not rlo <= v <= rhi]
        pc = {c.key: c.p["analytical"] for c in grid.cells}
        hits = (("ожидание", _pct(x)),) if not rlo <= x <= rhi else ()
        vs = list(ri.values())
        out.append(GateCalc("roic_increment", True, hits, True, x, min(vs), max(vs), (rlo, rhi), "доля",
                            f"отдача окна {_pct(roic)} ± {_pct(rtol, 0)}"
                            f"{'; обе стороны по общей ставке ' + _pct(tg, 0) if g4 else ''}"
                            f"{'; капитал одного сезона (30.06)' if season1 else ''}"
                            f"{'; ожидание с весами заголовка' if opt(A, 'joint', 'expectation') else ''}; база без "
                            f"оборотного капитала; по ожиданию "
                            f"{_pct(x)}; советующий флаг клеток (не блокирует): вне коридора {len(cells_out)} из "
                            f"{len(ri)}, масса {fsum(pc[k] for k in cells_out):.4f}"))
    else:
        out.append(_cell_gate(grid, "roic_increment", ri, rlo, rhi, "доля", _pct,
                              f"отдача окна {_pct(roic)} ± {_pct(rtol, 0)}; база без оборотного капитала"))

    # Вложения терминала: Σ_h (CX − DA)(T_h) ≥ g × FA(конец прогноза); значение — отношение.
    rv, hits = {}, []
    for key, _p, res in _cells(grid):
        net = fsum(res.terminal["capex"][h] - res.terminal["da"][h] for h in range(len(res.terminal["capex"])))
        need = res.g * res.group["fa"][-1]
        rv[key] = net / need if need else math.inf
        if net < need * (1.0 - IDENTITY_TOL):     # допуск — точность тождества (§13.2): по правилу κ это тождество
            hits.append((key, f"CX − DA {_fmt(net)} < g × FA {_fmt(need)} млрд"))
    vs = list(rv.values())
    out.append(GateCalc("terminal_reinvest", True, tuple(hits), False, _expected(grid, rv), min(vs), max(vs),
                        (1.0, None), "раз", "(CX − DA) терминала / (g × FA конца прогноза), не ниже 1 с допуском 1e-9"))

    # EV/EBITDA второго года точки (масса 1).
    x = grid.point.ev_ebitda_2027_point
    lo, hi = (float(v) for v in K["ev_ebitda_2027"])
    hits = (("точка", "EBITDA ≤ 0" if x is None else f"{_fmt(x, 2)}×"),) if x is None or not lo <= x <= hi else ()
    out.append(GateCalc("ev_ebitda_2027", True, hits, True, x, None, None, (lo, hi), "×"))

    # Доля терминала в EV клетки: коридор клетки `terminal_share_cell` (бессмыслица); без него — `terminal_share`.
    ts = {key: (res.pv_tv + res.pv_tv_shield) / res.ev for key, _p, res in _cells(grid)}
    lo, hi = (float(v) for v in K["terminal_share"])
    if "terminal_share_cell" in K:
        clo, chi = (float(v) for v in K["terminal_share_cell"])
        out.append(_cell_gate(grid, "terminal_share", ts, clo, chi, "доля", _pct, "коридор клетки"))
        # Приёмка — по точке: доля = низ + λ (верх − низ), низ и верх — доли слоёв точки (§8, §13.2).
        lay_lo, lay_hi = grid.layers["macro_neutral"].terminal_share, grid.layers["analytical"].terminal_share
        x = lay_lo + grid.point.lam * (lay_hi - lay_lo)
        hits = (("точка", _pct(x)),) if not lo <= x <= hi else ()
        out.append(GateCalc("terminal_share_point", True, hits, True, x, None, None, (lo, hi), "доля",
                            f"слои: рыночные ставки как есть {_pct(lay_lo)}, свой взгляд {_pct(lay_hi)}"))
    else:
        out.append(_cell_gate(grid, "terminal_share", ts, lo, hi, "доля", _pct))
        out.append(GateCalc("terminal_share_point", False, unit="доля",
                            reason="коридора `checks.terminal_share_cell` в книге нет — доля терминала проверяется "
                                   "по клеткам, по точке не считается"))

    # Маржа группы, вложения, реальная ставка: коридоры — в книге; нет ключа — значения печатаются, гейт не считается.
    mg = {key: [e / r for e, r in zip(res.group["ebitda"], res.group["revenue"])] for key, _p, res in _cells(grid)}
    allm = [v for vs in mg.values() for v in vs]
    if "margin_range" in K:
        lo, hi = (float(v) for v in K["margin_range"])
        hits = tuple((k, f"{_pct(min(v))}…{_pct(max(v))}") for k, v in mg.items() if min(v) < lo or max(v) > hi)
        out.append(GateCalc("margin_range", True, hits, False, None, min(allm), max(allm), (lo, hi), "доля"))
    else:
        out.append(_no_corridor("margin_range", "margin_range", None, min(allm), max(allm), "доля"))
    cxk = {}
    for k, _p, res in _cells(grid):
        g = res.group
        for y in range(y0 + 1, yl + 1):
            ii = _at(res, (y,))
            v = fsum(g["capex"][i] + g["lease_new"][i] for i in ii) / fsum(g["revenue"][i] for i in ii)
            cxk.setdefault(k, []).append((y, v))
    allc = [v for vs in cxk.values() for _y, v in vs]
    if "capex_range" in K:
        lo, hi = (float(v) for v in K["capex_range"])
        bad = {k: [(y, v) for y, v in vs if not lo <= v <= hi] for k, vs in cxk.items()}
        hits = tuple((k, ", ".join(f"{y} {_pct(v)}" for y, v in b)) for k, b in bad.items() if b)
        out.append(GateCalc("capex_range", True, hits, False, None, min(allc), max(allc), (lo, hi), "доля"))
    else:
        out.append(_no_corridor("capex_range", "capex_range", None, min(allc), max(allc), "доля"))
    real = {w: float(A["worlds"][w]["zero_curve"]["LT"]) + grid.ctx.premium - float(A["worlds"][w]["lt"]["inflation"])
            for w in WORLDS}
    if "real_rate" in K:
        lo, hi = (float(v) for v in K["real_rate"])
        hits = tuple((c.key, f"мир {c.result.world}: {_pct(real[c.result.world])}") for c in grid.cells
                     if not lo <= real[c.result.world] <= hi)
        out.append(GateCalc("real_rate", True, hits, False, None, min(real.values()), max(real.values()), (lo, hi),
                            "доля", "масса — клетки мира вне коридора; "
                                    + ", ".join(f"{w} {_pct(x)}" for w, x in real.items())))
    else:
        out.append(_no_corridor("real_rate", "real_rate", None, min(real.values()), max(real.values()), "доля"))

    # Капитал клеток > 0 (масса — клетки); V0/D любого слоя ≥ `checks.min_v0_to_d` (масса 1), если коридор в книге.
    eq = {key: res.ev - res.claims["total"] for key, _p, res in _cells(grid)}
    hits = tuple((k, f"капитал {_fmt(v)} млрд") for k, v in eq.items() if v <= 0)
    if "min_v0_to_d" in K:
        mn = float(K["min_v0_to_d"])
        lay = {nm: L_.v0_to_d for nm, L_ in grid.layers.items()}
        bad = tuple((f"слой {nm}", "D ≤ 0" if x is None else f"V0/D {_fmt(x, 2)}") for nm, x in lay.items()
                    if x is None or x < mn)
        out.append(GateCalc("equity_cushion", True, bad or hits, bool(bad), an.v0_to_d, min(eq.values()),
                            max(eq.values()), (0.0, None), "млрд ₽",
                            f"капитал клетки > 0 (наименьший {_fmt(min(eq.values()))} млрд); V0/D слоёв не ниже "
                            f"{_fmt(mn, 1)}: " + ", ".join(f"{nm} {_fmt(x, 2) if x is not None else 'D ≤ 0'}"
                                                         for nm, x in lay.items())))
    else:
        out.append(GateCalc("equity_cushion", True, hits, False, an.v0_to_d, min(eq.values()), max(eq.values()),
                            (0.0, None), "млрд ₽",
                            f"считается «капитал клетки > 0» (наименьший {_fmt(min(eq.values()))} млрд); V0/D слоя "
                            f"{_fmt(an.v0_to_d, 2) if an.v0_to_d is not None else 'D ≤ 0'} — коридора "
                            "`checks.min_v0_to_d` в книге нет, эта часть не считается"))

    out.append(flow_9m26(A, cf, grid))
    return out


def _quarter_end(q: str) -> str:
    y, n = int(q[:4]), int(q[5:])
    return (dt.date(y + n // 4, n % 4 * 3 + 1, 1) - dt.timedelta(days=1)).isoformat()


def nwc_quarter_estimate(A: dict, cf, date: str, shares_date: str) -> tuple[float, dict]:
    """§13.2 (книга 1.2): оборотный капитал без банка на дату квартала по строкам датабука в составе §4.10; две
    нераскрытые разбивки — доли на `shares_date`: x — строки, исключённые из прочих оборотных активов (авансы по налогу
    на прибыль — кроме `nwc.income_tax_place: nwc`, чистые инвестиции в аренду, финансовые вложения, займы выданные), к
    прочим оборотным активам; y — прочие налоги к обязательствам по налогам (с `income_tax_place: nwc` — 1)."""
    from model.facts import need

    def b(d, k):
        return need(cf.F, f"balance.{d}.{k}")
    inside = opt(A, "nwc", "income_tax_place") == "nwc"
    excl = ["oca_net_lease_investment", "oca_fin_investments", "oca_loans_employees", "oca_loans_third",
            "oca_loans_related"] + ([] if inside else ["oca_income_tax_prepaid"])
    x = fsum(b(shares_date, k) for k in excl) / b(shares_date, "bs_other_c_assets")
    taxes = b(shares_date, "bs_income_tax_payable") + b(shares_date, "bs_other_taxes_payable")
    y = 1.0 if inside else b(shares_date, "bs_other_taxes_payable") / taxes
    plus = fsum(b(date, k) for k in ("bs_inventories", "bs_trade_receivables", "bs_advances_paid", "bs_vat_receivable",
                                     "bs_unsettled_receivables", "bs_content_assets"))
    minus = fsum(b(date, k) for k in ("bs_payables", "bs_contract_liabilities", "bs_provisions"))
    v = plus + b(date, "bs_other_c_assets") * (1.0 - x) - minus - b(date, "bs_taxes_payable") * y
    return v, {"x": x, "y": y}


def _flow_9m26_season(A: dict, cf, grid) -> GateCalc:
    """§13.2, книга 1.2 (ключ `checks.flow_9m26_capex: season`): поток без банка 9 мес. года наблюдения — строки
    §4.12 на фактах 9 мес. с долями книги; вложения 3К — сезонная доля окна вложений 2П ядра; оборотный на конец
    квартала — оценка по строкам датабука и долям якоря (коридор `checks.flow_9m26_nwc_tol`)."""
    from model.core import _tau, nwc_fact
    from model.facts import need
    from model.rederive import Calendar, quarters, ssum, tax_base

    cal = Calendar(A["meta"])
    obs, anchor = cal.obs, cal.anchor
    y = obs[:4]
    q9 = quarters(f"{y}Q1", obs)
    q9p = quarters(f"{int(y) - 1}Q1", cal.obs_prev)
    end, start = _quarter_end(obs), f"{int(y) - 1}-12-31"
    E9, R9 = ssum(cf.ebitda["group"], q9), ssum(cf.rev["group"], q9)
    rule = opt(A, "tax", "base_rule")
    tg = float(A["tax"]["general"])
    if opt(A, "tax", "general_blocks"):          # книга 1.4 (§4.11, §13.2): блоки общей ставки на фактах 9М
        from model.core import _tax_split
        from model.rederive import general_part_fact
        tax9 = _tax_split(A, _tau(A, int(y)), tax_base(cf, q9, rule), (general_part_fact(A, cf, q9), 0.0), 0.0)
    else:
        tax9 = _tau(A, int(y)) * max(0.0, tax_base(cf, q9, rule))
    ebit9 = E9 - ssum(cf.sbc, q9) - ssum(cf.da, q9)
    tS = tax9 / ebit9 if ebit9 > 0 else 0.0
    if opt(A, "financing", "shield_cap"):
        tS = min(tS, tg)
    sig3 = (fsum(cf.capex[f"{fy}Q3"] for fy in cal.full_years)
            / fsum(cf.capex[f"{fy}H2"] for fy in cal.full_years))
    s_ = float(A["capex"]["first_share"])
    from model.rederive import derive_world_weights
    pw = derive_world_weights(A)                 # книга 1.4 (`joint.expectation: headline`) — веса заголовка
    rw = {c.result.world: c.result.group["revenue"][0] for c in grid.cells
          if c.result.regime == "base" and c.result.capex == "base"}
    cx_h2 = fsum(pw[w] * (s_ * (cf.rev["group"][anchor] + rw[w]) - cf.capex[anchor]) for w in WORLDS)
    capex9 = cf.capex[anchor] + sig3 * cx_h2
    ell, a = float(A["capex"]["lease_share"]), float(A["ma"]["share"])
    kb = float(A["bank"]["capital_to_revenue"])
    dkb = kb * (ssum(cf.rev["G"], q9) - ssum(cf.rev["G"], q9p))
    nwc_end, sh = nwc_quarter_estimate(A, cf, end, cf.facts_date)
    nwc_start = nwc_fact(A, cf, start)
    tol_n = float(A["checks"]["flow_9m26_nwc_tol"])
    f_wo_nwc = E9 - float(A["sbc"]["share"]) * R9 - tax9 - capex9 - (ell + a) * R9 - dkb
    cash = lambda k: fsum(need(cf.F, f"group.cash_{y}.{k}.{q}") for q in q9)       # noqa: E731
    i_net = ssum(cf.interest_expense, q9) - ssum(cf.office_lease_interest, q9) - ssum(cf.interest_income, q9)
    f_fact = (-(cf.nd_nonbank[end] - cf.nd_nonbank[start]) + cash("dividends_paid") + i_net * (1.0 - tS)
              - cash("disposals"))
    brg = ""
    if opt(A, "checks", "flow_9m26_bridge") == "cash":     # книга 1.3 (§13.2, решение Г9)
        def cq(k, q):
            return need(cf.F, f"group.cash_{y}.{k}.{q}")
        fin = {q: cq("fin_assets_disposed", q) + cq("fin_assets_purchased", q) for q in q9}
        loan = {q: cq("loans_repaid", q) + cq("loans_issued", q) for q in q9}
        fx = fsum(cq("fx_cash", q) for q in q9)
        f_fact = f_fact - fsum(fin.values()) - fsum(loan.values()) - fx
        inside = opt(A, "nwc", "income_tax_place") == "nwc"
        excl = ["oca_net_lease_investment", "oca_fin_investments", "oca_loans_employees", "oca_loans_third",
                "oca_loans_related"] + ([] if inside else ["oca_income_tax_prepaid"])
        ex_end = (fsum(need(cf.F, f"balance.{cf.facts_date}.{k}") for k in excl) - fin[obs] - loan[obs])
        oca = need(cf.F, f"balance.{end}.bs_other_c_assets")
        nwc_end = nwc_end + oca * sh["x"] - ex_end
        sh = {**sh, "x": ex_end / oca}
        brg = (f"; без потоков активов моста: фин. вложения {_fmt(fsum(fin.values()))}, займы "
               f"{_fmt(fsum(loan.values()))}, курсовая разница {_fmt(fx)}; исключённые из ПОА на {end} — по движению "
               f"денег {_fmt(ex_end)}")
        more = opt(A, "checks", "flow_9m26_claims")
        if more is not None:                     # книга 1.4 (§13.2): без движения требований моста
            fd = cf.facts_date
            np_start = (need(cf.F, f"balance.{start}.bs_income_tax_payable")
                        - need(cf.F, f"balance.{start}.oca_income_tax_prepaid"))
            np_end = (need(cf.F, f"balance.{end}.bs_taxes_payable") * (1.0 - sh["y"])
                      - need(cf.F, f"balance.{fd}.oca_income_tax_prepaid"))
            lease = fsum(need(cf.F, f"balance.{d}.{k}") * sg for d, sg in ((start, 1.0), (fd, -1.0))
                         for k in ("oca_net_lease_investment", "onca_net_lease_investment"))
            wt = float(more["withheld_tax"])
            f_fact = f_fact + (np_start - np_end) - lease + wt
            brg += (f"; без движения требований моста: налог к уплате {_fmt(np_start, 2)} → {_fmt(np_end, 2)}, "
                    f"поступления по инвестициям в аренду {_fmt(lease, 2)} (1П26), удержанный налог "
                    f"нетто-исполнения {_fmt(wt, 2)} (1П26)")
    gap = {d: f_wo_nwc - (nwc_end + d - nwc_start) - f_fact for d in (-tol_n, 0.0, tol_n)}
    tol = float(A["checks"]["flow_9m26_tol"])
    hits = (("книга", f"F_мод − F_факт {_fmt(gap[0.0])} млрд вне ± {_fmt(tol, 0)}"),) if abs(gap[0.0]) > tol else ()
    reason = (f"F_мод {_fmt(f_wo_nwc - (nwc_end - nwc_start))} (EBITDA {_fmt(E9)}, налог {_fmt(tax9)}, вложения 9М "
              f"{_fmt(capex9)} = 1П факт {_fmt(cf.capex[anchor])} + {_fmt(sig3, 3)} × 2П ядра {_fmt(cx_h2)}, ΔNWC "
              f"{_fmt(nwc_end - nwc_start)}: оборотный {end} {_fmt(nwc_end)} (доли {cf.facts_date}: x "
              f"{_fmt(sh['x'], 3)}, y {_fmt(sh['y'], 3)}) − {start} {_fmt(nwc_start)}); F_факт {_fmt(f_fact)} (ΔND "
              f"{_fmt(cf.nd_nonbank[end] - cf.nd_nonbank[start])}, дивиденды {_fmt(cash('dividends_paid'))}, проценты "
              f"нетто {_fmt(i_net)} × (1 − {_fmt(tS, 3)}), продажа бизнесов {_fmt(cash('disposals'))}); разница "
              f"{_fmt(gap[0.0])}, при оборотном ± {_fmt(tol_n, 0)}: {_fmt(gap[tol_n])} … {_fmt(gap[-tol_n])}" + brg)
    return GateCalc("flow_9m26", True, hits, True, gap[0.0], gap[tol_n], gap[-tol_n], (-tol, tol), "млрд ₽", reason)


def flow_9m26(A: dict, cf, grid=None) -> GateCalc:
    """§13.2 «Поток 9М26»: ΔNWC_факт = оборотный капитал без банка на конец квартала наблюдения − на начало года.
    Без оборотного капитала на конец квартала гейт не считается (причина — в выпуске). Книга 1.2 (ключ
    `checks.flow_9m26_capex: season`) — расчёт `_flow_9m26_season`."""
    if opt(A, "checks", "flow_9m26_capex") == "season" and grid is not None:
        return _flow_9m26_season(A, cf, grid)
    q = A["meta"]["observation_quarter"]
    end = _quarter_end(q)
    if end not in cf.nwc:
        have = ", ".join(sorted(cf.nwc)) or "нет"
        return GateCalc("flow_9m26", False, unit="млрд ₽",
                        reason=f"оборотного капитала без банка на {end} в фактах нет (есть на {have}): ΔNWC_факт "
                               "не считается — гейт не считается (рабочий файл, раздел 4, ошибки ниже порога)")
    return GateCalc("flow_9m26", False, unit="млрд ₽",
                    reason=f"оборотный капитал на {end} есть, расчёт F_мод и F_факт (§13.2) ещё не подключён")


def _parse_date(raw: Any, where: str) -> dt.date:
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    try:
        return dt.date.fromisoformat(str(raw))
    except ValueError:
        raise GateExplanationError(f"{where}: valid_until = {raw!r} — не дата ГГГГ-ММ-ДД") from None


def load_gate_explanations(path: Path | str | None = None) -> dict:
    """Объяснения гейтов: {гейт: {explanation, expected_mass (lo, hi), valid_until}}.

    Нет файла — пустой словарь (сработавший гейт тогда блокирует). Незнакомый гейт, незнакомый ключ записи,
    неполная запись — `GateExplanationError`.
    """
    path = Path(path) if path is not None else GATE_EXPLANATIONS
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise GateExplanationError(f"{path.name}: ожидается словарь {{гейт: объяснение}}")
    keys = {"explanation", "expected_mass", "valid_until"}
    out = {}
    for key, spec in raw.items():
        where = f"{path.name}: {key}"
        if key not in GATES:
            raise GateExplanationError(f"{where} — незнакомый гейт (известны: {', '.join(GATES)})")
        if not isinstance(spec, dict):
            raise GateExplanationError(f"{where} — ожидается запись {{explanation, expected_mass, valid_until}}")
        unknown, missing = sorted(set(spec) - keys), sorted(keys - set(spec))
        if unknown or missing:
            raise GateExplanationError(f"{where}: " + "; ".join(
                ([f"незнакомый ключ {', '.join(unknown)}"] if unknown else [])
                + ([f"нет ключа {', '.join(missing)}"] if missing else [])))
        text = spec["explanation"]
        if not isinstance(text, str) or not text.strip():
            raise GateExplanationError(f"{where}: explanation — пустой текст")
        mass = spec["expected_mass"]
        if (not isinstance(mass, list) or len(mass) != 2
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in mass)
                or not 0 <= mass[0] <= mass[1] <= 1):
            raise GateExplanationError(f"{where}: expected_mass = {mass!r} — ожидается коридор [нижняя, верхняя] "
                                       "долей массы, 0 ≤ нижняя ≤ верхняя ≤ 1")
        out[key] = {"explanation": text.strip(), "expected_mass": (float(mass[0]), float(mass[1])),
                    "valid_until": _parse_date(spec["valid_until"], where)}
    return out


@dataclass(frozen=True)
class GateResult:
    name: str
    title: str
    fired: bool
    mass: float | None
    value: float | None
    low: float | None
    high: float | None
    corridor: tuple | None
    unit: str
    cells: tuple
    detail: str
    explanation: str = ""
    valid_until: dt.date | None = None
    expected_mass: tuple | None = None
    status: str = "ok"          # ok | explained | unexplained | expired | mass_outside | not_computed
    blocking: bool = False

    def to_json(self) -> dict:
        d = asdict(self)
        d["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        d["corridor"] = list(self.corridor) if self.corridor is not None else None
        d["expected_mass"] = list(self.expected_mass) if self.expected_mass is not None else None
        d["cells"] = list(self.cells)
        return d


def gates(grid, explanations: dict | None = None, today: dt.date | None = None) -> list[GateResult]:
    """Гейты с объяснениями на день `today` (по умолчанию — сегодня, `FAKE_TODAY`)."""
    explanations = load_gate_explanations() if explanations is None else explanations
    today = today or _today()
    pa = {c.key: c.p["analytical"] for c in grid.cells}
    pp = pa
    if opt(grid.ctx.A, "checks", "cell_mass") == "point":      # книга 1.2 (§13.2): масса клеток по точке
        lam = grid.point.lam
        pp = {c.key: lam * c.p["analytical"] + (1.0 - lam) * c.p["macro_neutral"] for c in grid.cells}
    zw = opt(grid.ctx.A, "checks", "zero_weight_cells") == "pass"   # книга 1.3 (§13.2): клетки с весом 0 не срабатывают
    out = []
    for g in gate_calcs(grid):
        spec = explanations.get(g.name)
        p = pp if g.name in CELL_MASS_GATES else pa
        zero = ()
        if zw and g.name in CELL_MASS_GATES and not g.layer:
            zero = tuple(h for h in g.hits if p.get(h[0]) == 0.0)
            g = replace(g, hits=tuple(h for h in g.hits if p.get(h[0]) != 0.0))
        fired = bool(g.hits)
        mass = (1.0 if g.layer else fsum(p[k] for k, _ in g.hits)) if fired else (0.0 if g.computed else None)
        detail = "; ".join(f"{k}: {v}" for k, v in g.hits[:SHOW]) + (f"; и ещё {len(g.hits) - SHOW}"
                                                                      if len(g.hits) > SHOW else "")
        if zero:
            detail = "; ".join(x for x in (detail, "клетки с весом 0 (не срабатывают): "
                                           + "; ".join(f"{k}: {v}" for k, v in zero)) if x)
        detail = "; ".join(x for x in (g.reason, detail) if x)
        status, blocking = ("ok" if g.computed else "not_computed"), False
        if fired:
            if spec is None and not g.layer and mass == 0.0:
                status = "flag"             # сработали только клетки с вероятностью 0: печать, не сборка (§13.2)
            elif spec is None:
                status, blocking = "unexplained", True
            elif spec["valid_until"] < today:
                status, blocking = "expired", True
            elif not spec["expected_mass"][0] <= mass <= spec["expected_mass"][1]:
                status, blocking = "mass_outside", True
            else:
                status = "explained"
        out.append(GateResult(
            name=g.name, title=g.title or GATE_TITLES[g.name], fired=fired, mass=mass, value=g.value, low=g.low,
            high=g.high,
            corridor=g.corridor, unit=g.unit, cells=tuple(k for k, _ in g.hits), detail=detail,
            explanation=spec["explanation"] if spec else "", valid_until=spec["valid_until"] if spec else None,
            expected_mass=spec["expected_mass"] if spec else None, status=status, blocking=blocking))
    return out


# ----------------------------------------------------------------- ловушки


def _trap(name: str, title: str, bad: list[str], ok_text: str) -> dict:
    return {"name": name, "title": title, "ok": not bad, "detail": "; ".join(bad[:SHOW]) or ok_text}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= IDENTITY_TOL * max(1.0, abs(a), abs(b))


GROUP_ROWS = {"revenue", "ebitda", "attack", "sbc", "da", "ebit", "tax", "capex", "lease_new", "acq", "nwc", "dwc",
              "dkb", "fcff", "nopat", "fa", "interest", "tax_rate_cash", "shield",
              "revenue_xfs"}                # книга 1.2: выручка без финсервисов — база долей баланса (§0)
CLAIM_ROWS = {"net_debt_nonbank", "net_debt_company", "bank_money", "rollover", "dividends", "total"}


def trap_sbc_once(grid) -> dict:
    """Выплаты акциями ровно один раз: вычтены в потоке; строк выкупа нет; делитель — без будущих выплат."""
    A, cf, ctx = grid.ctx.A, grid.ctx.cf, grid.ctx
    o_d = cf.options_vested_mln                  # §7.2; книга 1.4 — с признанной частью неисполнимых
    rho = opt(A, "valuation", "options_recognized")
    if rho is not None:
        from model.facts import need
        o_d = o_d + float(rho) * (need(cf.F, f"equity.{cf.facts_date}.options_outstanding") - o_d)
    bad = []
    for c in grid.cells:
        g = c.result.group
        want = GROUP_ROWS if grid.ctx.xfs else GROUP_ROWS - {"revenue_xfs"}    # R° — только с ключом §0
        if set(g) != want:
            bad.append(f"{c.key}: строки потока {sorted(set(g) ^ want)}")
        for i in range(len(c.result.periods)):
            flow = (g["ebitda"][i] - g["sbc"][i] - g["tax"][i] - g["capex"][i] - g["lease_new"][i] - g["acq"][i]
                    - g["dwc"][i] - g["dkb"][i])
            if not (g["sbc"][i] > 0 and _close(flow, g["fcff"][i])):
                bad.append(f"{c.key} {c.result.periods[i]}: SBC {g['sbc'][i]:.3f} не вычтены один раз")
                break
        if not _close(c.result.shares, cf.shares_mln + float(A["valuation"]["net_exercise_k"]) * o_d):
            bad.append(f"{c.key}: делитель {c.result.shares:.3f} ≠ акции + k × заработанные опционы окна")
    if not _close(ctx.n_eff, cf.shares_mln + float(A["valuation"]["net_exercise_k"]) * o_d):
        bad.append("делитель контекста ≠ акции + k × опционы")
    return _trap("sbc_once", "Выплаты акциями ровно один раз (выкуп отдельно не вычитается)", bad,
                 "SBC вычтены в потоке каждой клетки; строк выкупа нет; делитель = акции + k × опционы окна")


def trap_bank_not_in_debt(grid) -> dict:
    """Вклады и деньги банка не в долге: D — чистый долг без банка; оборотный капитал — без банка."""
    from model.core import nwc_fact

    cf = grid.ctx.cf
    nd = cf.nd_nonbank[cf.facts_date]
    nwc_anchor = nwc_fact(grid.ctx.A, cf, cf.facts_date)
    bad = []
    if not _close(nd, cf.nd_company + cf.bank_money):
        bad.append(f"ND без банка {nd:.3f} ≠ ND компании {cf.nd_company:.3f} + деньги банка {cf.bank_money:.3f}")
    for c in grid.cells:
        cl = c.result.claims
        extra = {k for k in cl if k not in CLAIM_ROWS and not k.startswith("bridge.")}
        if extra:
            bad.append(f"{c.key}: лишние требования {sorted(extra)}")
        parts = cl["net_debt_nonbank"] + fsum(v for k, v in cl.items() if k.startswith("bridge.")) \
            + cl["rollover"] + cl["dividends"]
        if cl["net_debt_nonbank"] != nd or not _close(parts, cl["total"]):
            bad.append(f"{c.key}: в требованиях не чистый долг без банка")
        dwc0 = c.result.group["dwc"][0]
        if not _close(c.result.group["nwc"][0] - dwc0, nwc_anchor):  # факт без банка по правилам книги (§4.10)
            bad.append(f"{c.key}: оборотный капитал якоря — не без банка")
    return _trap("bank_not_in_debt", "Вклады и деньги банка не в долге", bad,
                 f"D = ND без банка {_fmt(nd)} млрд (ND компании {_fmt(cf.nd_company)} + деньги банка "
                 f"{_fmt(cf.bank_money)}); оборотный капитал якоря — без банка; средств клиентов в требованиях нет")


def _kappa_cx(A: dict, cf, res, i: int) -> float:
    """CX полугодия i клетки по правилу κ (§4.9) из её же строк: первое прогнозное — s × выручка года − капвложения
    якоря + ℓ × R; дальше max(DA + κ(p) × R12 − FA(p − 1), φ × DA) с линией κ от κ₁ клетки. Линия κ — по тексту §4.9,
    не функцией ядра (§13.3): порча линии ядра ловушку не обходит."""
    from model.core import revenue_base

    C, g = A["capex"], res.group
    anchor = A["meta"]["anchor_period"]
    ra = cf.rev["group"][anchor]
    ro, rx = revenue_base(A, cf, anchor), g.get("revenue_xfs", g["revenue"])     # база κ R° (§0; книга 1.1 — R)
    ell, phi = float(C["lease_share"]), float(C["floor_da"])
    r12 = rx[i] + (rx[i - 1] if i > 0 else ro)
    if i == 0:
        cx = float(C["first_share"]) * (ra + g["revenue"][0]) - cf.capex[anchor] + ell * g["revenue"][0]
    else:
        k1 = g["fa"][0] / (ro + rx[0])
        K = res.periods.index(f"{int(C['levels_to'])}H2")
        kC = float(C["kappa_levels"][res.capex])
        line = k1 + (kC - k1) * min(i, K) / K
        if C.get("season") == "h1_dip" and res.periods[i].endswith("H1"):
            line = line - float(C["h1_dip"])
        cx = g["da"][i] + line * r12 - g["fa"][i - 1]
    return max(cx, phi * g["da"][i])


def trap_one_lease(grid) -> dict:
    """Одна форма аренды: новые договоры — в вложениях (LEASE = ℓ × R; CAPEX + LEASE = CX правила §4.9: по правилу κ
    — во всех полугодиях, по прежнему — (уровень + ℓ) × R до года уровней); других арендных строк нет."""
    A, cf = grid.ctx.A, grid.ctx.cf
    ell, y30 = float(A["capex"]["lease_share"]), int(A["capex"]["levels_to"])
    kap = kappa_rule(A)
    bad = []
    for c in grid.cells:
        g, res = c.result.group, c.result
        rows = [k for k in g if "lease" in k or "rent" in k]
        if rows != ["lease_new"]:
            bad.append(f"{c.key}: арендные строки {rows}")
        k = None if kap else float(A["capex"]["levels"][res.capex]) + ell
        for i, p in enumerate(res.periods):
            if not _close(g["lease_new"][i], ell * g["revenue"][i]):
                bad.append(f"{c.key} {p}: новые договоры ≠ ℓ × R")
                break
            if kap:
                if not _close(g["capex"][i] + g["lease_new"][i], _kappa_cx(A, cf, res, i)):
                    bad.append(f"{c.key} {p}: CX ≠ правилу κ")
                    break
            elif int(p[:4]) <= y30 and not _close(g["capex"][i] + g["lease_new"][i], k * g["revenue"][i]):
                bad.append(f"{c.key} {p}: CX ≠ (уровень + ℓ) × R")
                break
    return _trap("one_lease", "Одна форма аренды", bad,
                 "EBITDA компании (после аренды офисов); новые договоры машин, земли и прочего — в CX; других "
                 "арендных строк нет; обязательство машин и складов — в ND компании (факт)")


def trap_one_base(grid) -> dict:
    """Одна база долей: перевывод на фактах воспроизводит выведенные числа книги (§0, §17)."""
    from model.rederive import DECIMALS, derive

    A, cf = grid.ctx.A, grid.ctx.cf
    V, *_ = derive(A, cf)
    bad = []
    for path, v in V.items():
        node: Any = A
        for part in path:
            node = node[part]
        if not (isinstance(node, (int, float)) and abs(float(node) - v) <= 10.0 ** -DECIMALS):
            bad.append(f"{'.'.join(map(str, path))}: книга {node!r}, перевывод {v!r}")
    return _trap("one_base", "Одна база долей (перевывод на фактах = книга)", bad,
                 f"{len(V)} выведенных чисел книги = перевывод на фактах")


def worsened_books(A: dict) -> dict:
    """Книги «ухудшения» §13.3: кривая всех миров выше, цели маржи ниже у всех блоков, уровни капвложений выше,
    выплаты акциями выше, налог (обе ставки) выше."""
    d = {k: v / PP for k, v in WORSEN_PP.items()}
    worlds = dict(A["worlds"])
    for w in WORLDS:
        W = dict(worlds[w])
        W["zero_curve"] = {k: float(v) + d["curve"] for k, v in W["zero_curve"].items()}
        worlds[w] = W
    capex = ({"kappa": override(A, [f"capex.kappa_levels.{c}" for c in CAPEX_LEVELS], "shift", WORSEN_KAPPA)}
             if kappa_rule(A) else
             {"capex": override(A, [f"capex.levels.{c}" for c in CAPEX_LEVELS], "shift", d["capex"])})
    return {
        "curve": {**A, "worlds": worlds},
        "margin": override(A, [f"margin.targets.{b}.{r}" for b in BLOCKS for r in REGIMES], "shift", d["margin"]),
        **capex,
        "sbc": override(A, ["sbc.share"], "shift", d["sbc"]),
        "tax": override(A, ["tax.general", "tax.it_rate"], "shift", d["tax"]),
    }


WORSEN_TITLES = {"curve": "кривая всех миров +{} п.п.", "margin": "цели маржи всех блоков {} п.п.",
                 "capex": "уровни капвложений +{} п.п.", "sbc": "выплаты акциями +{} п.п.",
                 "tax": "налог (обе ставки) +{} п.п.", "kappa": "κ всех уровней +{}"}


def _worse_median(B: dict, cf, band, n_workers) -> float:
    """Медиана полосы ухудшенной книги B на тех же случайных числах (§13.3, §9): те же положения u гиперкуба, а оси —
    книги B (медиана оси — значение B), поэтому ухудшение, которое лежит на оси полосы, доходит и до прогонов."""
    from model.uncertainty import applied, band_axes

    axes = applied(band_axes(B))
    rows = [tuple(ax.value(band.u[i][ax.index]) for ax in axes) for i in range(band.n)]
    return _band_median(B, cf, band, axes, rows, n_workers)


def trap_worsening(grid, band=None, headline: float | None = None, n_workers: int | None = None) -> dict:
    """«Ухудшение не повышает цену» (устав, раздел 5; §13.3): точка и медиана полосы выпуска (те же случайные числа)
    не растут; клетки, где цена выросла, — советующий флаг в детали (сборку не держат). Нет полосы — только точка."""
    from model.grid import evaluate

    A, cf = grid.ctx.A, grid.ctx.cf
    base = {c.key: c.result.price for c in grid.cells}
    on_band = band is not None and headline is not None
    bad, parts = [], []
    for name, B in worsened_books(A).items():
        g2 = evaluate(B, cf, valuation_date=grid.ctx.v, market_price=grid.market_price)
        up = [c.key for c in g2.cells if c.result.price > base[c.key] + WORSEN_TOL]
        dp = g2.point.central - grid.point.central
        dm = _worse_median(B, cf, band, n_workers) - headline if on_band else None
        label = (WORSEN_TITLES[name].format(_fmt(WORSEN_KAPPA, 2)) if name == "kappa" else
                 WORSEN_TITLES[name].format(_fmt(WORSEN_PP[name], 1 if WORSEN_PP[name] % 1 else 0)))
        part = f"{label}: точка {dp:+.0f} ₽" + (f", медиана {dm:+.0f} ₽" if on_band else "")
        if up:
            part += f" (советующий флаг: выросли {len(up)} клеток — {', '.join(up[:3])})"
        parts.append(part)
        if dp > WORSEN_TOL or (on_band and dm > WORSEN_TOL):
            bad.append(f"{label}: точка {dp:+.2f} ₽" + (f", медиана {dm:+.2f} ₽" if on_band else ""))
    return _trap("worsening", "Ухудшение не повышает цену (точка и медиана)", bad, "; ".join(parts))


def traps(grid, band=None, headline: float | None = None, n_workers: int | None = None) -> list[dict]:
    return [trap_sbc_once(grid), trap_bank_not_in_debt(grid), trap_one_lease(grid), trap_one_base(grid),
            trap_worsening(grid, band, headline, n_workers)]


# ------------------------------------------------------ порядок величин


def magnitudes(A: dict, grid, headline: float | None) -> list[dict]:
    """§13.4: медиана против рынка, целей брокеров и прикидки на бумаге; EV/EBITDA и доля терминала точки."""
    K = A["checks"]
    mp = grid.market_price
    an = grid.layers["analytical"]
    rows = [{"name": "market", "title": "Медиана против рынка", "value": headline, "reference": [mp],
             "detail": f"медиана {_fmt(headline, 0)} ₽, рынок {_fmt(mp, 1)} ₽: "
                       f"{_pct(headline / mp - 1.0) if headline is not None else '—'}"}]
    for key, title in (("brokers_price", "Медиана против целей брокеров"), ("paper_price", "Медиана против прикидки")):
        lo, hi = (float(x) for x in K[key])
        where = ("—" if headline is None else "внутри" if lo <= headline <= hi else
                 f"ниже на {_pct(1.0 - headline / lo)}" if headline < lo else f"выше на {_pct(headline / hi - 1.0)}")
        rows.append({"name": key, "title": title, "value": headline, "reference": [lo, hi],
                     "detail": f"{_fmt(lo, 0)}–{_fmt(hi, 0)} ₽: медиана {where} — сверка, не цель"})
    x = grid.point.ev_ebitda_2027_point
    rows.append({"name": "ev_ebitda_2027", "title": "EV/EBITDA второго года точки", "value": x,
                 "reference": [float(v) for v in K["ev_ebitda_2027"]], "detail": f"{_fmt(x, 2)}×"})
    lay_lo = grid.layers["macro_neutral"].terminal_share
    ts = lay_lo + grid.point.lam * (an.terminal_share - lay_lo)      # доля терминала точки — форма гейта (§8, §13.2)
    rows.append({"name": "terminal_share", "title": "Доля терминала точки", "value": ts,
                 "reference": [float(v) for v in K["terminal_share"]],
                 "detail": f"{_pct(ts)} (слой «свой взгляд» {_pct(an.terminal_share)})"})
    return rows


# ------------------------------------------------------ развилки с ценой


def _band_median(B: dict, cf, band, axes, rows, n_workers) -> float:
    from model.uncertainty import DRAW_DECIMALS, centers, median, run_draws

    pairs = run_draws(B, cf, axes, rows, valuation_date=band.valuation_date, n_workers=n_workers)
    low = [round(p[0], DRAW_DECIMALS) for p in pairs]
    high = [round(p[1], DRAW_DECIMALS) for p in pairs]
    return median(centers(low, high, band.lam))


FORK_REDERIVE_ROUNDS = 10          # §13.5 (книга 1.4): предел кругов перевывода книги развилки
_FORK_CACHE: dict = {}             # (книга развилки, факты) → результат: одна и та же развилка в процессе — один раз


def _fork_book(B: dict, cf, held: tuple) -> tuple[dict, str]:
    """§13.5, ключ `forks.rederive: true` (книга 1.4): книга развилки с перевыводом выведенных чисел до неподвижной
    точки; ключи развилки `held` (пути-префиксы) перевывод не трогает — им остаётся значение варианта. Без ключа — та же
    книга. → (книга, печать: что изменилось). Результат — функция книги и фактов: в процессе считается один раз."""
    if not opt(B, "forks", "rederive"):
        return B, ""
    key = json.dumps([B, held, cf.anchor, cf.F.raw], sort_keys=True, default=str, ensure_ascii=False)
    hit = _FORK_CACHE.get(key)
    if hit is not None:
        return copy.deepcopy(hit[0]), hit[1]
    out = _fork_rederive(B, cf, held)
    _FORK_CACHE[key] = copy.deepcopy(out)
    return out


def _fork_rederive(B: dict, cf, held: tuple) -> tuple[dict, str]:
    from model.rederive import DECIMALS, _current, _with, derive

    def kept(path: tuple) -> bool:
        return any(path[:len(h)] == h for h in held)

    changed: dict = {}
    for _ in range(FORK_REDERIVE_ROUNDS):
        V = {path: v for path, v in derive(B, cf)[0].items() if not kept(path)}
        diff = {path: v for path, v in V.items()
                if not (isinstance(_current(B, path), (int, float)) and abs(_current(B, path) - v) <= 10.0 ** -DECIMALS)}
        if not diff:
            break
        for path, v in diff.items():
            changed.setdefault(path, _current(B, path))
        B = _with(B, diff)
    else:
        raise ValueError(f"развилка: перевывод не сошёлся за {FORK_REDERIVE_ROUNDS} кругов (§13.5)")
    kap = {path: v for path, v in changed.items() if path[:2] == ("capex", "kappa_levels") or path[-1] == "h1_dip"}
    note = "; ".join(f"{'.'.join(map(str, path))} {_fmt(old, 4)} → {_fmt(_current(B, path), 4)}"
                     for path, old in kap.items())
    return B, (f"с перевыводом ({len(changed)} выведенных чисел изменилось" + (f": {note}" if note else "") + ")")


def forks(A: dict, cf, grid, band=None, headline: float | None = None, n_workers: int | None = None) -> list[dict]:
    """§13.5: развилки устава (раздел 3) с ценой. Цена по полосе — сдвиг медианы на тех же случайных числах, что у
    полосы выпуска (полная полоса или малая при `--fast`); по точке — сдвиг точки."""
    from model.uncertainty import at_value

    out = []
    lam = grid.point.lam
    point = grid.point.central

    def row(name, title, basis, variants, status="ok", detail=""):
        out.append({"name": name, "title": title, "basis": basis, "variants": variants, "status": status,
                    "detail": detail})

    # Налог IT после 2030 г.: медиана с осью налога на краях против заголовка.
    ax = next((a for a in band.used if a.key == "it_tax"), None) if band is not None else None
    if ax is not None and headline is not None:
        keep = tuple(a for a in band.used if a.index != ax.index)
        rows_ = band.rows(keep)
        variants = [{"label": f"{_pct(x, 0)}", "value": x,
                     "price": _band_median(at_value(A, ax, x), cf, band, keep, rows_, n_workers) - headline}
                    for x in (ax.low, ax.high)]
        row("it_tax", "Налог IT после 2030 г.", f"медиана полосы ({band.n} прогонов)",
            [{"label": f"{_pct(ax.book, 0)} (книга)", "value": ax.book, "price": 0.0}] + variants)
        # Стресс платформы: P(stress) по уставу, остальные режимы поровну.
        w = float(A["forks"]["stress_weight"])
        probs = {r: (w if r == "stress" else (1.0 - w) / (len(REGIMES) - 1)) for r in REGIMES}
        Bs = override(A, ["joint.regime_prob"], "dict", probs)
        Bs, note = _fork_book(Bs, cf, (("joint", "regime_prob"),))     # книга 1.4 (§13.5): с перевыводом
        row("stress", "Стресс платформы", f"медиана полосы ({band.n} прогонов)",
            [{"label": "равные режимы (книга)", "value": float(A["joint"]["regime_prob"]["stress"]), "price": 0.0},
             {"label": f"стресс {_pct(w, 0)}", "value": w,
              "price": _band_median(Bs, cf, band, band.used, band.rows(), n_workers) - headline}],
            "ok", note)
    else:
        for name, title in (("it_tax", "Налог IT после 2030 г."), ("stress", "Стресс платформы")):
            row(name, title, "медиана полосы", [], "not_computed", "полосы с осью налога IT нет")

    # Маржа электронной коммерции: медиана полосы, когда центр суждения 3 стоит на варианте (безубыточность; торговля
    # Ozon), а полуразмахи прежние — центр, цели режимов C и края сдвигаются на x − центр (§13.5).
    from model.rederive import Calendar, ssum
    c0 = float(A["margin"]["center"]["C"])
    cal = Calendar(A["meta"])
    kC = ssum(cf.gtv_C, cal.ltm) / ssum(cf.rev["C"], cal.ltm)
    ozon = float(A["margin"]["C"]["ozon_trade_gmv"])
    if band is not None and headline is not None:
        variants = [{"label": f"центр {_pct(c0)} (книга)", "value": c0, "price": 0.0}]
        for label, x in (("безубыточность", 0.0), (f"торговля Ozon {_pct(ozon, 2)} GMV", ozon * kC)):
            B = override(A, ["margin.center.C"] + [f"margin.targets.C.{r}" for r in REGIMES], "shift", x - c0)
            B["margin"] = {**B["margin"], "range": {**B["margin"]["range"],
                                                    "C": [float(v) + x - c0 for v in A["margin"]["range"]["C"]]}}
            B, note = _fork_book(B, cf, (("margin", "center", "C"), ("margin", "targets", "C"),
                                         ("margin", "range", "C")))       # книга 1.4 (§13.5): ключи развилки держатся
            variants.append({"label": f"{label}: центр {_pct(x)}", "value": x,
                             "price": _band_median(B, cf, band, band.used, band.rows(), n_workers) - headline})
        row("ecom_margin", "Маржа электронной коммерции", f"медиана полосы ({band.n} прогонов)", variants, "ok",
            f"центр суждения 3 — на варианте, полуразмахи прежние; торговля Ozon × GTV / выручка e-com за 12 мес. "
            f"({_fmt(kC, 3)})" + (f"; {note}" if opt(A, "forks", "rederive") else ""))
    else:
        row("ecom_margin", "Маржа электронной коммерции", "медиана полосы", [], "not_computed", "полосы нет")

    # Деньги банка: D при деньгах банка по ликвидности формы 101 на якорь — сдвиг моста.
    from model.facts import FactsError, need
    try:
        f101 = need(cf.F, f"balance.{cf.facts_date}.f101_liquidity")
    except FactsError as exc:
        row("bank_money", "Деньги банка", "мост", [], "not_computed", f"ликвидности ф.101 на якорь нет: {exc}")
    else:
        dD = f101 - cf.bank_money
        ctx = grid.ctx
        lo_p = ctx.price_of(grid.layers["macro_neutral"].equity - dD)
        hi_p = ctx.price_of(grid.layers["analytical"].equity - dD)
        row("bank_money", "Деньги банка", "мост (точка; медиана сдвигается так же)",
            [{"label": f"МСФО: {_fmt(cf.bank_money)} млрд (книга)", "value": cf.bank_money, "price": 0.0},
             {"label": f"ф.101: {_fmt(f101)} млрд", "value": f101, "price": lo_p + lam * (hi_p - lo_p) - point}],
            "ok", f"D {dD:+.2f} млрд; проценты и щит не пересчитаны (§13.5: цена — сдвиг моста)")

    # Автономные технологии: медиана полосы с осью 6 на её краях (положение пути −1 / +1), остальные оси — как в
    # полосе (§13.5); без ключа книги `f.at.position` или без полосы цена не считается.
    ax6 = next((a for a in band.used if a.key == "at_expense"), None) if band is not None else None
    if "position" in A["f"]["at"] and ax6 is not None and headline is not None:
        x0 = float(A["f"]["at"]["position"])
        keep = tuple(a for a in band.used if a.index != ax6.index)
        rows_ = band.rows(keep)
        row("autonomous", "Автономные технологии", f"медиана полосы ({band.n} прогонов)",
            [{"label": f"положение {_fmt(x0, 0)} (книга)", "value": x0, "price": 0.0}]
            + [{"label": f"положение {x:+.0f} ({word})", "value": x,
                "price": _band_median(at_value(A, ax6, x), cf, band, keep, rows_, n_workers) - headline}
               for x, word in ((-1.0, "низ: вечный расход"), (1.0, "верх: ноль с года f.at.high_zero_from"))],
            "ok", "ось 6 на краю, остальные оси — как в полосе, те же случайные числа")
    else:
        row("autonomous", "Автономные технологии", "медиана полосы", [], "not_computed",
            "нет ключа книги `f.at.position` или полосы с осью 6 — цена не считается")
    return out


# ----------------------------------------------------------------- флаги


@dataclass(frozen=True)
class Flag:
    name: str
    title: str
    raised: bool
    detail: str
    status: str = "ok"          # ok | not_connected


def flag_report_fact(period: str | None, closing_date: dt.date | None, closing_title: str | None,
                     valuation_date: dt.date) -> Flag:
    """`report_fact`: МСФО, закрывающее открытое полугодие книги (`next_report.closing`), по календарю вышло — его
    дата не позже даты оценки, — а книга полугодие ещё не закрыла: факт не внесён."""
    title = "Отчёт вышел, факт не внесён"
    if period is None:
        return Flag("report_fact", title, False, "открытого полугодия нет")
    if closing_date is None:
        return Flag("report_fact", title, False, "закрывающего отчёта в календаре нет (календарь — задача плана 15)",
                     "not_connected")
    if closing_date > valuation_date:
        return Flag("report_fact", title, False, f"отчёт за {half_label(period)} по календарю — "
                                                 f"{closing_date.isoformat()}")
    return Flag("report_fact", title, True, f"{closing_title or 'МСФО'} — {closing_date.isoformat()} по календарю; "
                                            f"в книге {half_label(period)} ещё открыто: внести факт")


def flag_dividend_register(expected_ex_dates: Iterable[dt.date], register: Iterable[dict],
                           facts_date: dt.date, today: dt.date | None = None) -> Flag:
    """`dividend_register`: по календарю прошла ожидаемая отсечка (позже даты фактов), а в реестре нет объявленного
    дивиденда с отсечкой в том же полугодии."""
    today = today or _today()
    halves = {period_of(dt.date.fromisoformat(str(r["record_date"]))) for r in register if r.get("record_date")}
    missed = [d for d in expected_ex_dates if facts_date < d <= today and period_of(d) not in halves]
    return Flag("dividend_register", "Отсечка прошла, дивиденда в реестре нет", bool(missed),
                ", ".join(d.isoformat() for d in sorted(missed)) or "реестр полон")


PRICE_STATUS_WORDS = {"live": "живая цена", "book": "цена книги", "fallback": "запасная цена"}


def flag_price_fallback(status: str, reason: str | None = None) -> Flag:
    """`price_fallback`: живая цена не принята, взята последняя принятая (запасная)."""
    words = PRICE_STATUS_WORDS.get(status)
    detail = reason if status == "fallback" and reason else (
        f"цена рынка: {words}" if words else f"цена рынка: статус «{status}» не известен")
    return Flag("price_fallback", "Цена — запасная", status == "fallback", detail)


def flags(A: dict, *, price_status: str, price_reason: str | None, period: str | None, closing: dict | None,
          valuation_date: dt.date) -> list[Flag]:
    """Флаги §13.6. Чего нет во входах (пороги книги, календарь) — `not_connected` с причиной."""
    closing_date = dt.date.fromisoformat(closing["date"]) if closing else None
    return [
        Flag("book_update", "Книгу пора обновить", False,
             "порогов `checks.book_update` (сдвиг кривой, возраст книги) в книге нет — флаг не считается",
             "not_connected"),
        Flag("dividend_register", "Отсечка прошла, дивиденда в реестре нет", False,
             "ожидаемых отсечек в календаре нет (календарь — задача плана 15)", "not_connected"),
        flag_report_fact(period, closing_date, (closing or {}).get("title"), valuation_date),
        flag_price_fallback(price_status, price_reason),
        Flag("stale_release", "Выпуск устарел", False, "считают хаб и сторож свежести по дате выпуска",
             "not_connected"),
    ]


# ----------------------------------------------------------------- всё вместе


@dataclass
class Report:
    invariants: list
    gates: list
    traps: list
    magnitudes: list
    forks: list
    blocking: list = field(default_factory=list)


def blocking_reasons(invariant_list: Iterable[dict], gate_list: Iterable[GateResult],
                     trap_list: Iterable[dict] = ()) -> list[str]:
    """Причины, по которым сборка не выходит (пусто — выходит)."""
    out = [f"инвариант {i['name']}: {i['detail']}" for i in invariant_list if not i["ok"]]
    reason = {"unexplained": "нет объяснения", "expired": "срок объяснения истёк",
              "mass_outside": "масса вне коридора объяснения"}
    for g in gate_list:
        if g.blocking:
            out.append(f"гейт {g.name} ({g.title}): {reason[g.status]}; масса {g.mass:.4f}; {g.detail}")
    out += [f"ловушка {t['name']}: {t['detail']}" for t in trap_list if not t["ok"]]
    return out


def run(grid, band=None, headline: float | None = None, *, explanations: dict | None = None,
        today: dt.date | None = None, n_workers: int | None = None) -> Report:
    """Все проверки §13.1–§13.5 на сетке (и полосе выпуска, если есть)."""
    A, cf = grid.ctx.A, grid.ctx.cf
    inv = invariants(grid)
    gl = gates(grid, explanations, today)
    tl = traps(grid, band, headline, n_workers)
    rep = Report(invariants=inv, gates=gl, traps=tl, magnitudes=magnitudes(A, grid, headline),
                 forks=forks(A, cf, grid, band, headline, n_workers))
    rep.blocking = blocking_reasons(inv, gl, tl)
    return rep


# ----------------------------------------------------------------- печать


def _range(g: dict) -> str:
    lo, hi, unit = g.get("low"), g.get("high"), g.get("unit")
    f = _pct if unit == "доля" else (lambda x: _fmt(x, 2) if unit in ("×", "раз") else _fmt(x))
    val = "—" if g.get("value") is None else f(g["value"])
    span = "" if lo is None else f"; клетки {f(lo)}…{f(hi)}"
    c = g.get("corridor")
    cor = "—" if not c else f"{'−∞' if c[0] is None else f(c[0])}…{'∞' if c[1] is None else f(c[1])}"
    return f"{val}{span} | {cor}"


def render(checks: dict) -> list[str]:
    """Таблица проверок блока `checks` выпуска."""
    out = ["Инварианты:"]
    out += [f"  {'ок ' if i['ok'] else 'ПРОВАЛ'} {i['name']}: {i['detail']}" for i in checks.get("invariants") or []]
    out.append("Гейты (значение слоя «свой взгляд» или точки; клетки; коридор; масса; статус):")
    for g in checks.get("gates") or []:
        mass = "—" if g.get("mass") is None else f"{g['mass']:.4f}"
        out.append(f"  {g['name']}: {_range(g)} | масса {mass} | {g['status']}"
                   + (" | СРАБОТАЛ" if g.get("fired") else ""))
        if g.get("detail"):
            out.append(f"      {g['detail']}")
    out.append("Ловушки:")
    out += [f"  {'ок ' if t['ok'] else 'ПРОВАЛ'} {t['name']}: {t['detail']}" for t in checks.get("traps") or []]
    out.append("Порядок величин:")
    out += [f"  {m['title']}: {m['detail']}" for m in checks.get("magnitudes") or []]
    out.append("Развилки устава с ценой:")
    for f in checks.get("forks") or []:
        vs = "; ".join(f"{v['label']} {v['price']:+.0f} ₽" for v in f.get("variants") or [])
        out.append(f"  {f['title']} ({f['basis']}): {vs or f['status']}" + (f" — {f['detail']}" if f.get("detail")
                                                                             else ""))
    out.append("Флаги:")
    out += [f"  {f['name']}: {'ПОДНЯТ' if f.get('raised') else 'нет'} ({f.get('status')}) — {f.get('detail')}"
            for f in checks.get("flags") or []]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="model.checks", description="проверки §13 готового выпуска")
    ap.add_argument("--release", default=str(DEFAULT_RELEASE))
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.loads(Path(args.release).read_text(encoding="utf-8"))
    print("\n".join(render(payload.get("checks") or {})))
    bad = [g["name"] for g in payload["checks"].get("gates") or [] if g.get("blocking")]
    bad += [i["name"] for i in payload["checks"].get("invariants") or [] if not i.get("ok")]
    bad += [t["name"] for t in payload["checks"].get("traps") or [] if not t.get("ok")]
    print(f"ИТОГ: {'блокируют ' + ', '.join(bad) if bad else 'блокирующих нет'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
