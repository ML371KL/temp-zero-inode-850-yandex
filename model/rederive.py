"""Перевывод книги: выведенные ключи §17 методики — из фактов и суждений книги по правилам, одной командой.

    python -m common.cli rederive [--dry-run]          (= python -m model.rederive [--dry-run])

Читает книгу как YAML (без проверки схемы: до первого перевывода выведенных чисел в ней нет — `null`), факты
`data/facts/*.json` и пишет выведенные ключи точечно (`ops/tools/adopt_worlds.py::set_values`: комментарии и
порядок книги не трогаются, запись сверяется повторным разбором). `--dry-run` — только таблица, книга не пишется.
Печать: таблица «ключ — значение — из чего», строки «ФЛАГ: …» (сверка амортизации вне коридора) и «ИТОГ: …». Руками выведенные ключи не правятся: следующий перевывод их перепишет.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
from pathlib import Path
from statistics import NormalDist
from typing import Any

import yaml

from model.book import BOOK_YAML, ROOT, path_value
from model.book_schema import ALL_BLOCKS, BLOCKS, REGIMES, SHIFT_BLOCKS, opt
from model.facts import CoreFacts, core_facts, load_facts, need, one_composition

fsum = math.fsum

DECIMALS = 10
DIP_SEARCH_HI = 0.05            # §4.9 (книга 1.3): верх поиска провала κ на 30.06
FIXED_POINT_ROUNDS = 50         # §4.9 (книга 1.3): предел кругов «δ — уровни κ»


# ------------------------------------------------------------------ календарь фактов

def q_index(q: str) -> int:
    return 4 * int(q[:4]) + int(q[5]) - 1


def q_name(i: int) -> str:
    return f"{i // 4}Q{i % 4 + 1}"


def quarters(first: str, last: str) -> list[str]:
    return [q_name(i) for i in range(q_index(first), q_index(last) + 1)]


def h_index(p: str) -> int:
    return 2 * int(p[:4]) + int(p[5]) - 1


def h_name(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


def halves(first: str, last: str) -> list[str]:
    return [h_name(i) for i in range(h_index(first), h_index(last) + 1)]


class Calendar:
    """Периоды фактов книги: якорь, наблюдение, «12 мес.» и окно (§0)."""

    def __init__(self, meta: dict):
        self.anchor = meta["anchor_period"]
        self.obs = meta["observation_quarter"]               # 2026Q3
        self.window = quarters(meta["window_start"], self.obs)
        self.ltm = quarters(q_name(q_index(self.obs) - 3), self.obs)
        self.ltm_prev = [q_name(q_index(q) - 4) for q in self.ltm]
        self.obs_prev = q_name(q_index(self.obs) - 4)        # тот же квартал годом раньше
        self.full_years = [str(y) for y in range(int(meta["window_start"][:4]), int(self.anchor[:4]))]
        a = h_index(self.anchor)
        self.a1, self.a2 = h_name(a - 1), h_name(a - 2)      # 2025H2, 2025H1
        self.ltm_end = q_end(self.obs)

    def h_of_obs_prev_half(self) -> str:
        """Полугодие, в котором лежит квартал наблюдения годом раньше (2025H2 для 2025Q3)."""
        y, q = int(self.obs_prev[:4]), int(self.obs_prev[5])
        return f"{y}H{1 if q <= 2 else 2}"


def q_end(q: str) -> str:
    y, n = int(q[:4]), int(q[5])
    return dt.date(y, 3 * n, 30 if n in (2, 3) else 31).isoformat()


def h_end(p: str) -> str:
    return f"{p[:4]}-06-30" if p[5] == "1" else f"{p[:4]}-12-31"


# ------------------------------------------------------------------ правила

def regimes(center: float, low: float, high: float) -> list[float]:
    """§3.1: середины квартилей двукусочного нормального (§0.4), сдвинутые к среднему = центру."""
    nd = NormalDist()
    z90 = nd.inv_cdf(0.9)
    sl, sr = (center - low) / z90, (high - center) / z90
    xs = []
    for k in range(1, 5):
        u = (2 * k - 1) / 8
        z = nd.inv_cdf(u)
        xs.append(center + (sl if u <= 0.5 else sr) * z)
    delta = center - fsum(xs) / 4
    return [x + delta for x in xs]


def ssum(series: dict, keys) -> float:
    return fsum(series[k] for k in keys)


def months_before(sale: dt.date, months: list[tuple[int, int]]) -> int:
    """Сколько месяцев списка (год, месяц) целиком до месяца продажи."""
    return sum(1 for (y, m) in months if (y, m) < (sale.year, sale.month))


def q_months(qs: list[str]) -> list[tuple[int, int]]:
    out = []
    for q in qs:
        y, n = int(q[:4]), int(q[5])
        out += [(y, 3 * (n - 1) + k) for k in (1, 2, 3)]
    return out


def tax_base(cf: CoreFacts, p: str | list, rule: str | None = None, adj: dict | None = None) -> float:
    """База налога §4.11 на фактах: Σ прибыльных блоков (F — одним блоком) − амортизация + исключения; `rule =
    "f_other"` (ключ `tax.base_rule`, книга 1.2) — прибыльные блоки A–E, G плюс расходы «прочих» F (E_o). `adj` —
    {блок: сумма}, вычитаемая из EBITDA блока за период (книга 1.3, §4.4: взносы IT; `F_o` — в F или в E_o)."""
    keys = p if isinstance(p, list) else [p]
    if adj:
        def e(b):
            return ssum(cf.ebitda[b], keys) - adj.get(b, 0.0) - (adj.get("F_o", 0.0) if b == "F" else 0.0)
    else:
        def e(b):
            return ssum(cf.ebitda[b], keys)
    if rule == "f_other":
        blocks = [e(b) for b in BLOCKS]
        return (fsum(x for x in blocks if x > 0) + e("F_o") - ssum(cf.da, keys)
                + ssum(cf.ebitda["X"], keys))
    blocks = [e(b) for b in ("A", "B", "C", "D", "E", "F", "G")]
    return fsum(x for x in blocks if x > 0) - ssum(cf.da, keys) + ssum(cf.ebitda["X"], keys)


def general_part_fact(A: dict, cf: CoreFacts, p: str | list) -> float:
    """§4.11, ключ `tax.general_blocks` (книга 1.4): часть базы на фактах периода p, облагаемая общей ставкой, —
    Σ_{b ∈ список, E_b > 0} (E_b − DA × R_b / Σ_П R), П — прибыльные блоки периода (F — одним блоком); остаток базы —
    base − pg (как в ядре); `tax.general_blocks_da: none` — без доли DA. Без ключа — 0."""
    gb = opt(A, "tax", "general_blocks")
    if not gb:
        return 0.0
    keys = p if isinstance(p, list) else [p]
    e = {b: ssum(cf.ebitda[b], keys) for b in ("A", "B", "C", "D", "E", "F", "G")}
    r = {b: ssum(cf.rev[b], keys) for b in e}
    prof = [b for b in e if e[b] > 0]
    da = 0.0 if opt(A, "tax", "general_blocks_da") == "none" else ssum(cf.da, keys) / fsum(r[b] for b in prof)
    return fsum(e[b] - da * r[b] for b in gb if b in prof)


def derive_world_weights(A: dict) -> dict:
    """§4.9: веса миров вывода κ, δ и доли I полугодия — слой «свой взгляд» `joint.world_prob`; книга 1.4 (ключ
    `joint.expectation: headline`) — веса заголовка P_H(W) = λ × P(W) + (1 − λ) × [W = W_n]."""
    from model.book_schema import WORLDS

    J = A["joint"]
    if opt(A, "joint", "expectation") == "headline":
        lam = float(J["lambda"])
        return {w: lam * float(J["world_prob"][w]) + (1.0 - lam) * (1.0 if w == J["neutral_world"] else 0.0)
                for w in WORLDS}
    return {w: float(J["world_prob"][w]) for w in WORLDS}


def _wlab(A: dict) -> str:
    return "миры с весами заголовка" if opt(A, "joint", "expectation") == "headline" else "миры слоя «свой взгляд»"


def rev_base(A: dict, cf: CoreFacts, keys: list) -> float:
    """§0: R°(P) фактов за период P (список периодов) — выручка группы × Σ A–F / Σ A–G по суммам P (ключ
    `meta.balance_base: ex_fs`); без ключа — выручка группы за P."""
    r = ssum(cf.rev["group"], keys)
    if opt(A, "meta", "balance_base") != "ex_fs":
        return r
    return r * fsum(ssum(cf.rev[b], keys) for b in ALL_BLOCKS if b != "G") / fsum(ssum(cf.rev[b], keys)
                                                                                  for b in ALL_BLOCKS)


def cx_history(cf: CoreFacts, last: str, back: int) -> dict:
    """Вложения CX полугодий (капвложения + поступления права пользования машин, земли и прочего) с предысторией
    «плоско назад» по каждой части (§4.9): CX(q) = CX(q + 2n), n — наименьшее, при котором q + 2n раскрыто."""
    out = {}
    for i in range(h_index(last) - back, h_index(last) + 1):
        p = h_name(i)
        total = 0.0
        for part in (cf.capex, cf.lease_add):
            j = i
            while h_name(j) not in part:
                j += 2
                if j > h_index(last):
                    raise ValueError(f"нет фактов вложений для предыстории {p}")
            total += part[h_name(j)]
        out[p] = total
    return out


def da_rule(cx: dict, p: str, life: float) -> float:
    n = int(round(2 * life))
    return fsum(cx[h_name(h_index(p) - j)] for j in range(1, n + 1)) / n


MARKET = ROOT / "data" / "market" / "beta_inputs.json"


def _week_end(day: dt.date) -> dt.date:
    """Пятница календарной недели даты (неделя — пн–вс; цена недели — последнее закрытие недели)."""
    return day + dt.timedelta(days=(4 - day.weekday()) % 7 if day.weekday() <= 4 else -(day.weekday() - 4))


def _step(points: dict, day: str) -> float:
    """Значение на последнюю дату ≤ day; до первой даты — значение первой («плоско назад», §0.1 п. 5)."""
    keys = sorted(points)
    past = [k for k in keys if k <= day]
    return points[past[-1] if past else keys[0]]


def beta_rule(A: dict, cf: CoreFacts, market: dict) -> tuple[float, dict]:
    """§17 `valuation.beta_u` — правило семейства: недельная полная доходность акции к MCFTR, окно лет
    `beta_rule.window_years` до даты книги, склейка YNDX → YDEX без недель `beta_rule.skip_week_ends` (у обоих рядов),
    дивиденд — в неделю даты отсечки R (экс-дата при T+1), β_E — МНК; Харрис — Пингл: β_u = β_E × E/V + β_d × D/V,
    D/V — среднее по неделям окна: D — чистый долг без банка, E — акции МКПАО в обращении × цена закрытия недели."""
    rule = A["valuation"]["beta_rule"]
    book_day = dt.date.fromisoformat(A["meta"]["date"])
    end = _week_end(book_day) if book_day.weekday() >= 4 else _week_end(book_day) - dt.timedelta(days=7)
    t0 = end.replace(year=end.year - int(rule["window_years"]))
    start = t0 + dt.timedelta(days=((4 - t0.weekday()) % 7 or 7) - 7)   # конец недели перед первой неделей окна
    lo, hi = (dt.date.fromisoformat(x) for x in rule["skip_week_ends"])

    def weekly(rows):
        out = {}
        for day, price in rows:
            d = dt.date.fromisoformat(day)
            out[_week_end(d)] = price           # строки по возрастанию даты: остаётся последнее закрытие недели
        return out
    ps = weekly([(r[0], r[2]) for r in market["stock"]])
    pm = weekly([(r[0], r[1]) for r in market["index"]])
    divs = [(dt.date.fromisoformat(d["record_date"]), float(d["per_share_rub"])) for d in market["dividends"]]
    shares = {d: need(cf.F, f"equity.{d}.shares_outstanding") for d in cf.F.raw["equity"]
              if re.match(r"^\d{4}-\d{2}-\d{2}$", d) and "shares_outstanding" in cf.F.raw["equity"][d]}
    nd = dict(cf.nd_nonbank)
    rs, rm, dv, weeks = [], [], [], []
    w = start + dt.timedelta(days=7)
    while w <= end:
        prev = w - dt.timedelta(days=7)
        if not lo <= w <= hi:
            if w not in ps or prev not in ps or w not in pm or prev not in pm:
                raise ValueError(f"β: нет цены недели {w} или {prev} вне исключённых недель")
            div = fsum(x for (r, x) in divs if prev < r <= w)
            rs.append((ps[w] + div) / ps[prev] - 1)
            rm.append(pm[w] / pm[prev] - 1)
            e = _step(shares, w.isoformat()) * ps[w] / 1000.0
            d = _step(nd, w.isoformat())
            dv.append(d / (d + e))
            weeks.append(w.isoformat())
        w += dt.timedelta(days=7)
    n = len(rs)
    ms, mm = fsum(rs) / n, fsum(rm) / n
    cov = fsum((a - ms) * (b - mm) for a, b in zip(rs, rm)) / (n - 1)
    var = fsum((b - mm) ** 2 for b in rm) / (n - 1)
    beta_e = cov / var
    resid = [a - ms - beta_e * (b - mm) for a, b in zip(rs, rm)]
    se = math.sqrt(fsum(x * x for x in resid) / (n - 2) / (var * (n - 1)))
    dvm = fsum(dv) / n
    bd = float(rule["debt_beta"])
    beta_u = beta_e * (1 - dvm) + bd * dvm
    return beta_u, {"beta_e": beta_e, "se": se, "n": n, "dv": dvm, "first": weeks[0], "last": weeks[-1],
                    "start_point": start.isoformat(), "dividends": len([1 for r, _ in divs if start < r <= end])}


def _classes(cf: CoreFacts, node: str) -> list[tuple[str, float, float]]:
    """(класс, первоначальная стоимость, середина диапазона срока) амортизируемых классов узла фактов."""
    raw = cf.F.raw["balance"]["asset_classes"][node]
    out = []
    for c, v in raw.items():
        if "life_min" not in v:
            continue                       # незавершённые и предоплаты не амортизируются
        base = f"balance.asset_classes.{node}.{c}"
        mid = 0.5 * (need(cf.F, base + ".life_min") + need(cf.F, base + ".life_max"))
        out.append((f"{node}.{c}", need(cf.F, base + ".gross"), mid))
    return out


def life_from_classes(cf: CoreFacts) -> tuple[float, float, list]:
    """§17 `capex.asset_life_years`: Σ стоимость × срок / Σ стоимость по классам ОС и прочих НМА; шаг полугодия."""
    parts = _classes(cf, "ppe_2026-06-30") + _classes(cf, "intangibles_2025-12-31")
    raw = fsum(g * m for _, g, m in parts) / fsum(g for _, g, _m in parts)
    return round(2 * raw) / 2, raw, parts


def da_check(cf: CoreFacts, cal: Calendar, life: float, rest: bool = False, land: float | None = None) -> dict:
    """Сверка срока (решение архитектора): по ходу ядра — 9 мес. года наблюдения = факт амортизации якоря + DA
    первого прогнозного полугодия по §4.9 (база якоря — факт, выбывает по форме когорт; от клетки не зависит) / 2
    против факта; полные годы окна — справка (правило когорт на фактах вложений с предысторией, `check: False`)."""
    nxt = h_name(h_index(cal.anchor) + 1)
    first = f"{cal.full_years[0]}H1"
    n2 = int(round(2 * life))
    cx = cx_history(cf, cal.anchor, n2 + h_index(cal.anchor) - h_index(first) + 2)
    out = {}
    for y in cal.full_years:
        out[y] = {"model": da_rule(cx, f"{y}H1", life) + da_rule(cx, f"{y}H2", life), "fact": cf.da[y],
                  "check": False}
    ia = h_index(cal.anchor)
    s0 = fsum(cx[h_name(ia - j)] for j in range(1, n2 + 1))
    s1 = fsum(cx[h_name(ia - j)] for j in range(1, n2))
    da_next = cf.da[cal.anchor] * s1 / s0 + cx[cal.anchor] / n2          # DA(первое прогнозное), §4.9, k = 1
    if rest:                                 # книга 1.3 (§4.9, `capex.anchor_rest`): остаток капитала якоря когортой
        sk = [fsum(cx[h_name(ia - j)] for j in range(1, n2 - k + 1)) for k in range(0, n2)]
        rv = cf.fa_anchor - fsum(cf.da[cal.anchor] * sk[k] / sk[0] for k in range(1, n2)) - cx[cal.anchor]
        if land is not None:                 # книга 1.4 (§4.9, `capex.anchor_land`): земля вне остатка, по факту
            l0 = need(cf.F, f"balance.{cf.facts_date}.rou_land")
            rv, da_next = rv - l0, da_next + min(land, l0)
        da_next = da_next + rv / n2
    out["9М" + cal.obs[:4]] = {"model": cf.da[cal.anchor] + da_next / 2, "fact": cf.da[cal.anchor] + cf.da[cal.obs],
                               "check": True, "da_next": da_next, "next": nxt}
    for v in out.values():
        v["dev"] = v["model"] / v["fact"] - 1
    return out


def derive(A: dict, cf: CoreFacts) -> tuple[dict, list, list, dict]:
    """→ (значения {путь-кортеж: число}, таблица [(ключ, значение, из чего)], флаги, справка подгонки)."""
    cal = Calendar(A["meta"])
    cf = one_composition(A, cf)                     # книга 1.4 (§0.5): факты одного состава
    V: dict[tuple, Any] = {}
    T: list[tuple[str, Any, str]] = []
    flags: list[str] = []

    def put(path: tuple, value: float, how: str) -> float:
        V[path] = round(float(value), DECIMALS)
        T.append((".".join(map(str, path)), V[path], how))
        return V[path]

    rev, eb = cf.rev, cf.ebitda
    grp = rev["group"]
    R12 = ssum(grp, cal.ltm)
    cpi = cf.cpi
    alpha = float(A["growth"]["A"]["autoru_share"])
    obs, obs_prev, a1 = cal.obs, cal.obs_prev, cal.a1

    # ---- 2026H2 (§4.2)
    b_rev = opt(A, "path_2026h2", "b_growth") == "revenue"          # книга 1.2 (§4.2): B по выручке
    a_mult = opt(A, "growth", "A", "autoru_form") == "multiply"      # книга 1.2 (§4.2): Авто.ру множителем
    for b in BLOCKS:
        if b == "B" and not b_rev:
            g = cf.gtv_B[obs] / cf.gtv_B[obs_prev] - 1
            how = f"(GTV B {obs}/{obs_prev}) / (1 + ИПЦ {obs}) − 1"
        elif b == "A" and a_mult:
            g = rev[b][obs] / rev[b][obs_prev] - 1
            how = f"(1 + рост R_A {obs} г/г) / (1 − α) / (1 + ИПЦ {obs}) − 1"
        else:
            g = rev[b][obs] / rev[b][obs_prev] - 1
            how = f"(1 + рост R_{b} {obs} г/г{' + α' if b == 'A' else ''}) / (1 + ИПЦ {obs}) − 1"
        if b == "A":
            g = (1 + g) / (1 - alpha) - 1 if a_mult else g + alpha
        put(("path_2026h2", "real_growth", b), (1 + g) / (1 + cpi[obs]) - 1, how)
    for b in BLOCKS:
        m_obs = eb[b][obs] / rev[b][obs]
        m_h = eb[b][a1] / rev[b][a1]
        m_q = eb[b][obs_prev] / rev[b][obs_prev]
        put(("path_2026h2", "margin", b), m_obs + (m_h - m_q), f"m_{b}({obs}) + m_{b}({a1}) − m_{b}({obs_prev})")
    put(("path_2026h2", "f_ebitda"), 2 * eb["F"][obs], f"2 × E_F({obs})")

    # ---- рост A 2027 (§4.3, §17)
    sale = dt.date.fromisoformat(A["growth"]["A"]["autoru_sale_date"])
    mo = months_before(sale, q_months(cal.ltm))
    RA, RA_prev = ssum(rev["A"], cal.ltm), ssum(rev["A"], cal.ltm_prev)
    own = RA * (1 - alpha * mo / 12) / (RA_prev * (1 - alpha)) - 1
    if opt(A, "growth", "A", "cpi_12m") == "monthly":                 # книга 1.2 (§0.3): по месячному ряду
        cpi12 = cpi[f"12m_{cal.obs}"]
    else:
        cpi12 = fsum(cpi[q] for q in cal.ltm) / len(cal.ltm)
    own_real = (1 + own) / (1 + cpi12) - 1
    ad = cf.ad_market
    akar = (1 + ad["akar_2026Q1"]) / (1 + cpi["2026Q1"]) - 1
    arir = (1 + ad["arir_2026H1"]) / (1 + cpi["2026H1"]) - 1
    put(("growth", "A", "y2027_real"), 0.5 * (own_real + 0.5 * (akar + arir)),
        f"½ × (собственный реальный рост A за 12 мес. без Авто.ру ({mo} мес. из 12) {own_real:.4f}; ИПЦ 12 мес. "
        f"{cal.ltm[0]}–{cal.ltm[-1]} {cpi12:.4f} + рекламный: ½ × (АКАР 1К26 {akar:.4f} + "
        f"АРИР 1П26 {arir:.4f}))")

    # ---- режимы роста (§3.1)
    for b in BLOCKS:
        if b in SHIFT_BLOCKS:
            lo, hi = A["growth"]["shift_range"][b]
            vs = regimes(0.0, float(lo), float(hi))
            how = f"§3.1 от центра 0 и краёв {lo}…{hi}"
        else:
            vs, how = [0.0] * 4, "диапазона сдвига нет — 0 во всех режимах (§3.1)"
        for r, v in zip(REGIMES, vs):
            put(("growth", "shift", b, r), v, how)

    # ---- цели маржи (§3.1, §17)
    gtvB12, gtvC12 = ssum(cf.gtv_B, cal.ltm), ssum(cf.gtv_C, cal.ltm)
    RB12, RC12 = ssum(rev["B"], cal.ltm), ssum(rev["C"], cal.ltm)
    kB, kC = gtvB12 / RB12, gtvC12 / RC12
    center = {b: A["margin"]["center"][b] for b in BLOCKS}
    itc = opt(A, "margin", "it_contrib_4q25")         # книга 1.3 (§4.4): взносы IT IV кв. 2025 г. по ставке 2026 г.
    xit = {} if itc is None else {b: float(itc["amount"]) * float(itc[b]) for b in ("A", "B", "F_o")}
    if itc is None:
        center["A"] = put(("margin", "center", "A"), ssum(eb["A"], cal.ltm) / RA, "E_A / R_A за 12 мес.")
        center["B"] = put(("margin", "center", "B"), ssum(eb["B"], cal.ltm) / gtvB12 * kB,
                          f"E_B / GTV_B за 12 мес. × GTV_B / R_B за 12 мес. ({kB:.4f})")
    else:
        center["A"] = put(("margin", "center", "A"), (ssum(eb["A"], cal.ltm) - xit["A"]) / RA,
                          f"(E_A за 12 мес. − {xit['A']:.3f} взносов IT 4К25) / R_A за 12 мес. (§4.4)")
        center["B"] = put(("margin", "center", "B"), (ssum(eb["B"], cal.ltm) - xit["B"]) / gtvB12 * kB,
                          f"(E_B − {xit['B']:.3f} взносов IT 4К25) / GTV_B за 12 мес. × GTV_B / R_B за 12 мес. "
                          f"({kB:.4f}) (§4.4)")
    center["C"] = put(("margin", "center", "C"), 0.5 * float(A["margin"]["C"]["ozon_trade_gmv"]) * kC,
                      f"½ × маржа торговли Ozon × GTV_C / R_C за 12 мес. ({kC:.4f})")
    lo, hi = A["margin"]["B"]["gtv_range"]
    put(("margin", "range", "B", 0), lo * kB, "низ суждения 2 (доля GTV) × GTV_B / R_B за 12 мес.")
    put(("margin", "range", "B", 1), hi * kB, "верх суждения 2 (доля GTV) × GTV_B / R_B за 12 мес.")
    ranges = {b: list(A["margin"]["range"][b]) for b in BLOCKS}
    ranges["B"] = [V[("margin", "range", "B", 0)], V[("margin", "range", "B", 1)]]
    for b in BLOCKS:
        vs = regimes(center[b], float(ranges[b][0]), float(ranges[b][1]))
        for r, v in zip(REGIMES, vs):
            put(("margin", "targets", b, r), v, f"§3.1 от центра {center[b]:.4f} и краёв суждения")
        gaps = [eb[b][f"{y}H1"] / rev[b][f"{y}H1"] - eb[b][f"{y}H2"] / rev[b][f"{y}H2"] for y in cal.full_years]
        put(("margin", "season_gap", b), fsum(gaps) / len(gaps),
            f"среднее m(H1) − m(H2) за {', '.join(cal.full_years)}")

    # ---- F, исключения, банк, выплаты акциями (§4.1, §4.5, §4.6, §4.8)
    put(("f", "revenue_share"), ssum(rev["F"], cal.ltm) / R12, "R_F / R за 12 мес.")
    if itc is None:
        put(("f", "other_share"), ssum(eb["F_o"], cal.ltm) / R12, "E прочих сервисов и инициатив / R за 12 мес.")
    else:
        put(("f", "other_share"), (ssum(eb["F_o"], cal.ltm) - xit["F_o"]) / R12,
            f"(E прочих сервисов и инициатив − {xit['F_o']:.3f} взносов IT 4К25) / R за 12 мес. (§4.4)")
    Sw = fsum(ssum(rev[b], cal.window) for b in ("A", "B", "C", "D", "E", "F", "G"))
    xw = cal.ltm if opt(A, "excl", "revenue_period") == "ltm" else cal.window     # книга 1.2 (§4.1): 12 мес.
    Sx = fsum(ssum(rev[b], xw) for b in ("A", "B", "C", "D", "E", "F", "G"))
    put(("excl", "revenue_share"), -ssum(rev["X"], xw) / Sx,
        f"−Σ R_X / Σ R блоков A–G, кварталы {xw[0]}–{xw[-1]}")
    put(("excl", "ebitda_share"), ssum(eb["X"], cal.window) / Sw,
        f"Σ E_X / Σ R блоков A–G, кварталы {cal.window[0]}–{cal.window[-1]}")
    put(("bank", "capital_to_revenue"), cf.bank_capital[cal.ltm_end] / ssum(rev["G"], cal.ltm),
        f"капитал банка (ф.123) на {cal.ltm_end} / R_G за 12 мес.")
    put(("sbc", "share"), ssum(cf.sbc, cal.ltm) / R12, "выплаты акциями / R за 12 мес.")
    sy = [str(y) for y in opt(A, "sbc", "h1_years", default=cal.full_years)]   # книга 1.2 (§4.8): годы сезона
    put(("sbc", "h1_share"), fsum(cf.sbc[f"{y}H1"] for y in sy) / fsum(cf.sbc[y] for y in sy),
        f"Σ SBC(H1) / Σ SBC(год), {', '.join(sy)}")

    # ---- вложения и амортизация (§4.9)
    put(("capex", "lease_share"), cf.lease_add["ltm"] / (grp[a1] + grp[cal.anchor]),
        f"поступления права пользования машин, земли и прочего {a1} + {cal.anchor} / R тех же 12 мес.")
    life, raw_life, parts = life_from_classes(cf)
    put(("capex", "asset_life_years"), life,
        f"сроки классов МСФО (середина диапазона), взвешенные по первоначальной стоимости: ОС на 30.06.2026 и прочие "
        f"НМА на 31.12.2025, без незавершённых; {raw_life:.3f} лет → ближайшее кратное 0,5")
    tol = float(A["checks"]["da_tol"])
    land = opt(A, "capex", "anchor_land")
    chk = da_check(cf, cal, life, opt(A, "capex", "anchor_rest") == "cohort",
                   None if land is None else float(land["da_half"]))
    fit = {"life_parts": parts, "life_raw": raw_life, "da_check": chk}
    msg = "; ".join(f"{k} модель {v['model']:.1f} против факта {v['fact']:.1f} ({100 * v['dev']:+.1f} %)"
                    for k, v in chk.items())
    if any(abs(v["dev"]) > tol for v in chk.values() if v["check"]):
        flags.append(f"сверка амортизации вне ± {100 * tol:.0f} % (по ходу ядра: 9М = факт якоря + DA первого "
                     f"прогнозного / 2, L = {life:g}; полные годы — справка): {msg}")

    # ---- оборотный капитал (§4.10)
    from model.core import nwc_fact
    put(("nwc", "share_h1"), nwc_fact(A, cf, h_end(cal.anchor)) / (rev_base(A, cf, [cal.anchor]) + rev_base(A, cf, [a1])),
        f"NWC без банка на {h_end(cal.anchor)} / R°_ann({cal.anchor})")
    put(("nwc", "share_h2"), nwc_fact(A, cf, h_end(a1)) / (rev_base(A, cf, [a1]) + rev_base(A, cf, [cal.a2])),
        f"NWC без банка на {h_end(a1)} / R°_ann({a1})")

    # ---- налог: доля IT-корзины (§4.11, §17)
    tg = float(A["tax"]["general"])
    tp = [cal.a2, a1, cal.anchor]
    num = -fsum(cf.tax_rate_diff[p] for p in tp)
    rule = opt(A, "tax", "base_rule")                                  # книга 1.2 (§4.11): `f_other`
    if opt(A, "tax", "general_blocks"):         # книга 1.4 (§4.11): база без блоков общей ставки
        den = fsum((tg - path_value(A["tax"]["it_rate"], p)) * max(0.0, tax_base(cf, p, rule)
                                                                    - general_part_fact(A, cf, p)) for p in tp)
        gl = f" без блоков общей ставки {', '.join(A['tax']['general_blocks'])}"
    else:
        den = fsum((tg - path_value(A["tax"]["it_rate"], p)) * max(0.0, tax_base(cf, p, rule)) for p in tp)
        gl = ""
    s = put(("tax", "it_share"), num / den,
            f"−Σ «разница в ставках» {tp[0]}–{tp[-1]} / Σ (τ_g − τ_IT) × max(0, база §4.11 на фактах{gl})")

    # ---- отдача капитала окна (§6.2, §17)
    y_obs = cal.obs[:4]
    tau = s * path_value(A["tax"]["it_rate"], f"{y_obs}H2") + (1 - s) * tg
    E12, SBC12, DA12 = ssum(eb["group"], cal.ltm), ssum(cf.sbc, cal.ltm), ssum(cf.da, cal.ltm)
    g4 = opt(A, "checks", "roic_tax") == "general"      # книга 1.3 (§13.2, решение Г4): налог окна по τ_g
    if itc is None and not g4:
        tax12 = tau * max(0.0, tax_base(cf, cal.ltm, rule))
        put(("terminal", "roic"), (E12 - SBC12 - DA12 - tax12) / cf.fa_anchor,
            f"NOPAT 12 мес. (E − SBC − DA − налог §4.11 по τ̄({y_obs})) / FA на {h_end(cal.anchor)} (ОС + НМА без гудвила "
            f"+ право пользования машин, земли и прочего; оборотный капитал в базу не входит)")
    else:
        # книга 1.3: E — минус добавка взносов IT 4К25 целиком (§4.4); налог — по правилу §4.11 от EBITDA блоков,
        # поправленных долями добавки (A, B, «прочие» F); остаток добавки по блокам не разложен — базу не меняет
        x_all = float(itc["amount"]) if itc is not None else 0.0
        base12 = tax_base(cf, cal.ltm, rule, xit or None)
        t12 = tg if g4 else tau
        tax12 = t12 * max(0.0, base12)
        roic = put(("terminal", "roic"), (E12 - x_all - SBC12 - DA12 - tax12) / cf.fa_anchor,
                   f"NOPAT 12 мес. (E − {x_all:.3f} взносов IT 4К25 − SBC − DA − налог §4.11 по "
                   f"{'τ_g' if g4 else f'τ̄({y_obs})'}, база {base12:.3f}) / FA на {h_end(cal.anchor)} (ОС + НМА без "
                   f"гудвила + право пользования машин, земли и прочего; оборотный капитал в базу не входит)")
        # печать: прочтение образца 12в — налог на неисправленной базе
        tax_s = t12 * max(0.0, tax_base(cf, cal.ltm, rule))
        fit["roic_window"] = {"book": roic, "sample": (E12 - x_all - SBC12 - DA12 - tax_s) / cf.fa_anchor,
                              "flat": (E12 - x_all - SBC12 - DA12) * (1.0 - tg) / cf.fa_anchor}

    # ---- спред долга якоря (§4.13, §17)
    issues = list(A["financing"]["floaters"])
    spreads = [need(cf.F, f"balance.floater_spread.{s}") * 0.01 for s in issues]
    put(("financing", "spread"), fsum(spreads) / len(spreads),
        f"среднее спредов флоатеров {', '.join(issues)} к ключевой (купон = ключевая + спред, ISS на дату книги): "
        + ", ".join(f"{s} {100 * x:.2f} п.п." for s, x in zip(issues, spreads)))

    import json
    market = json.loads(MARKET.read_text(encoding="utf-8"))
    bu, bi = beta_rule(A, cf, market)
    put(("valuation", "beta_u"), bu,
        f"Харрис — Пингл: β_E {bi['beta_e']:.4f} (с.о. {bi['se']:.3f}; {bi['n']} недель {bi['first']}–{bi['last']}, "
        f"точка отсчёта {bi['start_point']}; дивидендов {bi['dividends']}) × (1 − D/V) + β_d × D/V, D/V {bi['dv']:.4f}; "
        f"ряды — data/market/beta_inputs.json")
    fit["beta"] = bi

    # ---- уровни капиталоёмкости κ (§4.9, §17) — ядром в клетке базового режима, после всех выведенных чисел выше
    if A["capex"].get("rule") == "kappa":
        yw = cal.full_years[-1]
        q9 = quarters(f"{cal.obs[:4]}Q1", cal.obs)
        if opt(A, "capex", "kappa_window") == "ltm":                   # книга 1.2 (§4.9): 12 мес., не 9М
            q9, lab = cal.ltm, "12 мес."
        else:
            lab = f"9М{cal.obs[:4]}"
        base_of = "выручка без финсервисов R°" if opt(A, "meta", "balance_base") == "ex_fs" else "выручка группы"
        sh_y = cf.capex[yw] / rev_base(A, cf, [yw])
        sh_9 = ssum(cf.capex, q9) / rev_base(A, cf, q9)
        b = put(("capex", "kappa_base_capex"), 0.5 * (sh_y + sh_9),
                f"½ × (капвложения в ОС и НМА / {base_of} за {yw} г. {sh_y:.4f} + за {lab} "
                f"({q9[0]}–{q9[-1]}) {sh_9:.4f})")
        V2 = _with(A, V)
        mirror = opt(A, "capex", "kappa_high") == "mirror"             # книга 1.2 (§4.9): высокий зеркален низкому
        all_cells = opt(A, "capex", "kappa_target") == "all_cells"     # книга 1.3 (§4.9): доля окна — ожидание клеток
        targets = {"base": b, "high": None if mirror else float(A["capex"]["kappa_high_capex"])}
        if opt(A, "capex", "season") == "h1_dip":                      # книга 1.3 (§4.9): провал κ на 30.06
            T1, tinfo = h1_target(V2, cf, cal)
            T1 = put(("capex", "h1_share"), T1,
                     "доля I полугодия во вложениях CX: среднее " + ", ".join(f"{y} {v:.4f}" for y, v in tinfo.items())
                     + " (2026H2 — Σ_w p_w CX_w по правилу s)")
            V2["capex"]["h1_share"] = T1
            dip, rounds = float(A["capex"]["h1_dip"]), 0
            while True:                                                # неподвижная точка δ и уровней κ
                V2["capex"]["h1_dip"] = dip
                low, levels, info = kappa_levels(V2, cf, targets, all_cells=all_cells)
                V2["capex"]["kappa_levels"].update({"low": round(low, DECIMALS),
                                                    **{c: round(v, DECIMALS) for c, v in levels.items()}})
                new, hinfo = h1_dip(V2, cf, T1)
                new = round(new, DECIMALS)
                rounds += 1
                if new == dip:
                    break
                if rounds >= FIXED_POINT_ROUNDS:
                    raise ValueError(f"провал κ: δ и уровни κ не сошлись за {FIXED_POINT_ROUNDS} кругов (δ {dip} → {new}, §4.9)")
                dip = new
            put(("capex", "h1_dip"), dip,
                f"δ: средняя за {hinfo['years'][0]}–{hinfo['years'][1]} доля I полугодия во вложениях CX (клетки "
                f"базового режима и уровня, {_wlab(A)}) = capex.h1_share; бисекция на [0; 0,05]; доля "
                f"на корне {hinfo['share']:.10f}; кругов с уровнями κ — {rounds}")
            fit["h1_dip"] = {"rounds": rounds, "by_year": hinfo["by_year"], "target": tinfo}
        else:
            low, levels, info = kappa_levels(V2, cf, targets, all_cells=all_cells)
        if mirror:
            if all_cells:
                how = (f"D_high = 2 × D_b − D(κ_low) = 2 × {info['d_b']:.6f} − {info['low_share']:.6f}; D_b — доля "
                       f"клетки базового режима, при которой ожидание по всем клеткам {info['expect']:.10f} = "
                       f"kappa_base_capex (§4.9)")
            else:
                how = f"D_high = 2 × kappa_base_capex − D(κ_low) = 2 × {b:.6f} − {info['low_share']:.6f} (§4.9)"
            put(("capex", "kappa_high_capex"), info["share"]["high"], how)
            fit["kappa_group_shares"] = info.get("group_shares")
        if all_cells and opt(A, "meta", "release_rules"):       # книга 1.4 (§14): D_b — в выпуск
            put(("capex", "kappa_base_cell_capex"), info["d_b"],
                f"D_b — доля клетки базового режима базового уровня, при которой ожидание по всем клеткам "
                f"{info['expect']:.10f} = kappa_base_capex (§4.9)")
        y1, y2 = info["years"]
        put(("capex", "kappa_levels", "low"), low,
            f"κ на конец {info['first']} = Σ_w p_w FA_w / Σ_w p_w R12_w (клетки базового режима, {_wlab(A)}; "
            f"FA после правила первого полугодия: капвложения года — доля {A['capex']['first_share']} "
            f"выручки года)")
        for c, key in (("base", "kappa_base_capex"), ("high", "kappa_high_capex")):
            if all_cells and c == "base":
                key = f"D_b {info['d_b']:.10f} (ожидание всех клеток = kappa_base_capex)"
            else:
                key = f"capex.{key}"
            put(("capex", "kappa_levels", c), levels[c],
                f"κ, при которой средняя за {y1}–{y2} доля капвложений в ОС и НМА без новой аренды (доля года — "
                f"Σ_w p_w CAPEX_w / Σ_w p_w R_w, клетки базового режима) = {key}; бисекция; доля на корне "
                f"{info['share'][c]:.10f}")
    return V, T, flags, fit


def _with(A: dict, V: dict) -> dict:
    """Копия книги с выведенными числами V."""
    import copy
    B = copy.deepcopy(A)
    for path, v in V.items():
        node: Any = B
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = v
    return B


def kappa_levels(A: dict, cf: CoreFacts, targets: dict, tol: float = 1e-12,
                 all_cells: bool = False) -> tuple[float, dict, dict]:
    """§4.9, §17: уровни κ в клетке базового режима по мирам слоя «свой взгляд» (`joint.world_prob`).

    Низкий — κ на конец первого прогнозного полугодия: Σ_w p_w FA_w / Σ_w p_w R12_w. Уровень c из `targets`
    {уровень: доля} — κ, при которой средняя по годам после первого прогнозного до `capex.levels_to` доля капвложений
    в ОС и НМА без новой аренды d(y) = Σ_w p_w CAPEX_w(y) / Σ_w p_w R_w(y) равна доле; корень — бисекцией на [низкий; 1].
    → (низкий, {уровень: κ}, справка)."""
    from model.book_schema import WORLDS
    from model.core import Context, run_cell

    B = _with(A, {})
    ctx = Context(B, cf)
    pw = derive_world_weights(B)                       # книга 1.4 (§4.9): веса заголовка
    anchor, first = B["meta"]["anchor_period"], B["meta"]["first_period"]
    y1, y2 = int(first[:4]) + 1, int(B["capex"]["levels_to"])
    rho = "base"                                   # клетка базового режима (решение архитектора)
    from model.core import revenue_base
    ra = revenue_base(B, cf, anchor)               # R° якоря (§0; книга 1.1 — выручка группы)
    rkey = "revenue_xfs" if opt(B, "meta", "balance_base") == "ex_fs" else "revenue"
    fa1 = r12 = 0.0
    for w in WORLDS:
        x = run_cell(ctx, w, rho, "base")
        fa1 += pw[w] * x.group["fa"][0]
        r12 += pw[w] * (ra + x.group[rkey][0])
    low = fa1 / r12

    def share(k: float, rk: str = rkey) -> float:
        B["capex"]["kappa_levels"]["base"] = k
        cx = {y: 0.0 for y in range(y1, y2 + 1)}
        rv = dict(cx)
        for w in WORLDS:
            x = run_cell(ctx, w, rho, "base")
            for i, p in enumerate(x.periods):
                y = int(p[:4])
                if y1 <= y <= y2:
                    cx[y] += pw[w] * x.group["capex"][i]
                    rv[y] += pw[w] * x.group[rk][i]
        return fsum(cx[y] / rv[y] for y in cx) / len(cx)

    if all_cells:                                  # книга 1.3 (§4.9): доля окна — ожидание всех клеток
        out, extra = _levels_all_cells(B, ctx, share, low, float(targets["base"]), tol, y1, y2, rkey)
        return low, out, {"years": (y1, y2), "first": first, "share_of": share, **extra}
    out, got = {}, {}
    extra = {}
    if targets.get("high", 0.0) is None:           # книга 1.2 (§4.9): D_high = 2 × D_base − D(κ_low)
        extra["low_share"] = share(low)
        targets = {**targets, "high": 2.0 * targets["base"] - extra["low_share"]}
    for c, target in targets.items():
        lo, hi = low, 1.0
        if share(lo) >= target or share(hi) <= target:
            raise ValueError(f"капиталоёмкость: доля {target} вне [{share(lo):.4f}; {share(hi):.4f}] при κ из "
                             f"[{lo:.4f}; {hi}] (§17)")
        while hi - lo > tol:
            mid = 0.5 * (lo + hi)
            if share(mid) < target:
                lo = mid
            else:
                hi = mid
        out[c] = 0.5 * (lo + hi)
        got[c] = share(out[c])
    if extra:                                      # сверка: доли уровней к выручке группы (прогноз компании 11 %)
        grp = {"low": low, **out}
        extra["group_shares"] = {}
        for c, k in grp.items():
            extra["group_shares"][c] = share(k, "revenue")
        B["capex"]["kappa_levels"]["base"] = out["base"]
    return low, out, {"years": (y1, y2), "first": first, "share": got, "share_of": share, **extra}


def _bisect_up(f, target: float, lo: float, hi: float, tol: float, what: str) -> float:
    """Корень f(x) = target растущей f на [lo; hi] бисекцией до tol."""
    if f(lo) >= target or f(hi) <= target:
        raise ValueError(f"{what}: цель {target} вне [{f(lo):.6f}; {f(hi):.6f}] при x из [{lo:.6f}; {hi:.6f}] (§17)")
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _levels_all_cells(B: dict, ctx, share, low: float, window: float, tol: float, y1: int, y2: int,
                      rkey: str) -> tuple[dict, dict]:
    """§4.9, ключ `capex.kappa_target: all_cells` (книга 1.3, решение Г3): D_b — доля клетки базового режима
    базового уровня, при которой средняя за y1–y2 доля капвложений по всем клеткам (q = P(W) P(ρ) P(C | ρ), миры
    слоя «свой взгляд») равна доле окна; κ_base: D(κ) = D_b; κ_high: D(κ) = 2 D_b − D(κ_low)."""
    from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
    from model.core import run_cell
    from model.grid import capex_probs_by_regime, regime_probs

    pw = derive_world_weights(B)                       # книга 1.4 (§4.9): веса заголовка
    pr, pc = regime_probs(B), capex_probs_by_regime(B)
    cells = [(w, r, c, pw[w] * pr[r] * pc[r][c]) for w in WORLDS for r in REGIMES for c in CAPEX_LEVELS]
    cells = [x for x in cells if x[3] != 0.0]
    d_low = share(low)

    def kappa_for(d: float) -> float:
        if d <= d_low:                             # доля не выше доли низкого уровня — низкий уровень
            return low
        return _bisect_up(share, d, low, 1.0, tol, "капиталоёмкость")

    def expect(kb: float, kh: float) -> float:
        B["capex"]["kappa_levels"].update({"low": low, "base": kb, "high": kh})
        cx = {y: 0.0 for y in range(y1, y2 + 1)}
        rv = dict(cx)
        for w, r, c, q in cells:
            x = run_cell(ctx, w, r, c)
            for i, p in enumerate(x.periods):
                y = int(p[:4])
                if y1 <= y <= y2:
                    cx[y] += q * x.group["capex"][i]
                    rv[y] += q * x.group[rkey][i]
        return fsum(cx[y] / rv[y] for y in cx) / len(cx)

    def outer(d: float) -> float:
        return expect(kappa_for(d), kappa_for(2.0 * d - d_low))

    d_b = _bisect_up(outer, window, max(window - 0.01, d_low), window + 0.01, tol, "доля окна по ожиданию клеток")
    kb, kh = kappa_for(d_b), kappa_for(2.0 * d_b - d_low)
    e = expect(kb, kh)
    got = {"base": share(kb), "high": share(kh)}
    B["capex"]["kappa_levels"].update({"low": low, "base": kb, "high": kh})
    return {"base": kb, "high": kh}, {"share": got, "low_share": d_low, "d_b": d_b, "expect": e}


def h1_target(B: dict, cf: CoreFacts, cal) -> tuple[float, dict]:
    """§4.9, §17 `capex.h1_share` (книга 1.3, решение Г2): доля I полугодия во вложениях CX — среднее полных лет окна
    (факты) и года якоря (I полугодие — факт, II — Σ_w p_w CX_w(2026H2) ядра по правилу s)."""
    from model.book_schema import WORLDS
    from model.core import Context, run_cell

    def cx(p):
        return cf.capex[p] + cf.lease_add[p]
    out = {}
    for y in cal.full_years:
        out[y] = cx(f"{y}H1") / (cx(f"{y}H1") + cx(f"{y}H2"))
    ctx = Context(B, cf)
    pw = derive_world_weights(B)                       # книга 1.4 (§4.9): веса заголовка
    h2 = 0.0
    for w in WORLDS:
        x = run_cell(ctx, w, "base", "base")
        h2 += pw[w] * (x.group["capex"][0] + x.group["lease_new"][0])
    out[cal.anchor[:4]] = cx(cal.anchor) / (cx(cal.anchor) + h2)
    return fsum(out.values()) / len(out), out


def h1_dip(B: dict, cf: CoreFacts, target: float, tol: float = 1e-12) -> tuple[float, dict]:
    """§4.9, §17 `capex.h1_dip` (книга 1.3, решение Г2): δ, при котором средняя за 2027–`levels_to` доля I полугодия
    во вложениях CX клеток (W, base, base) с весами слоя «свой взгляд» = target; бисекция на [0; 0,05]."""
    from model.book_schema import WORLDS
    from model.core import Context, run_cell

    first = B["meta"]["first_period"]
    y1, y2 = int(first[:4]) + 1, int(B["capex"]["levels_to"])
    pw = derive_world_weights(B)                       # книга 1.4 (§4.9): веса заголовка
    by_year: dict = {}

    def h(d: float) -> float:
        B["capex"]["h1_dip"] = d
        ctx = Context(B, cf)
        h1 = {y: 0.0 for y in range(y1, y2 + 1)}
        yr = dict(h1)
        for w in WORLDS:
            x = run_cell(ctx, w, "base", "base")
            for i, p in enumerate(x.periods):
                y = int(p[:4])
                if y1 <= y <= y2:
                    c = x.group["capex"][i] + x.group["lease_new"][i]
                    yr[y] += pw[w] * c
                    if p.endswith("H1"):
                        h1[y] += pw[w] * c
        by_year.clear()
        by_year.update({y: h1[y] / yr[y] for y in h1})
        return fsum(by_year.values()) / len(by_year)

    keep = B["capex"]["h1_dip"]
    d = _bisect_up(lambda x: -h(x), -target, 0.0, DIP_SEARCH_HI, tol, "провал κ на 30.06")
    share = h(d)
    B["capex"]["h1_dip"] = keep
    return d, {"years": (y1, y2), "share": share, "by_year": dict(by_year)}


def _current(A: dict, path: tuple) -> Any:
    node: Any = A
    for part in path:
        node = node[part]
    return node


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rederive", description="перевывод выведенных ключей книги (§17)")
    ap.add_argument("--dry-run", action="store_true", help="только что изменилось бы; книга не пишется")
    ap.add_argument("--table", action="store_true", help="напечатать всю таблицу «ключ — значение — из чего»")
    ap.add_argument("--book", default=str(BOOK_YAML))
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    path = Path(args.book)
    A = yaml.safe_load(path.read_text(encoding="utf-8"))
    cf = core_facts(load_facts(), A["meta"]["anchor_period"])
    V, T, flags, _ = derive(A, cf)
    if args.table:
        print("| Ключ | Значение | Из чего |")
        print("|---|---|---|")
        for k, v, how in T:
            print(f"| `{k}` | {v:g} | {how} |")
    diff = {p: (_current(A, p), v) for p, v in V.items()
            if not (isinstance(_current(A, p), (int, float)) and abs(_current(A, p) - v) <= 10.0 ** -DECIMALS)}
    for p, (old, new) in diff.items():
        print(f"{'было бы' if args.dry_run else 'изменено'}: {'.'.join(map(str, p))}: {old!r} → {new!r}")
    for f in flags:
        print("ФЛАГ: " + f)
    if args.dry_run:
        print(f"ИТОГ: сухой прогон — {len(V)} выведенных чисел, изменилось бы {len(diff)}; книга не писалась")
        return 0
    if diff:
        sys.path.insert(0, str(ROOT / "ops" / "tools"))
        import adopt_worlds  # noqa: E402 — точечная запись скаляров YAML движка приёма миров (общее, только вызов)
        adopt_worlds.set_values(path, {p: v for p, (_o, v) in diff.items()}, {p: DECIMALS for p in diff})
    print(f"ИТОГ: {len(V)} выведенных чисел; изменено {len(diff)}")
    from model.book import load_book
    load_book(path)                      # книга после перевывода проходит закрытую схему
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
