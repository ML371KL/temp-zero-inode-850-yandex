"""Полная полоса и таблицы результатов книги — `results.json` и `run_output.txt` расчётом ядра.

    python -B -m model.book_results [--draws N] [--workers K] [--no-reverse] [--no-axis-prices]
                                    [--book <путь>] [--asof <ГГГГ-ММ-ДД>] [--out <каталог>]
    python -m common.cli band [те же аргументы]

Считает сетку на входах книги (`meta.valuation_date`, `meta.market_price`) и полную полосу §9 (2 000 прогонов,
семя `valuation.uncertainty.seed` книги, процессов — `YANDEX_WORKERS` или ядра машины), и на её гиперкубе — суждения по цене
ошибки, цены осей (сдвиг медианы от оси на тех же случайных числах) и обратный DCF §11. Пишет (по умолчанию в
`data/assumptions/`, перевод строк LF):

* `results.json` — точка, слои, «только этот мир», 36 клеток, тождества, ожидаемый путь слоя «свой взгляд» по
  годам, раздел `band` (квантили, печать, среднее, P(ниже рынка), вклады осей, суждения, цены осей, обратный DCF,
  время);
* `run_output.txt` — те же числа для чтения глазами, из `results.json` без нового расчёта.

Прогоны округляются до 0,1 ₽, как в выпуске. Файлы — функция книги, фактов и кода (время расчёта — справочно).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from model import uncertainty as U
from model.book import BOOK_DIR, BookError, load_book
from model.checks import round_to_step
from model.core import CellError
from model.facts import FactsError, core_facts, load_facts
from model.grid import BLOCKS, LAYERS, annual_path, evaluate, expected_path, invariants

SCHEMA = "yandex-book-results-1"


def _ll_layer_prices(A: dict, cf, valuation_date=None) -> tuple[float, float]:
    """(низ, верх) — цены слоёв при ограниченной ответственности акционеров (§13.2, решение Г11): капитал слоя =
    Σ P × max(0, EV − D) по клеткам; цена — §7.2."""
    from math import fsum

    from model.core import Context, run_cell
    from model.grid import (CAPEX_LEVELS, REGIMES, WORLDS, _within, capex_probs_by_regime, layer_world_weights,
                            regime_probs)

    ctx = Context(A, cf, U._date(valuation_date))
    weights = layer_world_weights(A)
    need = {w for k in ("macro_neutral", "analytical") for w in WORLDS if weights[k][w]}
    results = {(w, r, c): run_cell(ctx, w, r, c) for w in WORLDS if w in need for r in REGIMES for c in CAPEX_LEVELS}
    pr, pc = regime_probs(A), capex_probs_by_regime(A)

    def floor(x):
        return max(0.0, x.ev - x.claims["total"])

    eq = {k: fsum(weights[k][w] * _within(results, pr, pc, w, floor) for w in WORLDS if w in need)
          for k in ("macro_neutral", "analytical")}
    return ctx.price_of(eq["macro_neutral"]), ctx.price_of(eq["analytical"])


def limited_liability(A: dict, cf, grid, band) -> dict:
    """Цена ограниченной ответственности (§13.2, объяснение гейта `equity_cushion`): сдвиг точки и медианы полосы
    `band` (те же случайные числа), если капитал клетки и прогона полосы не ниже нуля."""
    lam = grid.point.lam
    lo, hi = _ll_layer_prices(A, cf, grid.ctx.v)
    point = lo + lam * (hi - lo)
    pairs = [_ll_layer_prices(U.trial_book(A, band.used, row), cf, band.valuation_date) for row in band.rows()]
    low = [round(p[0], U.DRAW_DECIMALS) for p in pairs]
    high = [round(p[1], U.DRAW_DECIMALS) for p in pairs]
    med = U.median(U.centers(low, high, band.lam))
    base = band.stats()["median"]
    return {"point": point, "point_shift": point - grid.point.central, "median": med, "median_shift": med - base}


def _clean(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def compute(A: dict, cf, *, asof=None, draws: int | None = None, n_workers: int | None = None,
            reverse: bool = True, with_axis_prices: bool = True) -> dict:
    t0 = time.perf_counter()
    g = evaluate(A, cf, valuation_date=asof)
    step = float(A["valuation"]["headline"]["print_step"])
    b = U.band(A, cf, valuation_date=g.ctx.v, market_price=g.market_price, draws=draws, n_workers=n_workers)
    t_band = time.perf_counter() - t0
    s = b.stats()
    sub = U.Subsample(b, s["median"], cf, size=U.subsample_of(A), n_workers=n_workers)
    t1 = time.perf_counter()
    prices = U.axis_prices(A, cf, b, n_workers=n_workers) if with_axis_prices else None
    t_axes = time.perf_counter() - t1
    t2 = time.perf_counter()
    rdcf = U.reverse_dcf(A, cf, sub, point_only=not reverse)
    t_rev = time.perf_counter() - t2
    years = annual_path(g, expected_path(g))
    return _clean({
        "schema": SCHEMA, "book_version": A["meta"]["version"], "book_date": A["meta"]["date"],
        "asof": g.ctx.v.isoformat(), "market_price": g.market_price, "print_step": step,
        "point": {"central": g.point.central, "low": g.point.low, "high": g.point.high, "lambda": g.point.lam,
                  "v0_point": g.point.v0_point, "v_star": g.point.v_star,
                  "ev_ebitda_2027_point": g.point.ev_ebitda_2027_point,
                  "printed": round_to_step(g.point.central, step)},
        "layers": {n: {"v0": L.v0, "d": L.d, "equity": L.equity, "price": L.price,
                       "terminal_share": L.terminal_share, "ev_ebitda_2027": L.ev_ebitda_2027}
                   for n, L in g.layers.items()},
        "worlds_only": {w: {"price": L.price, "v0": L.v0} for w, L in g.worlds_only.items()},
        "cells": {c.key: {"price": c.result.price, "ev": c.result.ev, "d": c.result.claims["total"],
                          "c_T": c.result.c_T} for c in g.cells},
        "invariants": invariants(g),
        "annual": [{"year": y["year"], "revenue": y["revenue"], "ebitda": y["ebitda"], "margin": y["margin"],
                    "fcff": y["fcff"], "capex_pct": y["capex_pct"],
                    "blocks": {k: y["blocks"][k] for k in BLOCKS}} for y in years],
        "band": {"draws": b.n, "seed": b.seed, "workers": b.n_workers, "stats": s,
                 "printed": {k: round_to_step(s[k], step) for k in ("median", "p10", "p25", "p75", "p90")},
                 "axes": [{"key": ax.key, "name": ax.name, "paths": list(ax.paths), "book": ax.book,
                           "low": ax.low, "high": ax.high, "in_core": ax.in_core} for ax in b.axes],
                 "contributions": b.contributions(), "judgements": U.judgements(A, cf, b),
                 "axis_prices": prices, "reverse_dcf": rdcf, "subsample": sub.m, "delta": sub.delta,
                 "seconds": {"band": t_band, "axis_prices": t_axes, "reverse_dcf": t_rev,
                             "total": time.perf_counter() - t0}},
    })


def _f(x, nd: int = 0) -> str:
    return "—" if x is None else f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def render(R: dict) -> list[str]:
    """Текст для чтения глазами из `results.json` (без нового расчёта)."""
    p, B = R["point"], R["band"]
    s, pr = B["stats"], B["printed"]
    out = [f"Книга {R['book_version']} от {R['book_date']}; дата оценки {R['asof']}; рынок {_f(R['market_price'], 1)} ₽",
           f"Заголовок (медиана полосы): {_f(pr['median'])} ₽ (точно {_f(s['median'], 1)}); полоса P10–P90 "
           f"{_f(pr['p10'])}–{_f(pr['p90'])} ₽; P25–P75 {_f(pr['p25'])}–{_f(pr['p75'])} ₽; среднее {_f(s['mean'], 1)}; "
           f"P(ниже рынка) {_f(100 * s['p_below'], 1)} %",
           f"Точка {_f(p['central'], 1)} ₽ (печать {_f(p['printed'])}): низ {_f(p['low'], 1)}, верх {_f(p['high'], 1)}, "
           f"λ {_f(p['lambda'], 2)}; V0 точки {_f(p['v0_point'], 1)} млрд ₽; V* {_f(p['v_star'], 1)}; EV/EBITDA 2027 "
           f"{_f(p['ev_ebitda_2027_point'], 2)}×",
           f"Полоса: {B['draws']} прогонов, семя {B['seed']}, процессов {B['workers']}; подвыборка обратного DCF "
           f"{B['subsample']}, δ {_f(B['delta'], 1)} ₽"]
    for ax in B["axes"]:
        if not ax["in_core"]:
            out.append(f"ФЛАГ: ось «{ax['name']}» ядро не считает — в полосу не входит")
    out.append("Слои:")
    for n in LAYERS:
        L = R["layers"][n]
        out.append(f"  {n}: V0 {_f(L['v0'], 1)}, D {_f(L['d'], 1)}, цена {_f(L['price'], 1)} ₽, доля терминала "
                   f"{_f(100 * L['terminal_share'], 1)} %")
    out.append("Только этот мир: " + "; ".join(f"{w} {_f(x['price'], 1)} ₽" for w, x in R["worlds_only"].items()))
    out.append("Вклад осей в полосу: " + "; ".join(f"{c['key']} {_f(100 * c['share'], 1)} %" for c in
                                                   sorted(B["contributions"], key=lambda c: -c["share"])))
    out.append("Суждения по цене ошибки (точка на краях оси):")
    for j in B["judgements"]:
        out.append(f"  {j['name']}: книга {j['book']:.4g}, края {j['low']:.4g} … {j['high']:.4g} → "
                   + (f"{_f(j['price_low'], 1)} … {_f(j['price_high'], 1)} ₽, размах {_f(j['swing'], 1)}"
                      if j["in_core"] else "ядро не считает"))
    if B.get("axis_prices"):
        out.append("Цены осей (медиана с осью − медиана с осью на книге, те же случайные числа):")
        for a in B["axis_prices"]:
            out.append(f"  {a['name']}: {_f(a['price'], 1)} ₽ (без оси {_f(a['median_without'], 1)})")
    out.append("Обратный DCF (§11; оси — оси полосы, окно — решение ведущего):")
    for r in B["reverse_dcf"]:
        out.append(f"  {r['name']}: книга {r['book']:.4g}; медиана = рынку при "
                   + (f"{r['solved']:.4g} (невязка {_f(r['gap_full'], 1)} ₽, {'внутри' if r['in_range'] else 'вне'} "
                      f"диапазона книги)" if r["solved"] is not None else r["status"])
                   + f"; точка = рынку при " + (f"{r['point_solved']:.4g}" if r["point_solved"] is not None
                                                else r["point_status"]))
    bad = [i["name"] for i in R["invariants"] if not i["ok"]]
    out.append("Тождества: " + ("все выполнены" if not bad else "НЕ выполнены " + ", ".join(bad)))
    t = B["seconds"]
    out.append(f"Время: полоса {_f(t['band'], 1)} с, цены осей {_f(t['axis_prices'], 1)} с, обратный DCF "
               f"{_f(t['reverse_dcf'], 1)} с, всего {_f(t['total'], 1)} с")
    out.append(f"ИТОГ: медиана {_f(pr['median'])} ₽; полоса {_f(pr['p10'])}–{_f(pr['p90'])} ₽; точка "
               f"{_f(p['central'])} ₽; {B['draws']} прогонов за {_f(t['total'], 0)} с")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m model.book_results", description="полная полоса и таблицы книги")
    ap.add_argument("--draws", type=int, default=None, help="прогонов (по умолчанию valuation.uncertainty.draws)")
    ap.add_argument("--workers", type=int, default=None, help=f"процессов (по умолчанию {U.WORKERS_ENV} или ядра)")
    ap.add_argument("--no-reverse", action="store_true", help="обратный DCF только для точки")
    ap.add_argument("--no-axis-prices", action="store_true", help="без цен осей")
    ap.add_argument("--book", default=None)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--out", default=str(BOOK_DIR), help="каталог results.json и run_output.txt")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        A = load_book(args.book)
        R = compute(A, core_facts(load_facts(), A["meta"]["anchor_period"]), asof=args.asof, draws=args.draws,
                    n_workers=args.workers, reverse=not args.no_reverse, with_axis_prices=not args.no_axis_prices)
    except (BookError, FactsError, CellError, ValueError) as exc:
        print(f"ФЛАГ: полоса не посчитана: {exc}")
        return 2
    finally:
        U.close_pool()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                      newline="\n")
    text = render(R)
    (out / "run_output.txt").write_text("\n".join(text) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(text))
    print(f"записано: {out / 'results.json'}, {out / 'run_output.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
