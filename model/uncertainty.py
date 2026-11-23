"""Полоса неопределённости (§9), цены осей и обратный DCF (§11) на ядре сервисов — docs/MODEL.md.

**Оси** — суждения с диапазоном, которых нет в сетке (§9, «Состав осей»), в порядке §9: 6 (расход автономных
технологий, вид `path`), 8 (выплаты акциями), 9 (налог IT-корзины после 2030 г.), 10 (реальный рост терминала),
дисконт за управление, доля покупок компаний, ущерб атак, доля нетто-исполнения опционов. Значение книги — медиана,
края — ключи диапазонов §17 (`sbc.range`, `tax.it_rate_lt_range`, …) как P10, P90. Состав, пути, края и границы осей,
число прогонов полной и малой полосы, семя, подвыборка и окно поиска обратного DCF — в книге (`valuation.uncertainty`,
`valuation.reverse_dcf`, §9, §11, §17); в коде — только константы методики (квантили, точность прогонов, шаги поиска).

**Ось 6 (вид `path`)** — положение x пути расхода автономных технологий, ключ книги `f.at.position` (книга — 0):
ядро считает путь `центр + |x| × (край − центр)` (§4.5, §9); у оси c = 0, a = −1, b = +1.

**Распределение** — двукусочное нормальное §0.4: медиана — значение книги, P10 и P90 — края; значение обрезается
границами оси книги (`bounds`: доли и ставки — [0; 1], ущерб атак — не выше 0, рост терминала — без границ: в схеме он
`num`). **Выборка** — латинский гиперкуб ровно по §9 (`lhs`). Каждый прогон — вся сетка на книге
прогона: низ и верх (`model.grid.layer_prices`), центр = низ + λ·(верх − низ).

**Детерминизм.** Прогон — чистая функция книги, фактов и своей строки значений. Пул процессов (`YANDEX_WORKERS`,
по умолчанию — ядра машины, не больше `MAX_WORKERS`) считает прогоны кусками по порядку и склеивает в том же
порядке: числа бит в бит не зависят от числа процессов. Рабочий — чистый интерпретатор (`spawn`).

**Обратный DCF (§11)** — бисекция на первых `subsample` прогонах гиперкуба со сдвигом δ к полной медиане, затем не
больше двух шагов уточнения на полной полосе; для справки — тот же поиск для точки. Состав осей обратного DCF —
`valuation.reverse_dcf.axes` (ключи осей полосы; решение ведущего: оси полосы, как у X5), окно поиска — центр ±
`search_half_ranges` полуразмахов диапазона книги в границах оси.

Единицы: цена — ₽ на акцию; ставки и доли — доли единицы; ущерб атак — млрд ₽.
"""

from __future__ import annotations

import atexit
import datetime as dt
import math
import os
import pickle
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from multiprocessing import get_context, parent_process
from statistics import NormalDist
from typing import Any, Callable

from model.book import BookError, get_node, override
from model.facts import CoreFacts
from model.grid import layer_prices

fsum = math.fsum

# Квантили печати полосы (§9): 10/25/50/75/90.
QUANTILES = {"p10": 0.10, "p25": 0.25, "median": 0.50, "p75": 0.75, "p90": 0.90}
# Малая полоса — «быстрые числа» на двух процессах (общая машина); число прогонов — книга.
SMALL_WORKERS = 2
# Обратный DCF (§11): бисекция ≤ 40 шагов, стоп по цене 5 ₽, ≤ 2 шагов уточнения; подвыборка быстрой сборки
# (не выпуск); подвыборка выпуска и окно поиска — книга (`valuation.reverse_dcf`).
FAST_SUBSAMPLE = 40
BISECTION_STEPS = 40
REVERSE_TOL_RUB = 5
SECANT_STEPS = 2
# Прогоны в выпуске и в таблицах — до 0,1 ₽ (§9: «низ и верх прогонов округляются до 0,1 ₽»).
DRAW_DECIMALS = 1

WORKERS_ENV = "YANDEX_WORKERS"
MAX_WORKERS = 16
PARALLEL_MIN_DRAWS = 64          # меньше прогонов в вызове — последовательно: пересылка дороже выигрыша
CHUNKS_PER_WORKER = 3            # ядра неравные, мелкие куски выравнивают нагрузку

Z90 = NormalDist().inv_cdf(0.9)
_PHI_INV = NormalDist().inv_cdf


def full_draws(A: dict) -> int:
    """Прогонов полной полосы — `valuation.uncertainty.draws` (§9)."""
    return int(A["valuation"]["uncertainty"]["draws"])


def small_draws(A: dict) -> int:
    """Прогонов малой полосы («быстрые числа») — `valuation.uncertainty.small_draws`."""
    return int(A["valuation"]["uncertainty"]["small_draws"])


def seed_of(A: dict) -> int:
    """Семя гиперкуба — `valuation.uncertainty.seed` (§9)."""
    return int(A["valuation"]["uncertainty"]["seed"])


def subsample_of(A: dict) -> int:
    """Подвыборка обратного DCF — `valuation.reverse_dcf.subsample` (§11)."""
    return int(A["valuation"]["reverse_dcf"]["subsample"])


# ------------------------------------------------------------------- оси


@dataclass(frozen=True)
class Axis:
    """Ось полосы (§9)."""

    index: int                  # столбец гиперкуба (порядок §9)
    key: str
    name: str
    kind: str                   # value | path
    paths: tuple
    unit: str
    book: float                 # медиана: значение книги (у `path` — 0)
    low: float                  # P10
    high: float                 # P90
    bounds: tuple               # (низ, верх) обрезки; None — без границы
    in_core: bool               # прикладывается ли ось к книге прогона

    def value(self, u: float) -> float:
        """Значение оси Q(u) (§0.4) с обрезкой границами схемы."""
        c, a, b = self.book, self.low, self.high
        z = _PHI_INV(u)
        x = c + ((c - a) if z <= 0 else (b - c)) / Z90 * z
        lo, hi = self.bounds
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x


IN_RANGE_EPS = 1e-9            # §11: решение на краю диапазона (ось 6 на ±1) — внутри


def in_range(x: float, lo: float, hi: float) -> bool:
    """§11: x внутри [lo; hi] с допуском IN_RANGE_EPS × (hi − lo) — решение на краю диапазона внутри."""
    return lo - IN_RANGE_EPS * (hi - lo) <= x <= hi + IN_RANGE_EPS * (hi - lo)


def band_axes(A: dict) -> tuple[Axis, ...]:
    """Оси полосы — `valuation.uncertainty.axes` книги `A` по порядку (медиана — значение книги, края — ключ
    `range`; у вида path — c = 0, a = −1, b = +1, §9)."""
    out = []
    for j, spec in enumerate(A["valuation"]["uncertainty"]["axes"]):
        kind, paths = spec["kind"], tuple(spec["paths"])
        if kind == "path":
            book, low, high = 0.0, -1.0, 1.0
        else:
            book = float(get_node(A, paths[0]))
            if "offset" in spec:            # книга 1.2 (§9): края — сдвиги от значения книги
                d_lo, d_hi = (float(x) for x in get_node(A, spec["offset"]))
                low, high = book + d_lo, book + d_hi
            else:
                low, high = (float(x) for x in get_node(A, spec["range"]))
        lo, hi = spec["bounds"]
        if "bounds_from" in spec:           # книга 1.4 (§9): граница — значение ключа книги прогона (ось IT — τ_g)
            lo, hi = (b if path is None else float(get_node(A, path)) for path, b in zip(spec["bounds_from"], (lo, hi)))
        out.append(Axis(index=j, key=spec["key"], name=spec["name"], kind=kind, paths=paths, unit=spec["unit"],
                        book=book, low=low, high=high, bounds=(lo, hi), in_core=_in_core(A, kind, paths)))
    return tuple(out)


def _in_core(A: dict, kind: str, paths: tuple) -> bool:
    """Прикладывается ли ось к книге прогона: ось value — всегда; ось path — когда в книге есть ключ положения пути,
    который читает ядро (`f.at.position`, §4.5)."""
    if kind == "value":
        return True
    try:
        return all(isinstance(get_node(A, p), (int, float)) for p in paths)
    except BookError:
        return False


def applied(axes) -> tuple:
    """Оси, которые прикладываются к книге прогона."""
    return tuple(ax for ax in axes if ax.in_core)


def lhs(n: int, k: int, seed: int) -> list[list[float]]:
    """Латинский гиперкуб u[i][j] ровно по §9: для каждой оси j по порядку перестановка страт `rng.shuffle`,
    затем для i = 0..n−1 — `rng.random()`."""
    rng = random.Random(seed)
    u = [[0.0] * k for _ in range(n)]
    for j in range(k):
        perm = list(range(n))
        rng.shuffle(perm)
        for i in range(n):
            u[i][j] = (perm[i] + rng.random()) / n
    return u


def trial_book(A: dict, axes, values) -> dict:
    """Книга прогона: каждая ось `axes` — в своём значении `values` (оси вида value — подменой по путям)."""
    B = A
    for ax, x in zip(axes, values):
        if not ax.in_core:
            raise ValueError(f"ось {ax.key}: вид {ax.kind} ядро не считает")
        B = override(B, list(ax.paths), "value", float(x))      # path — положение x на пути суждения
    return B


def at_value(A: dict, axis: Axis, x: float) -> dict:
    """Книга с осью в значении x, остальное — книга."""
    return trial_book(A, (axis,), (x,))


# -------------------------------------------------------------- прогоны


def _run_rows(B: dict, cf: CoreFacts, axes, rows, valuation_date) -> list[tuple[float, float]]:
    """(низ, верх) прогонов по порядку строк `rows` (значения осей `axes`)."""
    return [layer_prices(trial_book(B, axes, r), cf, valuation_date=valuation_date) for r in rows]


def workers() -> int:
    """Число процессов полосы: `YANDEX_WORKERS` (целое ≥ 1 или `auto`); auto — ядра машины, не больше
    `MAX_WORKERS`. 1 — последовательно."""
    raw = os.environ.get(WORKERS_ENV, "").strip().lower()
    if raw in ("", "auto"):
        return max(1, min(os.cpu_count() or 1, MAX_WORKERS))
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{WORKERS_ENV}={raw!r}: нужно целое ≥ 1 или auto")
    return min(int(raw), MAX_WORKERS)


_POOL: dict = {"executor": None, "workers": 0, "failed": False}


def _executor(k: int) -> ProcessPoolExecutor:
    if _POOL["executor"] is None or _POOL["workers"] != k:
        close_pool()
        _POOL.update(executor=ProcessPoolExecutor(max_workers=k, mp_context=get_context("spawn")), workers=k)
    return _POOL["executor"]


def close_pool() -> None:
    """Закрывает пул полосы (и при выходе из процесса)."""
    executor, _POOL["executor"], _POOL["workers"] = _POOL["executor"], None, 0
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


atexit.register(close_pool)


def _chunk(blob: bytes) -> list[tuple[float, float]]:
    """Кусок прогонов в рабочем процессе: входы приходят одним pickle."""
    B, cf, axes, rows, valuation_date = pickle.loads(blob)
    return _run_rows(B, cf, axes, rows, valuation_date)


def run_draws(B: dict, cf: CoreFacts, axes, rows, *, valuation_date=None, n_workers: int | None = None,
              min_parallel: int = PARALLEL_MIN_DRAWS) -> list[tuple[float, float]]:
    """(низ, верх) прогонов: пул процессов кусками по порядку строк или здесь же.

    Результат не зависит от числа процессов: каждый прогон — функция своих входов, куски склеиваются в порядке
    строк. Пул недоступен — сообщение в stderr и последовательный расчёт до конца процесса.
    """
    rows = [tuple(r) for r in rows]
    k = workers() if n_workers is None else max(1, int(n_workers))
    if k == 1 or len(rows) < min_parallel or parent_process() is not None or _POOL["failed"]:
        return _run_rows(B, cf, axes, rows, valuation_date)
    size = -(-len(rows) // (k * CHUNKS_PER_WORKER))
    parts = [rows[i:i + size] for i in range(0, len(rows), size)]
    blobs = [pickle.dumps((B, cf, tuple(axes), part, valuation_date), protocol=pickle.HIGHEST_PROTOCOL)
             for part in parts]
    try:
        done = list(_executor(k).map(_chunk, blobs))
    except (BrokenProcessPool, OSError, MemoryError, pickle.PicklingError) as exc:
        _POOL["failed"] = True
        close_pool()
        print(f"пул процессов полосы недоступен ({type(exc).__name__}: {exc}) — считаю последовательно",
              file=sys.stderr)
        return _run_rows(B, cf, axes, rows, valuation_date)
    return [x for part in done for x in part]


# ----------------------------------------------------------- статистика


def quantile7(sorted_values, q: float) -> float:
    """Квантиль типа 7 (линейная интерполяция): h = (n − 1)·q."""
    n = len(sorted_values)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def median(values) -> float:
    return quantile7(sorted(values), QUANTILES["median"])


def centers(low, high, lam: float) -> list[float]:
    """Центр прогона при λ: низ + λ·(верх − низ)."""
    return [a + lam * (b - a) for a, b in zip(low, high)]


def band_stats(low, high, lam: float, market_price: float) -> dict:
    """Квантили 10/25/50/75/90 центра, среднее и P(центр < рынка)."""
    c = centers(low, high, lam)
    s = sorted(c)
    out = {name: quantile7(s, q) for name, q in QUANTILES.items()}
    out["mean"] = fsum(c) / len(c)
    out["p_below"] = sum(1 for x in c if x < market_price) / len(c)
    return out


def ranks(values) -> list[float]:
    """Ранги 1..n, равным — средний ранг."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            out[order[t]] = mean_rank
        i = j + 1
    return out


def pearson(x, y) -> float:
    n = len(x)
    mx, my = fsum(x) / n, fsum(y) / n
    sxy = fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = fsum((a - mx) ** 2 for a in x)
    syy = fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def contributions(axes, u, center) -> list[dict]:
    """Вклад оси (§9): доля квадрата ранговой корреляции (Спирмен) положения u оси с центром при λ книги в сумме
    квадратов по осям. Оси, которые ядро не считает, в сумму не входят."""
    rc = ranks(center)
    rho = [pearson(ranks([row[ax.index] for row in u]), rc) for ax in axes]
    total = fsum(r * r for r in rho)
    return [{"axis": ax.name, "key": ax.key, "index": ax.index, "rank_corr": r,
             "share": r * r / total if total > 0 else 0.0} for ax, r in zip(axes, rho)]


# ---------------------------------------------------------------- полоса


@dataclass(frozen=True)
class Band:
    """Полоса §9: положения u, значения осей и (низ, верх) каждого прогона на книге."""

    n: int
    seed: int
    lam: float
    market_price: float
    valuation_date: dt.date | None
    axes: tuple                 # все оси §9 (и не считаемые ядром)
    u: tuple                    # u[i][j], j — столбец оси
    low: tuple
    high: tuple
    seconds: float = 0.0
    n_workers: int = 1

    @property
    def used(self) -> tuple:
        return applied(self.axes)

    def values(self, i: int, axes=None) -> tuple:
        return tuple(ax.value(self.u[i][ax.index]) for ax in (axes if axes is not None else self.used))

    def rows(self, axes=None, m: int | None = None) -> list[tuple]:
        return [self.values(i, axes) for i in range(self.n if m is None else m)]

    def rounded(self, digits: int = DRAW_DECIMALS) -> tuple[list[float], list[float]]:
        return [round(x, digits) for x in self.low], [round(x, digits) for x in self.high]

    @property
    def center(self) -> list[float]:
        return centers(self.low, self.high, self.lam)

    def stats(self, lam: float | None = None, digits: int | None = DRAW_DECIMALS) -> dict:
        low, high = self.rounded(digits) if digits is not None else (self.low, self.high)
        return band_stats(low, high, self.lam if lam is None else lam, self.market_price)

    def contributions(self) -> list[dict]:
        return contributions(self.used, self.u, self.center)


def _date(v) -> dt.date | None:
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def band(A: dict, cf: CoreFacts, *, valuation_date=None, market_price=None, draws: int | None = None,
         seed: int | None = None, n_workers: int | None = None, min_parallel: int = PARALLEL_MIN_DRAWS) -> Band:
    """Полоса §9 на книге `A`: `draws` прогонов гиперкуба с семенем `seed` (по умолчанию — книга)."""
    import time

    n = int(full_draws(A) if draws is None else draws)
    seed = seed_of(A) if seed is None else seed
    axes = band_axes(A)
    u = lhs(n, len(axes), int(seed))
    v = _date(valuation_date) or _date(A["meta"]["valuation_date"])
    mp = float(market_price if market_price is not None else A["meta"]["market_price"])
    used = applied(axes)
    rows = [tuple(ax.value(u[i][ax.index]) for ax in used) for i in range(n)]
    k = workers() if n_workers is None else max(1, int(n_workers))
    t0 = time.perf_counter()
    pairs = run_draws(A, cf, used, rows, valuation_date=v, n_workers=k, min_parallel=min_parallel)
    return Band(n=n, seed=int(seed), lam=float(A["joint"]["lambda"]), market_price=mp, valuation_date=v,
                axes=axes, u=tuple(tuple(r) for r in u), low=tuple(p[0] for p in pairs),
                high=tuple(p[1] for p in pairs), seconds=time.perf_counter() - t0, n_workers=k)


def headline_without(A: dict, cf: CoreFacts, b: Band, axis: Axis, *, n_workers: int | None = None,
                     digits: int | None = DRAW_DECIMALS) -> float:
    """Медиана полосы с осью `axis`, зафиксированной на значении книги; остальные оси — на тех же случайных
    числах, что у полосы `b`."""
    keep = tuple(ax for ax in b.used if ax.index != axis.index)
    pairs = run_draws(A, cf, keep, b.rows(keep), valuation_date=b.valuation_date, n_workers=n_workers)
    low, high = [p[0] for p in pairs], [p[1] for p in pairs]
    if digits is not None:
        low, high = [round(x, digits) for x in low], [round(x, digits) for x in high]
    return median(centers(low, high, b.lam))


def axis_prices(A: dict, cf: CoreFacts, b: Band, *, n_workers: int | None = None) -> list[dict]:
    """Цена оси — сдвиг заголовка (медианы) от того, что ось разыграна, против оси на значении книги, на тех же
    случайных числах остальных осей."""
    base = b.stats()["median"]
    return [{"key": ax.key, "name": ax.name, "median_without": (m := headline_without(A, cf, b, ax,
                                                                                    n_workers=n_workers)),
             "price": base - m} for ax in b.used]


# ------------------------------------------------ суждения по цене ошибки


def point_price(A: dict, cf: CoreFacts, v, lam: float) -> float:
    lo, hi = layer_prices(A, cf, valuation_date=v)
    return lo + lam * (hi - lo)


def judgements(A: dict, cf: CoreFacts, b: Band) -> list[dict]:
    """«Суждения по цене ошибки» (§9): точка при low и high оси (остальные — книга), размах, вклад в полосу."""
    share = {c["index"]: c["share"] for c in b.contributions()}
    out = []
    for ax in b.axes:
        row = {"index": ax.index, "key": ax.key, "name": ax.name, "kind": ax.kind, "paths": list(ax.paths),
               "unit": ax.unit, "book": ax.book, "low": ax.low, "high": ax.high, "in_core": ax.in_core}
        if ax.in_core:
            pl = point_price(at_value(A, ax, ax.low), cf, b.valuation_date, b.lam)
            ph = point_price(at_value(A, ax, ax.high), cf, b.valuation_date, b.lam)
            row.update(price_low=pl, price_high=ph, swing=abs(ph - pl), share=share[ax.index])
        else:
            row.update(price_low=None, price_high=None, swing=None, share=None)
        out.append(row)
    return out


# ----------------------------------------------------------- бисекция


def bisect(f: Callable[[float], float], a: float, b: float, tol: float
           ) -> tuple[float | None, int, float | None]:
    """Корень f на [a; b]: бисекция ≤ 40 шагов, стоп при |f| ≤ tol (§11).

    Возвращает (корень или None — «недостижимо в поиске», число вычислений f, наклон: хорда от решения до конца
    последней скобки с другим знаком f; бисекция без стопа — хорда последней скобки; стоп на конце — хорда всего
    отрезка)."""
    fa, fb = f(a), f(b)
    calls = 2
    slope = (fb - fa) / (b - a) if b != a else None
    if abs(fa) <= tol:
        return a, calls, slope
    if abs(fb) <= tol:
        return b, calls, slope
    if (fa > 0) == (fb > 0):
        return None, calls, slope
    for _ in range(BISECTION_STEPS):
        m = (a + b) / 2.0
        fm = f(m)
        calls += 1
        if abs(fm) <= tol:
            other, fo = (a, fa) if (fm > 0) != (fa > 0) else (b, fb)
            return m, calls, (fm - fo) / (m - other)
        if (fm > 0) == (fa > 0):
            a, fa = m, fm
        else:
            b, fb = m, fm
    return (a + b) / 2.0, calls, (fb - fa) / (b - a)


def secant_refine(f: Callable[[float], float], x0: float, slope: float | None, lo: float, hi: float,
                  tol: float) -> tuple[float, float, int]:
    """Уточнение корня f от x0 (§11): f(x0); шаг по наклону подвыборки; затем секущая — всего не больше
    `SECANT_STEPS` шагов, стоп при |f| ≤ tol, шаги не выходят из [lo; hi]. (лучшая точка, f в ней, вычислений)."""
    fx = f(x0)
    calls, best = 1, (x0, fx)
    if abs(fx) <= tol or not slope:
        return best[0], best[1], calls
    xa, fa = x0, fx
    xb = min(hi, max(lo, x0 - fx / slope))
    for _ in range(SECANT_STEPS):
        if xb == xa:
            break
        fb = f(xb)
        calls += 1
        if abs(fb) < abs(best[1]):
            best = (xb, fb)
        if abs(fb) <= tol or fb == fa:
            break
        xa, fa, xb = xb, fb, min(hi, max(lo, xb - fb * (xb - xa) / (fb - fa)))
    return best[0], best[1], calls


# ------------------------------------------------------ медиана подвыборки


class Subsample:
    """Медиана центра на первых m прогонах гиперкуба полосы со сдвигом δ (§11) и на всех прогонах.

    `at(B, fixed)` — медиана подвыборки на книге `B`, где оси полосы с номерами `fixed` не разыгрываются
    (суждение зафиксировано подменой в `B`), плюс δ. `full(B, fixed)` — медиана всех прогонов (низ и верх
    округлены до 0,1 ₽, как у заголовка).
    """

    def __init__(self, b: Band, full_median: float, cf: CoreFacts, *, size: int, n_workers=None,
                 min_parallel: int = PARALLEL_MIN_DRAWS):
        self.band, self.cf = b, cf
        self.m = max(1, min(int(size), b.n))
        self.n_workers, self.min_parallel = n_workers, min_parallel
        self.base = median(centers(b.low[:self.m], b.high[:self.m], b.lam))
        self.full_median = full_median
        self.delta = full_median - self.base
        self.evaluations = 0
        self.full_evaluations = 0

    def _median(self, B: dict, fixed: frozenset, m: int, digits: int | None) -> float:
        axes = tuple(ax for ax in self.band.used if ax.index not in fixed)
        pairs = run_draws(B, self.cf, axes, self.band.rows(axes, m), valuation_date=self.band.valuation_date,
                          n_workers=self.n_workers, min_parallel=self.min_parallel)
        low, high = [p[0] for p in pairs], [p[1] for p in pairs]
        if digits is not None:
            low, high = [round(x, digits) for x in low], [round(x, digits) for x in high]
        return median(centers(low, high, self.band.lam))

    def at(self, B: dict, fixed: frozenset = frozenset()) -> float:
        self.evaluations += 1
        return self._median(B, fixed, self.m, None) + self.delta

    def full(self, B: dict, fixed: frozenset = frozenset()) -> float:
        self.full_evaluations += 1
        return self._median(B, fixed, self.band.n, DRAW_DECIMALS)


# ---------------------------------------------------------- обратный DCF


def reverse_axes(A: dict) -> list[dict]:
    """Оси обратного DCF (§11) — `valuation.reverse_dcf.axes`: ключи осей полосы (решение ведущего: оси полосы,
    как у X5); `range` — края книги (P10, P90); `search` — центр ± `search_half_ranges` полуразмахов диапазона в
    границах оси."""
    rd = A["valuation"]["reverse_dcf"]
    w = float(rd["search_half_ranges"])
    by_key = {ax.key: ax for ax in band_axes(A)}
    out = []
    for key in rd["axes"]:
        ax = by_key[key]
        a = ax.book - w * (ax.book - ax.low)
        b = ax.book + w * (ax.high - ax.book)
        lo, hi = ax.bounds
        a = a if lo is None else max(lo, a)
        b = b if hi is None else min(hi, b)
        out.append({"key": ax.key, "name": ax.name, "unit": ax.unit, "kind": "value", "paths": list(ax.paths),
                    "search": [a, b], "range": [ax.low, ax.high], "provisional": False})
    return out


def _matched(b: Band, raxis: dict) -> frozenset:
    """Оси полосы, которые обратный DCF фиксирует: те же пути, что у его оси."""
    paths = set(raxis["paths"])
    return frozenset(ax.index for ax in b.used if set(ax.paths) == paths)


def reverse_dcf(A: dict, cf: CoreFacts, sub: Subsample, axes: list[dict] | None = None,
                point_only: bool = False) -> list[dict]:
    """Что заложено в цену (§11): значение одного суждения, при котором медиана равна рыночной цене.

    Поиск — бисекция на подвыборке + δ (`search_value`), затем уточнение на полной полосе; `gap_full` —
    невязка полной полосы, ₽. Для справки — тот же поиск для точки (`point_solved`). `point_only` — без полосы
    (быстрая сборка)."""
    b = sub.band
    mp, lam, v = b.market_price, b.lam, b.valuation_date
    out = []
    for raxis in (reverse_axes(A) if axes is None else axes):
        kind, paths = raxis["kind"], list(raxis["paths"])
        fixed = _matched(b, raxis)
        before, before_full = sub.evaluations, sub.full_evaluations
        a, c = float(raxis["search"][0]), float(raxis["search"][1])

        def book_at(x: float, kind=kind, paths=paths) -> dict:
            return override(A, paths, kind, x)

        search = solved = gap = None
        if not point_only:
            search, _, slope = bisect(lambda x: sub.at(book_at(x), fixed) - mp, a, c, REVERSE_TOL_RUB)
            if search is not None:
                solved, gap, _ = secant_refine(lambda x: sub.full(book_at(x), fixed) - mp, search, slope, a, c,
                                               REVERSE_TOL_RUB)
        point_solved, _, _ = bisect(lambda x: point_price(book_at(x), cf, v, lam) - mp, a, c, REVERSE_TOL_RUB)
        book = 0.0 if kind == "shift" else float(get_node(A, paths[0]))
        lo, hi = (float(x) for x in raxis["range"])
        status = "skipped" if point_only else ("solved" if solved is not None else "unreachable")
        out.append({"key": raxis.get("key"), "name": raxis["name"], "unit": raxis["unit"], "kind": kind,
                    "paths": paths, "book": book, "solved": solved,
                    "delta": None if solved is None else solved - book,
                    "in_range": solved is not None and in_range(solved, lo, hi),
                    "range": [lo, hi], "search": [a, c],
                    "status": status, "search_value": search, "gap_full": gap, "point_solved": point_solved,
                    "point_status": "solved" if point_solved is not None else "unreachable",
                    "provisional": bool(raxis.get("provisional")), "fixed_axes": sorted(fixed),
                    "evaluations": sub.evaluations - before, "evaluations_full": sub.full_evaluations - before_full})
    return out


# ------------------------------------------------------------ всё вместе


@dataclass(frozen=True)
class Distribution:
    """Полоса и всё, что считается на её гиперкубе (для выпуска и таблиц книги)."""

    band: Band
    stats: dict                 # квантили, среднее, P(ниже рынка) при λ книги — по прогонам до 0,1 ₽
    contributions: list
    judgements: list
    axis_prices: list | None
    reverse_dcf: list
    subsample: int
    delta: float


def distribution(A: dict, cf: CoreFacts, *, valuation_date=None, market_price=None, draws: int | None = None,
                 fast: bool = False, n_workers: int | None = None, with_axis_prices: bool = True,
                 subsample: int | None = None) -> Distribution:
    """Полоса §9, суждения по цене ошибки, цены осей и обратный DCF §11 одним вызовом.

    `fast` — быстрая сборка (не выпуск): малая полоса книги, обратный DCF только для точки, без цен осей.
    """
    draws = full_draws(A) if draws is None else draws
    if fast:
        draws, with_axis_prices = min(draws, small_draws(A)), False
    b = band(A, cf, valuation_date=valuation_date, market_price=market_price, draws=draws, n_workers=n_workers)
    stats = b.stats()
    size = int((FAST_SUBSAMPLE if fast else subsample_of(A)) if subsample is None else subsample)
    sub = Subsample(b, stats["median"], cf, size=size, n_workers=n_workers)
    return Distribution(
        band=b, stats=stats, contributions=b.contributions(), judgements=judgements(A, cf, b),
        axis_prices=axis_prices(A, cf, b, n_workers=n_workers) if with_axis_prices else None,
        reverse_dcf=reverse_dcf(A, cf, sub, point_only=fast), subsample=sub.m, delta=sub.delta)
