"""Ядро сервисов Яндекса: блоки → поток группы → стоимость клетки → цена акции (docs/MODEL.md §4–§7).

    ctx = Context(A, cf, valuation_date=None)          # книга, факты (`model.facts.core_facts`), дата оценки
    res = run_cell(ctx, "H", "base", "base")           # мир × режим × уровень капвложений → CellResult

Проход клетки строго по методике: §4.1 замыкание группы, §4.2 2П2026, §4.3 выручка, §4.4 маржа, §4.5 F, §4.6 капитал
банка, §4.8 выплаты акциями, §4.9 вложения и амортизация когортами, §4.10 оборотный капитал, §4.11 налог, §4.12
поток, §4.13 проценты и щит, §5 дисконт и APV, §6 терминал, §7 требования и цена. Чисел книги и фактов в коде нет:
всё — по ключам книги и узлам фактов. Единицы: деньги — млрд ₽, акции — млн шт., цена — ₽.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from model.book import (BookError, interp_curve, path_value, period_end, period_index, period_of, period_start,
                        periods, prev_period, prev_same_half)
from model.book_schema import ALL_BLOCKS, BLOCKS, CAPEX_LEVELS, REGIMES, WORLDS, opt
from model.facts import CoreFacts, FactsError, need, one_composition

fsum = math.fsum
_TERMINAL_EPS = 1e-4          # §6: если g ≥ r, g = r − 0,0001 (защита X5)
_BISECT_TOL = 1e-12           # §6.2: корень c_T бисекцией до 1e-12


class CellError(ValueError):
    """Клетку нельзя посчитать по методике (например, f(1) < 0 в §6.2)."""


# ---------------------------------------------------------------------------------------------- дата оценки

@dataclass(frozen=True)
class Timing:
    """§5: закрытые и текущее полугодие на дату оценки, доли и времена дисконтирования."""

    closed: int
    elapsed: float
    roll: float                    # Δ переката по форвардам кривой, лет (0 — без переката)
    share: tuple                   # доля полугодия в EV
    t_mid: tuple                   # время середины потока, лет
    t_end: float


def _pos(day: dt.date) -> float:
    """Положение даты на линейке полугодий: номер полугодия + прошедшая доля (§5)."""
    p = period_of(day)
    a, b = period_start(p), period_end(p) + dt.timedelta(days=1)
    return period_index(p) + (day - a).days / (b - a).days


def timing(P: list[str], v: dt.date, curve_as_of: dt.date) -> Timing:
    first = P[0]
    if v < period_start(first) or v > period_end(P[-1]):
        raise BookError(f"дата оценки {v} вне прогнозных полугодий {P[0]}…{P[-1]}")
    closed = sum(1 for p in P if period_end(p) < v)
    cur = P[closed]
    a, b = period_start(cur), period_end(cur) + dt.timedelta(days=1)
    elapsed = (v - a).days / (b - a).days
    share, t = [], []
    for i, _ in enumerate(P):
        if i < closed:
            share.append(0.0)
            t.append(0.0)
        elif i == closed:
            share.append(1.0 - elapsed)
            t.append((1.0 - elapsed) * 0.25)
        else:
            share.append(1.0)
            t.append((1.0 - elapsed) * 0.5 + (i - closed - 1) * 0.5 + 0.25)
    t_end = (1.0 - elapsed) * 0.5 + (len(P) - closed - 1) * 0.5
    roll = 0.5 * (_pos(v) - _pos(curve_as_of)) if v > curve_as_of else 0.0
    return Timing(closed, elapsed, roll, tuple(share), tuple(t), t_end)


# ---------------------------------------------------------------------------------------------- результат

@dataclass
class CellResult:
    world: str
    regime: str
    capex: str
    periods: list
    blocks: dict                   # блок → {"revenue": [...], "ebitda": [...]}; A–G и X
    group: dict                    # строка → [...] по полугодиям
    terminal: dict                 # строки T1, T2 и параметры терминала
    tv: float
    tv_shield: float
    pv_explicit: float
    pv_shield: float
    pv_tv: float
    pv_tv_shield: float
    ev: float
    claims: dict
    shares: float
    price: float
    c_T: float
    g: float
    r: float
    warnings: list = field(default_factory=list)

    @property
    def v0(self) -> float:
        return self.ev

    def ebitda_year(self, year: int) -> float:
        return fsum(e for p, e in zip(self.periods, self.group["ebitda"]) if int(p[:4]) == year)

    def to_json(self) -> dict:
        """Ответ команды клетки (`model/README.md`, «Интерфейс клетки»)."""
        return {
            "world": self.world, "regime": self.regime, "capex": self.capex, "periods": list(self.periods),
            "blocks": {b: {"revenue": list(v["revenue"]), "ebitda": list(v["ebitda"])} for b, v in self.blocks.items()},
            "group": {k: list(v) for k, v in self.group.items()},
            "tv": self.tv, "v0": self.ev, "claims": dict(self.claims), "shares": self.shares, "price": self.price,
            "detail": {"tv_shield": self.tv_shield, "pv_explicit": self.pv_explicit, "pv_shield": self.pv_shield,
                       "pv_tv": self.pv_tv, "pv_tv_shield": self.pv_tv_shield, "c_T": self.c_T, "g": self.g,
                       "r": self.r, "terminal": self.terminal, "warnings": list(self.warnings)},
        }


# ---------------------------------------------------------------------------------------------- контекст

class Context:
    """Всё общее для клеток одной книги и одних фактов на одну дату оценки."""

    def __init__(self, A: dict, cf: CoreFacts, valuation_date: dt.date | str | None = None):
        cf = one_composition(A, cf)                            # книга 1.4 (§0.5): факты одного состава
        self.A, self.cf = A, cf
        m = A["meta"]
        if m["anchor_period"] != cf.anchor:
            raise BookError(f"якорь книги {m['anchor_period']} ≠ якорь фактов {cf.anchor}")
        beta = A["valuation"]["beta_u"]
        if beta is None:
            raise BookError("valuation.beta_u = null: β активов не выведена (правило семейства, §5) — клетка не "
                            "считается")
        self.P = periods(m["first_period"], m["last_period"])
        v = valuation_date or m["valuation_date"]
        self.v = v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))
        self.timing = timing(self.P, self.v, dt.date.fromisoformat(m["curve_as_of"]))
        val = A["valuation"]
        self.premium = float(beta) * float(val["erp"])
        self.g_gov = float(val["governance_discount"])
        self.options_div = options_of_divisor(A, cf)          # книга 1.4 (§7.2): O_d; без ключа — O_v
        self.n_eff = cf.shares_mln + float(val["net_exercise_k"]) * self.options_div
        self.life = float(A["capex"]["asset_life_years"])
        self._cx_hist = self._cx_history()
        self.bridge = {}
        lines = list(A["bridge"]["include"])
        if opt(A, "nwc", "income_tax_place") == "bridge":         # книга 1.2 (§4.10, §7.1): налог к уплате в мосте
            lines += INCOME_TAX_LINES
        for line in lines:
            try:
                amount = need(cf.F, f"balance.{cf.facts_date}.{line['fact']}")
            except FactsError as exc:
                raise BookError(f"bridge.include: строки {line['fact']} нет в балансе на якорь ({exc})") from None
            self.bridge[line["fact"]] = -amount if line["side"] == "asset" else amount
        nxt = opt(A, "bridge", "net_exercise_tax")             # книга 1.3 (§7.1): налог при нетто-исполнении опционов
        if nxt is not None:
            self.bridge["net_exercise_tax"] = float(nxt) * self.options_div * float(m["market_price"]) / 1000.0
        facts_date = dt.date.fromisoformat(m["facts_date"])
        paid = bool(opt(A, "bridge", "dividend_paid"))         # книга 1.2 (§7.1): по выплате ОДДС, если есть
        self.dividends = fsum((d.paid_amount if paid and d.paid_amount is not None else d.amount)
                              for d in cf.dividends if facts_date < d.record_date <= self.v)
        # книга 1.2 (§0, §4.10): выручка без финсервисов на якоре и оборотный капитал якоря по правилам книги
        self.xfs = opt(A, "meta", "balance_base") == "ex_fs"
        self.ro_anchor = revenue_base(A, cf, m["anchor_period"])
        self.nwc_anchor = nwc_fact(A, cf, cf.facts_date)

    # -- вложения до якоря (§4.9): капвложения + поступления права пользования, предыстория «плоско назад»
    def _cx_history(self) -> dict:
        cf, anchor = self.cf, self.A["meta"]["anchor_period"]
        n = int(round(2 * self.life))
        out = {}
        for k in range(0, n + 1):
            i = period_index(anchor) - k
            total = 0.0
            for part in (cf.capex, cf.lease_add):
                j = i
                while _pname(j) not in part:
                    j += 2
                    if j > period_index(anchor):
                        raise BookError(f"нет фактов вложений для предыстории {_pname(i)}")
                total += part[_pname(j)]
            out[_pname(i)] = total
        return out

    def price_of(self, equity: float) -> float:
        """§7.2: капитал → цена акции, ₽."""
        k = (1.0 - self.g_gov) if equity > 0 else 1.0
        return equity * k * 1000.0 / self.n_eff

    def v0_of(self, price: float, d: float) -> float:
        """§7.3: V0(цена)."""
        k = (1.0 - self.g_gov) if price > 0 else 1.0
        return price * self.n_eff / 1000.0 / k + d

    def rub_per_1pct(self, v0: float) -> float:
        return 0.01 * v0 * (1.0 - self.g_gov) * 1000.0 / self.n_eff


def _pname(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


# строки налога на прибыль к уплате и авансов по нему (ключ `nwc.income_tax_place`, §4.10)
INCOME_TAX_LINES = ({"fact": "bs_income_tax_payable", "side": "liability", "label": "налог на прибыль к уплате"},
                    {"fact": "oca_income_tax_prepaid", "side": "asset", "label": "авансы по налогу на прибыль"})
# книга 1.4 (§14): подписи строк моста, которых нет в `bridge.include`
BRIDGE_LABELS = {**{x["fact"]: x["label"] for x in INCOME_TAX_LINES},
                 "net_exercise_tax": "налог при нетто-исполнении опционов"}


def revenue_base(A: dict, cf: CoreFacts, period: str) -> float:
    """§0: R°(P) факта — выручка группы × Σ A–F / Σ A–G за период P (ключ `meta.balance_base: ex_fs`); без ключа —
    выручка группы R(P)."""
    r = cf.rev["group"][period]
    if opt(A, "meta", "balance_base") != "ex_fs":
        return r
    return r * fsum(cf.rev[b][period] for b in ALL_BLOCKS if b != "G") / fsum(cf.rev[b][period] for b in ALL_BLOCKS)


def _balance_line(cf: CoreFacts, date: str, line: str) -> float | None:
    bal = cf.F.raw["balance"].get(date, {})
    return need(cf.F, f"balance.{date}.{line}") if line in bal else None


def nwc_fact(A: dict, cf: CoreFacts, date: str) -> float:
    """§4.10: оборотный капитал без банка на дату баланса по правилам книги — факт минус строки моста
    `nwc.less_bridge`, лежащие в нём на эту дату; с `nwc.income_tax_place: nwc` — плюс авансы по налогу на прибыль
    минус налог к уплате (они тогда внутри). Без ключей — факт (книга 1.1)."""
    v = cf.nwc[date]
    for line in opt(A, "nwc", "less_bridge", default=[]):
        x = _balance_line(cf, date, line)
        if x is not None:
            v = v - x
    if opt(A, "nwc", "income_tax_place") == "nwc":
        v = v - need(cf.F, f"balance.{date}.bs_income_tax_payable") + need(cf.F, f"balance.{date}.oca_income_tax_prepaid")
    return v


# ---------------------------------------------------------------------------------------------- проход клетки

def _df_factory(curve: dict, premium: float, roll: float):
    def df0(t: float) -> float:
        if t <= 0:
            return 1.0
        return (1.0 + interp_curve(curve, t) + premium) ** (-t)
    base = df0(roll)

    def df(t: float) -> float:
        return df0(roll + t) / base
    return df


def run_cell(ctx: Context, world: str, regime: str, capex: str) -> CellResult:
    """Один проход клетки мир × режим × уровень капвложений (§4–§7)."""
    if world not in WORLDS or regime not in REGIMES or capex not in CAPEX_LEVELS:
        raise BookError(f"клетка {world}/{regime}/{capex}: мир из {WORLDS}, режим из {REGIMES}, уровень из "
                        f"{CAPEX_LEVELS}")
    A, cf, P = ctx.A, ctx.cf, ctx.P
    W = A["worlds"][world]
    anchor = A["meta"]["anchor_period"]
    first = P[0]
    n = len(P)
    cpi = [path_value(W["cpi"], p) for p in P]
    key = [path_value(W["key_rate"], p) for p in P]
    year = [int(p[:4]) for p in P]
    y0 = year[0]                       # год первого прогнозного полугодия (2026): старт путей §4.3–4.5
    warns: list[str] = []

    g_ = A["growth"]
    mg = A["margin"]
    alpha = float(g_["A"]["autoru_share"])
    sale = dt.date.fromisoformat(g_["A"]["autoru_sale_date"])
    a_start = period_start(anchor)
    months_anchor = sum(1 for k in range(6) if (a_start.year, a_start.month + k) < (sale.year, sale.month))

    # ---------------- выручка блоков (§4.2, §4.3)
    R = {b: {} for b in BLOCKS}
    for b in BLOCKS:
        hist = cf.rev[b]
        R[b][anchor] = hist[anchor]
        R[b][prev_period(anchor)] = hist[prev_period(anchor)]
    for i, p in enumerate(P):
        for b in BLOCKS:
            base_p = prev_same_half(p)
            if p == first:
                r0 = float(A["path_2026h2"]["real_growth"][b])
                base = cf.rev[b][base_p] * ((1.0 - alpha) if b == "A" else 1.0)
                R[b][p] = base * (1.0 + r0) * (1.0 + cpi[i])
                continue
            y = year[i]
            real = _base_growth(A, b, y, y0) + _shift(A, b, regime, y)
            base = R[b][base_p]
            if b == "A" and base_p == anchor:
                base = base * (1.0 - alpha * months_anchor / 6.0)
            R[b][p] = base * (1.0 + real) * (1.0 + cpi[i])

    # ---------------- замыкание группы (§4.1)
    xR, xE = float(A["excl"]["revenue_share"]), float(A["excl"]["ebitda_share"])
    fR, eo = float(A["f"]["revenue_share"]), float(A["f"]["other_share"])
    S = [fsum(R[b][p] for b in BLOCKS) for p in P]
    Rg = [(1.0 - xR) * s / (1.0 - fR * (1.0 - xR)) for s in S]
    RF = [fR * r for r in Rg]
    RX = [-xR * (s + rf) for s, rf in zip(S, RF)]
    EX = [xE * (s + rf) for s, rf in zip(S, RF)]
    # книга 1.2 (§0, §4.1): выручка без финсервисов R° = R − (1 − x_R) × R_G; без ключа — R
    RO = [Rg[i] - (1.0 - xR) * R["G"][p] for i, p in enumerate(P)] if ctx.xfs else Rg

    # ---------------- маржа и EBITDA блоков (§4.2, §4.4)
    E = {b: {} for b in BLOCKS}
    i1 = 0
    for b in BLOCKS:
        E[b][first] = float(A["path_2026h2"]["margin"][b]) * R[b][first]
        start = (cf.ebitda[b][anchor] + E[b][first]) / (cf.rev[b][anchor] + R[b][first])
        T = float(mg["targets"][b][regime])
        Ym = int(mg["target_year"][b])
        gap = float(mg["season_gap"][b])
        for i, p in enumerate(P):
            if i == i1:
                continue
            y = year[i]
            M = T if y >= Ym else start + (T - start) * (y - y0) / (Ym - y0)
            h1, h2 = f"{y}H1", f"{y}H2"
            w1 = R[b][h1] / (R[b][h1] + R[b][h2])
            m = M + gap * (1.0 - w1) if p.endswith("H1") else M - gap * w1
            E[b][p] = m * R[b][p]

    # ---------------- F (§4.2, §4.5) и ущерб атак
    at = A["f"]["at"]
    E_at0 = 2.0 * cf.ebitda["F_at"][A["meta"]["observation_quarter"]]
    Eat = {}
    for i, p in enumerate(P):
        y = year[i]
        if p == first:
            continue
        if y <= int(at["growth_to"]):
            Eat[p] = E_at0 * (1.0 + float(at["growth_nominal"])) ** (y - y0)
        elif y <= int(at["real_to"]):
            Eat[p] = Eat[prev_same_half(p)] * (1.0 + cpi[i])
        elif y < int(at["zero_from"]):
            Y2, Y3 = int(at["real_to"]), int(at["zero_from"])
            Eat[p] = Eat[f"{Y2}{p[4:]}"] * (Y3 - y) / (Y3 - Y2)
        else:
            Eat[p] = 0.0
    # Ось суждения 6 (§9, вид path): ключ книги `f.at.position` = x; E_at(p; x) = центр(p) + |x| × (край(p) − центр(p)),
    # край — низ «вечный расход» −φ × R(p) при x < 0, верх «ноль с `f.at.high_zero_from`» (центр до него, 0 с него) при
    # x > 0; затем E_at := min(0; E_at). Нет ключа или x = 0 — путь центра без изменений (прежняя механика).
    x_at = float(at.get("position", 0.0))
    if x_at != 0.0:
        phi, z0 = float(at["perpetual_share"]), int(at["high_zero_from"])
        for i, p in enumerate(P):
            if p == first:
                continue
            c = Eat[p]
            edge = -phi * Rg[i] if x_at < 0 else (c if year[i] < z0 else 0.0)
            Eat[p] = min(0.0, c + abs(x_at) * (edge - c))
    EF = [float(A["path_2026h2"]["f_ebitda"]) if p == first else Eat[p] + eo * Rg[i] for i, p in enumerate(P)]
    Eatk = [float(A["attack"]["ebitda_2026h2"]) if p == first else 0.0 for p in P]
    Eg = [fsum(E[b][p] for b in BLOCKS) + EF[i] + EX[i] + Eatk[i] for i, p in enumerate(P)]

    # ---------------- выплаты акциями (§4.8)
    sbc, q = float(A["sbc"]["share"]), float(A["sbc"]["h1_share"])
    Rmap = dict(zip(P, Rg))
    Rmap[anchor] = cf.rev["group"][anchor]
    SBC = []
    for i, p in enumerate(P):
        y = year[i]
        annual = sbc * (Rmap[f"{y}H1"] + Rmap[f"{y}H2"])
        SBC.append(annual * (q if p.endswith("H1") else 1.0 - q))

    # ---------------- оборотный капитал (§4.10), капитал банка (§4.6), покупки (§4.9)
    n1, n2 = float(A["nwc"]["share_h1"]), float(A["nwc"]["share_h2"])
    nwc_anchor = ctx.nwc_anchor
    ROmap = dict(zip(P, RO))
    ROmap[anchor] = ctx.ro_anchor
    NWC, dNWC = [], []
    prev = nwc_anchor
    for i, p in enumerate(P):
        r_ann = ROmap[p] + ROmap[prev_period(p)]
        cur = (n1 if p.endswith("H1") else n2) * r_ann
        NWC.append(cur)
        dNWC.append(cur - prev)
        prev = cur
    kappa = float(A["bank"]["capital_to_revenue"])
    RG = dict(R["G"])
    dKb = [kappa * (RG[p] - RG[prev_same_half(p)]) for p in P]
    if opt(A, "bank", "anchor_capital") == "fact":      # книга 1.3 (§4.6): прирост первого полугодия — от факта якоря
        dKb[0] = kappa * (RG[P[0]] + RG[anchor]) - cf.bank_capital[cf.facts_date]
    ma, ma_until = float(A["ma"]["share"]), int(A["ma"]["until"])
    MA = [ma * Rg[i] if year[i] <= ma_until else 0.0 for i in range(n)]

    # ---------------- терминал до явного участка: g, r, c_T (§6, §6.2)
    pi_lt = float(W["lt"]["inflation"])
    g = (1.0 + float(A["terminal"]["real_growth"])) * (1.0 + pi_lt) - 1.0
    r = float(W["zero_curve"]["LT"]) + ctx.premium
    if g >= r:
        g = r - _TERMINAL_EPS
        warns.append(f"g ≥ r в мире {world}: g = r − {_TERMINAL_EPS}")
    if g <= 0:
        raise BookError(f"терминал: g = {g:.4f} ≤ 0 в мире {world} — отказ книги (§6.2)")
    L = ctx.life
    tau_lt = _tau(A, None)
    last2 = P[-2:]                     # 2036H1, 2036H2
    iH = {p: P.index(p) for p in last2}
    RT = [Rg[iH[p]] * (1.0 + g) for p in last2]
    EbT = [{b: E[b][p] * (1.0 + g) for b in BLOCKS} for p in last2]
    EFT = [EF[iH[p]] * (1.0 + g) for p in last2]
    EXT = [EX[iH[p]] * (1.0 + g) for p in last2]
    ET = [Eg[iH[p]] * (1.0 + g) for p in last2]
    SBCT = [SBC[iH[p]] * (1.0 + g) for p in last2]
    RTO = [RO[iH[p]] * (1.0 + g) for p in last2]
    NWC_T1 = n1 * (RTO[0] + RO[-1])
    dNWCT = [NWC_T1 - NWC[-1], n2 * (RTO[0] + RTO[1]) - NWC_T1]
    dKbT = [kappa * (R["G"][p] * (1.0 + g) - R["G"][p]) for p in last2]
    # книга 1.2 (§4.11, ключ `tax.base_rule: f_other`): расходы «прочих» F уменьшают базу; E_o(2026H2) — факт 3К26 × 2
    f_other = opt(A, "tax", "base_rule") == "f_other"
    if f_other:
        obs = A["meta"]["observation_quarter"]
        EO = [2.0 * cf.ebitda["F_o"][obs] if p == first else eo * Rg[i] for i, p in enumerate(P)]
        EOT = [EO[iH[p]] * (1.0 + g) for p in last2]
        profitT = [fsum(e for e in EbT[h].values() if e > 0) + EOT[h] + EXT[h] for h in (0, 1)]
    else:
        profitT = [fsum(e for e in list(EbT[h].values()) + [EFT[h]] if e > 0) + EXT[h] for h in (0, 1)]

    # книга 1.4 (§4.11, ключ `tax.general_blocks`): часть строк T_h блоков общей ставки — (Σ E_b, Σ доля DA_b)
    gen_on = bool(opt(A, "tax", "general_blocks"))
    GT = ([_general_part(A, EbT[h], {b: R[b][p] * (1.0 + g) for b in BLOCKS}, EFT[h], RF[iH[p]] * (1.0 + g))
           for h, p in enumerate(last2)] if gen_on else [NO_GENERAL, NO_GENERAL])

    def da_T(c: float) -> tuple[float, float]:
        c1, c2 = c * RT[0], c * RT[1]
        a = math.floor(L)
        e = 2 * L - 2 * a
        Sg = (1.0 - (1.0 + g) ** (-a)) / g if g != 0 else float(a)
        d1 = ((c1 + c2) * Sg + e * c2 * (1.0 + g) ** (-(a + 1))) / (2 * L)
        d2 = ((c1 * (1.0 + g) + c2) * Sg + e * c1 * (1.0 + g) ** (-a)) / (2 * L)
        return d1, d2

    def nopat_T(c: float) -> tuple[list, list, list]:
        da = da_T(c)
        tax = [_tax_split(A, tau_lt, profitT[h] - da[h], GT[h], da[h]) for h in (0, 1)]
        nop = [ET[h] - SBCT[h] - da[h] - tax[h] for h in (0, 1)]
        return list(da), tax, nop

    # Ключ книги `capex.rule: kappa` (решение архитектора): вложения и терминал — правило капиталоёмкости κ (§4.9,
    # §6.2); прежняя доля c_T по отдаче окна тогда не считается. Без ключа — прежние правила, бит в бит.
    if A["capex"].get("rule") == "kappa":
        CX, DA, cT, kw = _kappa_path(ctx, capex, P, Rg, RT, g, da_T, RO)
        warns += kw
        ell = float(A["capex"]["lease_share"])
        LEASE = [ell * r_ for r_ in Rg]
        CAPEX = [c - l_ for c, l_ in zip(CX, LEASE)]
    else:
        CX, DA, CAPEX, LEASE, cT = _levels_path(ctx, world, regime, capex, P, Rg, RT, g, nopat_T, dNWCT, warns)

    # ---------------- налог (§4.11), поток (§4.12), проценты и щит (§4.13)
    TAX, EBIT, FCFF, NOPAT, I, TS, SH, FA = [], [], [], [], [], [], [], []
    fa = cf.fa_anchor
    nd = cf.nd_nonbank[cf.facts_date]
    sigma = float(A["financing"]["spread"])
    cap = float(A["tax"]["general"]) if opt(A, "financing", "shield_cap") else None    # книга 1.2 (§4.13)
    for i, p in enumerate(P):
        if f_other:
            base = fsum(e for e in (E[b][p] for b in BLOCKS) if e > 0) + EO[i] - DA[i] + EX[i] + Eatk[i]
        else:
            blocks_e = [E[b][p] for b in BLOCKS] + [EF[i]]
            base = fsum(e for e in blocks_e if e > 0) - DA[i] + EX[i] + Eatk[i]
        gpart = (_general_part(A, {b: E[b][p] for b in BLOCKS}, {b: R[b][p] for b in BLOCKS}, EF[i], RF[i])
                 if gen_on else NO_GENERAL)
        tax = _tax_split(A, _tau(A, year[i]), base, gpart, DA[i])     # книга 1.4 (§4.11): блоки общей ставки
        ebit = Eg[i] - SBC[i] - DA[i]
        TAX.append(tax)
        EBIT.append(ebit)
        FCFF.append(Eg[i] - SBC[i] - tax - CX[i] - MA[i] - dNWC[i] - dKb[i])
        NOPAT.append(ebit - tax)
        fa = fa + CX[i] - DA[i]
        FA.append(fa)
        interest = nd * (key[i] + sigma) / 2.0
        ts = tax / ebit if ebit > 0 else 0.0
        if cap is not None:
            ts = min(ts, cap)
        I.append(interest)
        TS.append(ts)
        SH.append(ts * interest)

    # ---------------- терминал (§6.3, §6.4)
    daT, taxT, nopT = nopat_T(cT)
    CXT = [cT * RT[h] for h in (0, 1)]
    if opt(A, "terminal", "gordon_dip"):       # книга 1.4 (§6.3): строки Гордона с провалом κ — система 2 × 2
        CXT, daT = _gordon_dip(A, capex, RO, RTO, L, g)
        taxT = [_tax_split(A, tau_lt, profitT[h] - daT[h], GT[h], daT[h]) for h in (0, 1)]
        nopT = [ET[h] - SBCT[h] - daT[h] - taxT[h] for h in (0, 1)]
    FT = [ET[h] - SBCT[h] - taxT[h] - CXT[h] - dNWCT[h] - dKbT[h] for h in (0, 1)]
    TV = (FT[0] * (1.0 + r) ** 0.75 + FT[1] * (1.0 + r) ** 0.25) / (r - g)
    H = opt(A, "terminal", "da_cohort_years")
    tk = [] if H is not None and opt(A, "terminal", "shield_cohort") else None     # книга 1.3 (§6.4)
    if H is not None:          # книга 1.2 (§6.3): первые H лет терминала — когортами и правилом κ, Гордон после
        TV = _cohort_terminal(ctx, capex, int(H), CX, FA[-1], RO[-1], RTO, ET, SBCT, dNWCT, dKbT, profitT, tau_lt,
                              g, r, TV, tk, GT)
    IT = nd * (key[-1] + sigma) / 2.0
    ebitT = [ET[h] - SBCT[h] - daT[h] for h in (0, 1)]
    tsT = [(taxT[h] / ebitT[h] if ebitT[h] > 0 else 0.0) for h in (0, 1)]
    if cap is not None:
        tsT = [min(t, cap) for t in tsT]
    ST = [t * IT for t in tsT]
    TVS = (ST[0] * (1.0 + r) ** 0.75 + ST[1] * (1.0 + r) ** 0.25) / r
    if tk is not None:         # книга 1.3 (§6.4): щит первых H лет — на ставке когорт, Гордон щита после них
        pv_s_k = []
        for k, (ebit_k, tax_k) in enumerate(tk):
            t_k = tax_k / ebit_k if ebit_k > 0 else 0.0
            if cap is not None:
                t_k = min(t_k, cap)
            pv_s_k.append(t_k * IT * (1.0 + r) ** (-(0.25 + 0.5 * k)))
        TVS = fsum(pv_s_k) + TVS * (1.0 + r) ** (-int(H))

    # ---------------- APV (§5)
    tm = ctx.timing
    df = _df_factory(W["zero_curve"], ctx.premium, tm.roll)
    pv_f = fsum(FCFF[i] * tm.share[i] * df(tm.t_mid[i]) for i in range(n) if tm.share[i] > 0)
    pv_s = fsum(SH[i] * tm.share[i] * df(tm.t_mid[i]) for i in range(n) if tm.share[i] > 0)
    dfe = df(tm.t_end)
    pv_tv, pv_tvs = TV * dfe, TVS * dfe
    EV = pv_f + pv_s + pv_tv + pv_tvs

    # ---------------- требования и цена (§7)
    roll = (fsum(FCFF[i] + SH[i] - I[i] for i in range(tm.closed))
            + tm.elapsed * (FCFF[tm.closed] + SH[tm.closed] - I[tm.closed]))
    claims = {"net_debt_nonbank": nd, "net_debt_company": cf.nd_company, "bank_money": cf.bank_money}
    claims.update({f"bridge.{k}": v for k, v in ctx.bridge.items()})
    claims["rollover"] = -roll
    claims["dividends"] = ctx.dividends
    claims["total"] = nd + fsum(ctx.bridge.values()) - roll + ctx.dividends
    price = ctx.price_of(EV - claims["total"])

    blocks_out = {b: {"revenue": [R[b][p] for p in P], "ebitda": [E[b][p] for p in P]} for b in BLOCKS}
    blocks_out["F"] = {"revenue": RF, "ebitda": EF}
    blocks_out["X"] = {"revenue": RX, "ebitda": EX}
    blocks_out = {b: blocks_out[b] for b in (*ALL_BLOCKS, "X")}
    group = {"revenue": Rg, "ebitda": Eg, "attack": Eatk, "sbc": SBC, "da": DA, "ebit": EBIT, "tax": TAX,
             "capex": CAPEX, "lease_new": LEASE, "acq": MA, "nwc": NWC, "dwc": dNWC, "dkb": dKb, "fcff": FCFF,
             "nopat": NOPAT, "fa": FA, "interest": I, "tax_rate_cash": TS, "shield": SH}
    if ctx.xfs:
        group["revenue_xfs"] = RO                           # книга 1.2: выручка без финсервисов R° (§0)
    terminal = {"revenue": RT, "ebitda": ET, "sbc": SBCT, "da": daT, "tax": taxT, "capex": CXT, "dwc": dNWCT,
                "dkb": dKbT, "fcff": FT, "nopat": nopT, "shield": ST, "interest": IT}
    return CellResult(world, regime, capex, list(P), blocks_out, group, terminal, TV, TVS, pv_f, pv_s, pv_tv, pv_tvs,
                      EV, claims, ctx.n_eff, price, cT, g, r, warns)


# ---------------------------------------------------------------------------------------------- вложения (§4.9)

def _cohort_da(ctx: Context, n: int):
    """§4.9: амортизация когортами. → da(k, cx_all): DA k-го прогнозного полугодия (k = 1 — первое) по вложениям
    cx_all {полугодие: CX} якоря и прогнозных полугодий до k-го (своё полугодие когорта не амортизирует)."""
    A, cf = ctx.A, ctx.cf
    anchor = A["meta"]["anchor_period"]
    N2 = int(round(2 * ctx.life))
    hist = ctx._cx_hist
    ia = period_index(anchor)
    S0 = fsum(hist[_pname(ia - j)] for j in range(1, N2 + 1))
    da_anchor = cf.da[anchor]
    rest = anchor_rest(ctx) if opt(A, "capex", "anchor_rest") == "cohort" else None     # книга 1.3 (§4.9)
    land = opt(A, "capex", "anchor_land") is not None                                       # книга 1.4 (§4.9)

    def da(k: int, cx_all: dict) -> float:
        Sk = fsum(hist[_pname(ia - j)] for j in range(1, N2 - k + 1)) if k < N2 else 0.0
        new = fsum(cx_all[_pname(ia + k - j)] for j in range(1, min(k, N2) + 1)) / N2
        if land:
            add = (rest / N2 if 1 <= k <= N2 else 0.0) + land_da(ctx, k)
            return da_anchor * Sk / S0 + new + add
        if rest is not None and 1 <= k <= N2:
            return da_anchor * Sk / S0 + new + rest / N2
        return da_anchor * Sk / S0 + new
    return da, {anchor: hist[anchor]}


def anchor_rest(ctx: Context) -> float:
    """§4.9, ключ `capex.anchor_rest: cohort` (книга 1.3, решение Г1): остаток капитала якоря сверх формы выбывания
    базы якоря и когорты якоря — REST = FA(якорь) − DA_якоря × Σ_{k=1..2L−1} S(k)/S(0) − CX(якорь)."""
    A, cf = ctx.A, ctx.cf
    anchor = A["meta"]["anchor_period"]
    N2 = int(round(2 * ctx.life))
    hist = ctx._cx_hist
    ia = period_index(anchor)
    S = [fsum(hist[_pname(ia - j)] for j in range(1, N2 - k + 1)) for k in range(0, N2)]
    runoff = fsum(cf.da[anchor] * S[k] / S[0] for k in range(1, N2))
    if opt(A, "capex", "anchor_land") is not None:      # книга 1.4 (§4.9): право пользования землёй — вне остатка
        return cf.fa_anchor - runoff - hist[anchor] - land_anchor(ctx)
    return cf.fa_anchor - runoff - hist[anchor]


def land_anchor(ctx: Context) -> float:
    """§4.9, ключ `capex.anchor_land` (книга 1.4): право пользования землёй на якорь L_0 (баланс, дата фактов)."""
    if getattr(ctx, "_land0", None) is None:
        ctx._land0 = need(ctx.cf.F, f"balance.{ctx.cf.facts_date}.rou_land")
    return ctx._land0


def land_da(ctx: Context, k: int) -> float:
    """§4.9, ключ `capex.anchor_land` (книга 1.4): DA_L(k) = min(d_L, max(0, L_0 − d_L × (k − 1))), k ≥ 1 — сквозной
    номер полугодия после якоря (явный участок и когорты терминала §6.3); без ключа — 0."""
    if k < 1:
        return 0.0
    d = getattr(ctx, "_land_d", None)
    if d is None:
        spec = opt(ctx.A, "capex", "anchor_land")
        d = ctx._land_d = 0.0 if spec is None else float(spec["da_half"])
    if d == 0.0:
        return 0.0
    return min(d, max(0.0, land_anchor(ctx) - d * (k - 1)))


def _levels_path(ctx: Context, world: str, regime: str, capex: str, P: list, Rg: list, RT: list, g: float,
                 nopat_T, dNWCT: list, warns: list):
    """Книга без ключа `capex.rule` (прежние правила): c_T по отдаче окна (§6.2, бисекция), вложения — доли уровней
    `capex.levels` до `levels_to`, линейно к c_T к `converge_year` (§4.9). → CX, DA, CAPEX, LEASE, c_T."""
    A = ctx.A
    n = len(P)
    year = [int(p[:4]) for p in P]
    roic = float(A["terminal"]["roic"])
    # Ключ книги `terminal.reinvestment_excludes_nwc` (решение архитектора): чистые вложения правила — CX − DA, прирост
    # оборотного капитала терминала — отдельной строкой потока; без ключа — прежнее правило CX − DA + ΔNWC.
    nwc_in = 0.0 if A["terminal"].get("reinvestment_excludes_nwc") else 1.0

    def f(c: float) -> float:
        da, _, nop = nopat_T(c)
        return fsum(c * RT[h] - da[h] + nwc_in * dNWCT[h] for h in (0, 1)) - g / roic * fsum(nop)

    if f(0.0) > 0:
        cT = 0.0
        warns.append("c_T: f(0) > 0 — доля вложений терминала 0 (§6.2)")
    elif f(1.0) < 0:
        raise CellError(f"клетка {world}/{regime}/{capex}: f(1) < 0 — c_T вне [0; 1] (§6.2)")
    else:
        lo, hi = 0.0, 1.0
        while hi - lo > _BISECT_TOL:
            mid = 0.5 * (lo + hi)
            if f(mid) < 0:
                lo = mid
            else:
                hi = mid
        cT = 0.5 * (lo + hi)

    kC, ell = float(A["capex"]["levels"][capex]), float(A["capex"]["lease_share"])
    YK, Y30 = int(A["capex"]["converge_year"]), int(A["capex"]["levels_to"])
    K30 = kC + ell
    CXsh = []
    for i in range(n):
        y = year[i]
        if y <= Y30:
            CXsh.append(K30)
        elif y < YK:
            CXsh.append(K30 + (cT - K30) * (y - Y30) / (YK - Y30))
        else:
            CXsh.append(cT)
    CX = [k * r_ for k, r_ in zip(CXsh, Rg)]
    LEASE = [ell * r_ for r_ in Rg]
    CAPEX = [c - l_ for c, l_ in zip(CX, LEASE)]

    da, cx_all = _cohort_da(ctx, n)
    cx_all.update(zip(P, CX))
    DA = [da(k, cx_all) for k in range(1, n + 1)]
    return CX, DA, CAPEX, LEASE, cT


def kappa_line(A: dict, capex: str, k1: float, i: int) -> float:
    """§4.9: κ(p_i) прогнозного полугодия i ≥ 1 (i = 0 — первое прогнозное, 2026H2): линейно по полугодиям от κ₁ на
    конец первого прогнозного полугодия к уровню `capex.kappa_levels[C]` на конец `capex.levels_to`, дальше постоянна.
    Книга 1.3 (`capex.season: h1_dip`): на конец I полугодия — ниже на δ = `capex.h1_dip`."""
    C = A["capex"]
    first = A["meta"]["first_period"]
    K = period_index(f"{int(C['levels_to'])}H2") - period_index(first)
    kC = float(C["kappa_levels"][capex])
    if C.get("season") == "h1_dip" and (period_index(first) + i) % 2 == 0:     # книга 1.3 (§4.9): I полугодие
        return k1 + (kC - k1) * min(i, K) / K - float(C["h1_dip"])
    return k1 + (kC - k1) * min(i, K) / K


def _cohort_terminal(ctx: Context, capex: str, H: int, CX: list, fa_last: float, ro_last: float, RTO: list,
                     ET: list, SBCT: list, dNWCT: list, dKbT: list, profitT: list, tau_lt: float, g: float, r: float,
                     gordon: float, parts: list | None = None, gen: list | None = None) -> float:
    """§6.3, ключ `terminal.da_cohort_years: H` (книга 1.2): 2H полугодий после `last_period` по правилам явного
    участка — амортизация когортами §4.9 и вложения правилом κ (κ_C, граница φ × DA), строки T_h растут на g по годам;
    дальше Гордон строк T_1, T_2, дисконтированный на H лет. → TV на конец `last_period`. Книга 1.3: цель κ полугодий
    I — κ_C − δ (`capex.season: h1_dip`); `parts` — список, куда пишутся (EBIT_k, tax_k) для щита когорт (§6.4)."""
    A = ctx.A
    kC, phi = float(A["capex"]["kappa_levels"][capex]), float(A["capex"]["floor_da"])
    kH1 = kC - float(A["capex"]["h1_dip"]) if A["capex"].get("season") == "h1_dip" else kC     # книга 1.3 (§6.3)
    N2 = int(round(2 * ctx.life))
    land = opt(A, "capex", "anchor_land") is not None
    gen_on = bool(opt(A, "tax", "general_blocks"))
    if N2 > len(CX):
        raise CellError(f"терминал когортами: 2L = {N2} полугодий больше явного участка {len(CX)} (§6.3)")
    hist = list(CX)
    fa, r_prev = fa_last, ro_last
    pv = []
    for k in range(2 * H):
        h, s_k = k % 2, (1.0 + g) ** (k // 2)
        ro_k = RTO[h] * s_k
        d_k = fsum(hist[-j] for j in range(1, N2 + 1)) / N2
        if land:               # книга 1.4 (§4.9, §6.3): право пользования землёй — сквозным счётом до конца
            d_k = d_k + land_da(ctx, len(CX) + 1 + k)
        cx_k = max(d_k + (kH1 if h == 0 else kC) * (ro_k + r_prev) - fa, phi * d_k)
        if gen is None or not gen_on:
            tax_k = tau_lt * max(0.0, profitT[h] * s_k - d_k)
        else:                  # книга 1.4 (§4.11): блоки общей ставки — часть строк T_h, рост s_k
            tax_k = _tax_split(A, tau_lt, profitT[h] * s_k - d_k, (gen[h][0] * s_k, gen[h][1]), d_k)
        if parts is not None:
            parts.append(((ET[h] - SBCT[h]) * s_k - d_k, tax_k))
        f_k = (ET[h] - SBCT[h] - dNWCT[h] - dKbT[h]) * s_k - tax_k - cx_k
        pv.append(f_k * (1.0 + r) ** (-(0.25 + 0.5 * k)))
        hist.append(cx_k)
        fa = fa + cx_k - d_k
        r_prev = ro_k
    return fsum(pv) + gordon * ((1.0 + g) / (1.0 + r)) ** H


def _kappa_path(ctx: Context, capex: str, P: list, Rg: list, RT: list, g: float, da_T, RO: list | None = None):
    """Ключ `capex.rule: kappa` (§4.9, §6.2): вложения через капиталоёмкость κ = FA / выручка 12 мес.

    Первое прогнозное полугодие: капвложения в ОС и НМА = s × выручка года − капвложения якоря (факт), плюс новые
    договоры аренды ℓ × R. Дальше CX(p) = DA(p) + κ(p) × R12(p) − FA(p − 1). В каждом полугодии CX ≥ φ × DA.
    Терминал: Σ_h (CX − DA)(T_h) = g × κ × R12(last_period); DA(T_h) линейна по c_T — корень в замкнутом виде.
    → CX, DA, c_T, предупреждения."""
    A, cf = ctx.A, ctx.cf
    C = A["capex"]
    anchor = A["meta"]["anchor_period"]
    n = len(P)
    s, phi, ell = float(C["first_share"]), float(C["floor_da"]), float(C["lease_share"])
    kC = float(C["kappa_levels"][capex])
    r_anchor = cf.rev["group"][anchor]
    RO = Rg if RO is None else RO                   # база капиталоёмкости R° (§0; книга 1.1 — R)
    ro_anchor = ctx.ro_anchor
    da, cx_all = _cohort_da(ctx, n)
    CX, DA, warns, floors = [], [], [], []
    fa, k1 = cf.fa_anchor, 0.0
    for i, p in enumerate(P):
        d = da(i + 1, cx_all)
        r12 = RO[i] + (RO[i - 1] if i > 0 else ro_anchor)
        if i == 0:
            cx = s * (r_anchor + Rg[0]) - cf.capex[anchor] + ell * Rg[0]
        else:
            cx = d + kappa_line(A, capex, k1, i) * r12 - fa
        if cx < phi * d:
            cx = phi * d
            floors.append(p)
        CX.append(cx)
        DA.append(d)
        cx_all[p] = cx
        fa = fa + cx - d
        if i == 0:
            k1 = fa / r12
    if floors:
        warns.append(f"вложения на нижней границе {phi:g} × DA (§4.9): {', '.join(floors)}")
    d1, d2 = da_T(1.0)
    den = RT[0] + RT[1] - d1 - d2
    if den <= 0:
        raise CellError(f"клетка {capex}: установившаяся амортизация терминала ≥ вложений — c_T не определена (§6.2)")
    cT = g * kC * (RO[-2] + RO[-1]) / den
    return CX, DA, cT, warns


# ---------------------------------------------------------------------------------------------- пути книги

def _base_growth(A: dict, b: str, y: int, y0: int) -> float:
    """§4.3: путь центра реального роста блока в году y > y0 (y0 — год 2П2026); у A путь начинается в y0 + 1."""
    g = A["growth"]
    rL = float(g["lt_real"]) + float(g["lt_premium"][b])
    Y = int(g["converge_year"][b])
    if y >= Y:
        return rL
    if b == "A":
        a27 = float(g["A"]["y2027_real"])
        return a27 + (rL - a27) * (y - (y0 + 1)) / (Y - (y0 + 1))
    r0 = float(A["path_2026h2"]["real_growth"][b])
    return r0 + (rL - r0) * (y - y0) / (Y - y0)


def _shift(A: dict, b: str, regime: str, y: int) -> float:
    """§4.3: сдвиг режима — полный до `growth.shift_to` (2030), гаснет линейно к году схода."""
    g = A["growth"]
    s = float(g["shift"][b][regime])
    Y, Y30 = int(g["converge_year"][b]), int(g["shift_to"])
    if y >= Y:
        return 0.0
    if y <= Y30:
        return s
    return s * (Y - y) / (Y - Y30)


def options_of_divisor(A: dict, cf: CoreFacts) -> float:
    """§7.2, ключ `valuation.options_recognized: ρ` (книга 1.4): O_d = O_v + ρ × (O − O_v), O — неисполненные опционы
    на якорь; без ключа — O_v (книга 1.3)."""
    rho = opt(A, "valuation", "options_recognized")
    if rho is None:
        return cf.options_vested_mln
    total = need(cf.F, f"equity.{cf.facts_date}.options_outstanding")
    return cf.options_vested_mln + float(rho) * (total - cf.options_vested_mln)


NO_GENERAL = (0.0, 0.0)            # §4.11: без ключа `tax.general_blocks` — блоков общей ставки нет


def _general_part(A: dict, Eb: dict, Rb: dict, EF: float, RF: float) -> tuple[float, float]:
    """§4.11, ключ `tax.general_blocks` (книга 1.4): блоки, прибыль которых облагается общей ставкой τ_g (банк IT-льготы
    не имеет). → (Σ E_b, Σ доля DA_b) по прибыльным блокам списка; доля DA_b — по выручке прибыльных блоков П(p)
    (F — одним блоком), как в §4.11; `tax.general_blocks_da: none` — без доли амортизации. Без ключа — (0, 0)."""
    gb = opt(A, "tax", "general_blocks")
    if not gb:
        return (0.0, 0.0)
    e_, r_ = {**Eb, "F": EF}, {**Rb, "F": RF}
    prof = [b for b, e in e_.items() if e > 0]
    if opt(A, "tax", "general_blocks_da") == "none":
        sh = 0.0
    else:
        sh = fsum(r_[b] for b in gb if b in prof) / fsum(r_[b] for b in prof)
    return (fsum(e_[b] for b in gb if b in prof), sh)


def _tax_split(A: dict, tau: float, base: float, gpart: tuple, da: float) -> float:
    """§4.11: без ключа `tax.general_blocks` — τ̄ × max(0, base); с ключом — τ̄ × max(0, base − pg) + τ_g × max(0, pg),
    pg = Σ E_b − DA × Σ доля DA_b блоков общей ставки. `gpart` — NO_GENERAL без ключа."""
    if gpart is NO_GENERAL or not opt(A, "tax", "general_blocks"):
        return tau * max(0.0, base)
    pg = gpart[0] - da * gpart[1]
    return tau * max(0.0, base - pg) + float(A["tax"]["general"]) * max(0.0, pg)


def _gordon_dip(A: dict, capex: str, RO: list, RTO: list, L: float, g: float) -> tuple[list, list]:
    """§6.3, ключ `terminal.gordon_dip` (книга 1.4): строки T_1, T_2 Гордона — установившееся состояние правила κ с
    провалом δ на 30.06. Капитал концов 2036H2, T_1, T_2 — на линии κ; вложения — решение системы 2 × 2
    CX_h − d_h(CX_1, CX_2) = FA_h − FA_{h−1}; DA — установившаяся форма §6.1. → (CX(T_h), DA(T_h))."""
    kc, dl = float(A["capex"]["kappa_levels"][capex]), float(A["capex"]["h1_dip"])
    f0 = kc * (RO[-2] + RO[-1])
    f1 = (kc - dl) * (RTO[0] + RO[-1])
    f2 = kc * (RTO[0] + RTO[1])
    a = math.floor(L)
    e = 2 * L - 2 * a
    Sg = (1.0 - (1.0 + g) ** (-a)) / g if g != 0 else float(a)

    def dap(c1: float, c2: float) -> tuple[float, float]:
        return (((c1 + c2) * Sg + e * c2 * (1.0 + g) ** (-(a + 1))) / (2 * L),
                ((c1 * (1.0 + g) + c2) * Sg + e * c1 * (1.0 + g) ** (-a)) / (2 * L))

    u1, u2 = dap(1.0, 0.0), dap(0.0, 1.0)
    m11, m12, m21, m22 = 1.0 - u1[0], -u2[0], -u1[1], 1.0 - u2[1]
    b1, b2 = f1 - f0, f2 - f1
    det = m11 * m22 - m12 * m21
    cx = [(b1 * m22 - m12 * b2) / det, (m11 * b2 - m21 * b1) / det]
    return cx, list(dap(*cx))


def _tau(A: dict, y: int | None) -> float:
    """§4.11: τ̄(y) = s × τ_IT(y) + (1 − s) × τ_g; y = None — терминал (значение LT траектории)."""
    t = A["tax"]
    spec = t["it_rate"]
    it = float(spec["LT"]) if y is None else path_value(spec, f"{y}H2")
    s = float(t["it_share"])
    return s * it + (1.0 - s) * float(t["general"])


# ---------------------------------------------------------------------------------------------- прежние имена

def price_of_equity(ctx: Context, equity: float) -> float:
    return ctx.price_of(equity)


def v0_from_price(ctx: Context, price: float, d: float) -> float:
    return ctx.v0_of(price, d)


def rub_per_1pct_ev(ctx: Context, v0: float) -> float:
    return ctx.rub_per_1pct(v0)


def observations(*_args, **_kwargs):
    """Наблюдения режимов (§10) — задача плана 5б."""
    raise NotImplementedError("наблюдения режимов (§10) — задача плана 5б")
