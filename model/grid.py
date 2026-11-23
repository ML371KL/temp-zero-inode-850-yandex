"""Сетка 36 клеток, слои, точка и ожидаемый путь на ядре сервисов (docs/MODEL.md §3, §7, §8).

* Вероятность клетки в слое L: P_L(W) × P(ρ) × P(C | ρ) (§3): веса миров слоя, вероятности режимов
  `joint.regime_prob` и уровней капвложений в режиме (ключ `joint.capex_prob_given_regime`; без ключа —
  `joint.capex_prob` во всех режимах) — одни во всех слоях.
* Слой — набор весов миров на ОДНИХ И ТЕХ ЖЕ клетках (§8): «свой макро-взгляд» (`joint.world_prob`),
  «вменённые рынком» (`joint.market_implied_prob`), «рыночные ставки как есть» (100 % `joint.neutral_world`).
  V0 слоя = Σ P·EV, D = Σ P·требования, цена — `Context.price_of(V0 − D)` (§7.2).
* Точка = низ + λ·(верх − низ): низ — «рыночные ставки как есть», верх — «свой макро-взгляд» (§8).
  Числа слоёв и точки считаются в том же порядке, что `model.point.evaluate`, — точка совпадает бит в бит.

Клетка — `model.core.run_cell` (ядро не меняется). Единицы: млрд ₽, ₽ на акцию, доли.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
from model.core import CellResult, Context, run_cell
from model.facts import CoreFacts, core_facts

fsum = math.fsum

LAYERS = ("analytical", "market_implied", "macro_neutral")
LAYER_TITLES = {"analytical": "свой макро-взгляд",
                "market_implied": "вменённые рынком",
                "macro_neutral": "рыночные ставки как есть"}
BLOCKS = ("A", "B", "C", "D", "E", "F", "G", "X")      # блоки устава и исключения (§4.1)
BLOCK_TITLES = {"A": "Поиск и ИИ", "B": "Райдтех", "C": "Электронная коммерция и доставка",
                "D": "Плюс, развлечения, Свои Плюсы", "E": "Б2Б Тех", "F": "Автономные технологии и прочие",
                "G": "Финансовые сервисы", "X": "Исключения"}      # устав, раздел 1
# Строки группы, которые складываются по вероятностям клеток (потоки и запасы, §4.12).
GROUP_FLOWS = ("revenue", "ebitda", "attack", "sbc", "da", "ebit", "tax", "capex", "lease_new", "acq", "dwc",
               "dkb", "fcff", "nopat", "interest", "shield")
GROUP_STOCKS = ("nwc", "fa")
# Допуск тождеств ядра (model/README.md, «Интерфейс клетки»).
IDENTITY_TOL = 1e-9


# ------------------------------------------------------------------ веса


def layer_world_weights(A: dict) -> dict:
    """Веса миров трёх слоёв (§8)."""
    J = A["joint"]
    return {"analytical": {w: float(J["world_prob"][w]) for w in WORLDS},
            "market_implied": {w: float(J["market_implied_prob"][w]) for w in WORLDS},
            "macro_neutral": {w: 1.0 if w == J["neutral_world"] else 0.0 for w in WORLDS}}


def regime_probs(A: dict) -> dict:
    return {r: float(A["joint"]["regime_prob"][r]) for r in REGIMES}


def capex_probs(A: dict, regime: str | None = None) -> dict:
    """P(C) или, с ключом `joint.capex_prob_given_regime` и режимом, P(C | ρ) (§3; условность вложений X5)."""
    J = A["joint"]
    if regime is not None and "capex_prob_given_regime" in J:
        return {c: float(J["capex_prob_given_regime"][regime][c]) for c in CAPEX_LEVELS}
    return {c: float(J["capex_prob"][c]) for c in CAPEX_LEVELS}


def capex_probs_by_regime(A: dict) -> dict:
    """{режим: {уровень: P(C | ρ)}}; без ключа — P(C) в каждом режиме (книга 1.0 бит в бит)."""
    return {r: capex_probs(A, r) for r in REGIMES}


# ------------------------------------------------------------------ сетка


@dataclass(frozen=True)
class GridCell:
    result: CellResult
    p: dict                     # слой → вероятность клетки

    @property
    def key(self) -> str:
        r = self.result
        return f"{r.world}|{r.regime}|{r.capex}"


@dataclass(frozen=True)
class LayerResult:
    """Слой (§7.2, §8). V0 = pv_explicit + pv_shield + pv_tv + pv_tv_shield (§5)."""

    name: str
    title: str
    world_weights: dict
    v0: float
    d: float
    equity: float
    price: float
    pv_explicit: float
    pv_shield: float
    pv_tv: float
    pv_tv_shield: float
    terminal_share: float       # (PV терминала + PV щита терминала) / V0
    ebitda_2027: float
    ev_ebitda_2027: float | None
    v0_to_d: float | None

    @property
    def ev_parts(self) -> tuple:
        """Строки EV (ключ, млрд ₽); сумма — V0."""
        return (("pv_explicit", self.pv_explicit), ("pv_shield", self.pv_shield),
                ("pv_tv", self.pv_tv), ("pv_tv_shield", self.pv_tv_shield))


@dataclass(frozen=True)
class Point:
    """Точка при центральных значениях суждений (§8) и сравнение с рынком (§7.3)."""

    low: float
    high: float
    central: float
    lam: float
    rates_view: float
    v0_point: float
    v_star: float
    gap_point: float
    rub_per_1pct_ev_point: float
    ev_ebitda_2027_point: float | None
    equity_share_of_ev: float


@dataclass(frozen=True)
class Grid:
    ctx: Context
    cells: tuple                # GridCell в порядке мир × режим × капвложения
    layers: dict                # имя → LayerResult
    worlds_only: dict           # мир → LayerResult «только этот мир»
    point: Point
    market_price: float
    year_2027: int

    def cell(self, world: str, regime: str, capex: str) -> GridCell:
        key = f"{world}|{regime}|{capex}"
        return next(c for c in self.cells if c.key == key)


def _within(results: dict, pr: dict, pc: dict, w: str, f) -> float:
    """Среднее по режимам и уровням капвложений внутри мира (порядок сумм — как у model.point); pc — P(C | ρ)."""
    return fsum(pr[r] * pc[r][c] * f(results[(w, r, c)]) for r in REGIMES for c in CAPEX_LEVELS)


def _layers_of(ctx: Context, results: dict, weights_by_layer: dict, y27: int) -> dict:
    A = ctx.A
    pr, pc = regime_probs(A), capex_probs_by_regime(A)
    need = {w for wts in weights_by_layer.values() for w in WORLDS if wts[w]}
    fields = {"v0": lambda x: x.ev, "d": lambda x: x.claims["total"],
              "pv_explicit": lambda x: x.pv_explicit, "pv_shield": lambda x: x.pv_shield,
              "pv_tv": lambda x: x.pv_tv, "pv_tv_shield": lambda x: x.pv_tv_shield,
              "pv_terminal": lambda x: x.pv_tv + x.pv_tv_shield, "ebitda_2027": lambda x: x.ebitda_year(y27)}
    per_world = {w: {k: _within(results, pr, pc, w, f) for k, f in fields.items()} for w in WORLDS if w in need}
    out = {}
    for name, wts in weights_by_layer.items():
        s = {k: fsum(wts[w] * per_world[w][k] for w in WORLDS if w in need) for k in fields}
        v0, d = s["v0"], s["d"]
        out[name] = LayerResult(
            name=name, title=LAYER_TITLES.get(name, name), world_weights=dict(wts), v0=v0, d=d,
            equity=v0 - d, price=ctx.price_of(v0 - d), pv_explicit=s["pv_explicit"], pv_shield=s["pv_shield"],
            pv_tv=s["pv_tv"], pv_tv_shield=s["pv_tv_shield"],
            terminal_share=s["pv_terminal"] / v0 if v0 else math.inf, ebitda_2027=s["ebitda_2027"],
            ev_ebitda_2027=v0 / s["ebitda_2027"] if s["ebitda_2027"] > 0 else None,
            v0_to_d=v0 / d if d > 0 else None)
    return out


def point_of(ctx: Context, layers: dict, market_price: float, lam: float | None = None) -> Point:
    """Точка = низ + λ(верх − низ) и её EV против рыночного V* (§7.3, §8)."""
    lam = float(ctx.A["joint"]["lambda"]) if lam is None else float(lam)
    low, high = layers["macro_neutral"].price, layers["analytical"].price
    central = low + lam * (high - low)
    an = layers["analytical"]
    v0_point = ctx.v0_of(central, an.d)
    v_star = ctx.v0_of(market_price, an.d)
    e27 = an.ebitda_2027
    if ctx.A["joint"].get("expectation") == "headline":     # книга 1.4 (MODEL.md §8): знаменатель на весах заголовка
        e27 = lam * an.ebitda_2027 + (1.0 - lam) * layers["macro_neutral"].ebitda_2027
    return Point(low=low, high=high, central=central, lam=lam, rates_view=high - low, v0_point=v0_point,
                 v_star=v_star, gap_point=v0_point / v_star - 1.0, rub_per_1pct_ev_point=ctx.rub_per_1pct(v0_point),
                 ev_ebitda_2027_point=v0_point / e27 if e27 > 0 else None,
                 equity_share_of_ev=(v0_point - an.d) / v0_point if v0_point else math.nan)


def _date(v) -> dt.date | None:
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def evaluate(A: dict, cf: CoreFacts, *, valuation_date=None, market_price: float | None = None,
             lam: float | None = None) -> Grid:
    """Вся сетка книги на дату оценки: 36 клеток, три слоя, «только этот мир», точка (`cf` — CoreFacts или
    факты `load_facts()`)."""
    if not isinstance(cf, CoreFacts):
        cf = core_facts(cf, A["meta"]["anchor_period"])
    ctx = Context(A, cf, _date(valuation_date))
    mp = float(A["meta"]["market_price"] if market_price is None else market_price)
    results = {(w, r, c): run_cell(ctx, w, r, c) for w in WORLDS for r in REGIMES for c in CAPEX_LEVELS}
    y27 = int(A["meta"]["first_period"][:4]) + 1
    weights = layer_world_weights(A)
    layers = _layers_of(ctx, results, weights, y27)
    only = _layers_of(ctx, results, {f"world:{w}": {x: 1.0 if x == w else 0.0 for x in WORLDS} for w in WORLDS},
                      y27)
    pr, pc = regime_probs(A), capex_probs_by_regime(A)
    cells = tuple(GridCell(result=x, p={name: weights[name][w] * pr[r] * pc[r][c] for name in LAYERS})
                  for (w, r, c), x in results.items())
    return Grid(ctx=ctx, cells=cells, layers=layers,
                worlds_only={w: only[f"world:{w}"] for w in WORLDS},
                point=point_of(ctx, layers, mp, lam), market_price=mp, year_2027=y27)


def layer_prices(A: dict, cf: CoreFacts, *, valuation_date=None) -> tuple[float, float]:
    """(низ, верх) — цены слоёв «рыночные ставки как есть» и «свой макро-взгляд» (прогон полосы §9).

    Те же клетки и суммы, что у `evaluate`, без «только этот мир» и пути; считаются только миры с весом.
    """
    ctx = Context(A, cf, _date(valuation_date))
    weights = layer_world_weights(A)
    wanted = {k: weights[k] for k in ("macro_neutral", "analytical")}
    need = {w for wts in wanted.values() for w in WORLDS if wts[w]}
    results = {(w, r, c): run_cell(ctx, w, r, c) for w in WORLDS if w in need
               for r in REGIMES for c in CAPEX_LEVELS}
    y27 = int(A["meta"]["first_period"][:4]) + 1
    layers = _layers_of(ctx, results, wanted, y27)
    return layers["macro_neutral"].price, layers["analytical"].price


# ------------------------------------------------------- ожидаемый путь слоя


def expected_path(grid: Grid, layer: str = "analytical") -> list[dict]:
    """Ожидаемый путь слоя по полугодиям: Σ p × строки клеток (выручка и EBITDA блоков, строки группы §4.12).

    Маржа — отношение средних E[EBITDA] / E[R], а не среднее отношений.
    """
    cells = [c for c in grid.cells if c.p[layer]]
    out = []
    for i, period in enumerate(grid.ctx.P):
        row = {"period": period, "blocks": {}}
        for b in BLOCKS:
            rev = fsum(c.p[layer] * c.result.blocks[b]["revenue"][i] for c in cells)
            ebitda = fsum(c.p[layer] * c.result.blocks[b]["ebitda"][i] for c in cells)
            row["blocks"][b] = {"revenue": rev, "ebitda": ebitda}
        for name in GROUP_FLOWS + GROUP_STOCKS:
            row[name] = fsum(c.p[layer] * c.result.group[name][i] for c in cells)
        row["margin"] = row["ebitda"] / row["revenue"] if row["revenue"] else None
        out.append(row)
    return out


def annual_path(grid: Grid, halves: list[dict]) -> list[dict]:
    """Годы ожидаемого пути: потоки — суммы полугодий, запасы — на конец года.

    Год якоря (если якорь — первое полугодие года первого прогнозного полугодия) — факт якоря + прогноз
    в выручке и EBITDA блоков и группы; поле `fact` называет эти строки, `forecast_periods` — прогнозные
    полугодия года.
    """
    cf = grid.ctx.cf
    years: dict[int, dict] = {}
    for row in halves:
        y = int(row["period"][:4])
        acc = years.setdefault(y, {"year": y, "blocks": {b: {"revenue": 0.0, "ebitda": 0.0} for b in BLOCKS},
                                   "fact": [], "forecast_periods": []})
        acc["forecast_periods"].append(row["period"])
        for b in BLOCKS:
            for k in ("revenue", "ebitda"):
                acc["blocks"][b][k] += row["blocks"][b][k]
        for name in GROUP_FLOWS:
            acc[name] = acc.get(name, 0.0) + row[name]
        for name in GROUP_STOCKS:
            acc[name] = row[name]
    anchor = cf.anchor
    if int(anchor[:4]) in years and anchor.endswith("H1"):
        acc = years[int(anchor[:4])]
        for b in BLOCKS:
            for k, src in (("revenue", cf.rev), ("ebitda", cf.ebitda)):
                acc["blocks"][b][k] += float(src[b][anchor])
        acc["revenue"] += float(cf.rev["group"][anchor])
        acc["ebitda"] += float(cf.ebitda["group"][anchor])
        acc["fact"] = [f"{anchor}: revenue", f"{anchor}: ebitda"]
    out = []
    for y in sorted(years):
        acc = years[y]
        acc["margin"] = acc["ebitda"] / acc["revenue"] if acc["revenue"] else None
        acc["capex_pct"] = (acc["capex"] + acc["lease_new"]) / acc["revenue"] if acc["revenue"] else None
        out.append(acc)
    return out


# ------------------------------------------------------------------ тождества


def invariants(grid: Grid) -> list[dict]:
    """Тождества сетки, которые держит ядро (§13.1; полный набор проверок — задача плана 7)."""
    A = grid.ctx.A
    out = []

    def add(name: str, title: str, worst: float, tol: float) -> None:
        ok = math.isfinite(worst) and worst <= tol
        out.append({"name": name, "title": title, "ok": ok, "detail": f"наибольшее отклонение {worst:.3g}"})

    sums = [abs(fsum(c.p[L] for c in grid.cells) - 1.0) for L in LAYERS]
    sums += [abs(fsum(regime_probs(A).values()) - 1.0), abs(fsum(capex_probs(A).values()) - 1.0)]
    pcr, pr_ = capex_probs_by_regime(A), regime_probs(A)
    sums += [abs(fsum(pcr[r].values()) - 1.0) for r in REGIMES]
    sums += [abs(fsum(pr_[r] * pcr[r][c] for r in REGIMES) - capex_probs(A)[c]) for c in CAPEX_LEVELS]
    add("probabilities", "Вероятности клеток каждого слоя, режимов и уровней капвложений в сумме 1", max(sums), 1e-12)
    gid, fid, eid, cid, fin = 0.0, 0.0, 0.0, 0.0, True
    for c in grid.cells:
        x, g = c.result, c.result.group
        for i in range(len(x.periods)):
            rev = fsum(x.blocks[b]["revenue"][i] for b in BLOCKS)
            ebitda = fsum(x.blocks[b]["ebitda"][i] for b in BLOCKS) + g["attack"][i]
            flow = (g["ebitda"][i] - g["sbc"][i] - g["tax"][i] - g["capex"][i] - g["lease_new"][i] - g["acq"][i]
                    - g["dwc"][i] - g["dkb"][i])
            scale = max(1.0, abs(g["revenue"][i]))
            gid = max(gid, abs(rev - g["revenue"][i]) / scale, abs(ebitda - g["ebitda"][i]) / scale)
            fid = max(fid, abs(flow - g["fcff"][i]) / scale)
        eid = max(eid, abs(x.pv_explicit + x.pv_shield + x.pv_tv + x.pv_tv_shield - x.ev) / max(1.0, abs(x.ev)))
        cl = x.claims
        parts = cl["net_debt_nonbank"] + fsum(v for k, v in cl.items() if k.startswith("bridge.")) \
            + cl["rollover"] + cl["dividends"]
        cid = max(cid, abs(parts - cl["total"]) / max(1.0, abs(cl["total"])))
        fin = fin and all(math.isfinite(v) for v in (x.ev, x.price, cl["total"], x.tv))
    add("group_identity", "Выручка и EBITDA: Σ блоков (+ ущерб атак) = группа в каждом полугодии", gid, IDENTITY_TOL)
    add("fcff_identity", "Поток = EBITDA − SBC − налог − капвложения − аренда − покупки − ΔNWC − ΔKb", fid,
        IDENTITY_TOL)
    add("ev_identity", "EV клетки = PV потока + PV щита + PV терминала + PV щита терминала", eid, IDENTITY_TOL)
    add("claims_identity", "Требования = чистый долг без банка + мост + перекат + дивиденды", cid, IDENTITY_TOL)
    out.append({"name": "finite", "title": "Числа клеток конечны", "ok": fin, "detail": "EV, цена, требования, TV"})
    return out
