"""Быстрые числа: сетка 36 клеток и малая полоса одной командой (docs/MODEL.md §8, §9).

    python -m model.quick [--draws N] [--workers K] [--axes] [--book <путь>] [--asof <ГГГГ-ММ-ДД>] [--json <файл>]
    python -m common.cli quick [те же аргументы]

Печать: медиана (заголовок, к шагу печати книги, и точная), полоса P10–P90 и P25–P75, среднее, P(ниже рынка),
точка (низ, верх, λ), размер малой полосы, время и оценка времени полной полосы (2 000 прогонов). `--axes` —
ещё «суждения по цене ошибки» (точка на краях оси) и цены осей (сдвиг медианы от оси на тех же случайных числах).
Малая полоса — `valuation.uncertainty.small_draws` книги прогонов на `SMALL_WORKERS` процессах; семя то же, что у полной
полосы, поэтому повтор даёт те же числа. Прогоны округляются до 0,1 ₽, как в выпуске.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from model.book import BookError, load_book
from model.checks import round_to_step
from model.core import CellError
from model.facts import FactsError, core_facts, load_facts
from model.grid import evaluate
from model import uncertainty as U


def fmt(x: float, nd: int = 0) -> str:
    return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def pct(x: float, nd: int = 1) -> str:
    return fmt(100 * x, nd) + " %"


def full_estimate(b: U.Band, draws: int) -> dict:
    """Оценка времени полной полосы по замеру малой: секунд на прогон × прогонов (на тех же процессах) и на всех
    ядрах машины (линейно по числу процессов; пересылка и запуск пула — сверху)."""
    per = b.seconds / b.n
    cores = U.workers()
    same = per * draws
    return {"per_draw": per, "same_workers": same, "workers": b.n_workers,
            "all_cores": same * b.n_workers / cores, "cores": cores}


def run(A: dict, cf, *, asof=None, draws: int | None = None, n_workers: int = U.SMALL_WORKERS,
        axes: bool = False) -> dict:
    draws = U.small_draws(A) if draws is None else draws
    t0 = time.perf_counter()
    g = evaluate(A, cf, valuation_date=asof)
    t_grid = time.perf_counter() - t0
    b = U.band(A, cf, valuation_date=g.ctx.v, market_price=g.market_price, draws=draws, n_workers=n_workers)
    s = b.stats()
    out = {"asof": g.ctx.v.isoformat(), "book_version": A["meta"]["version"], "market_price": g.market_price,
           "print_step": float(A["valuation"]["headline"]["print_step"]), "lambda": g.point.lam,
           "draws": b.n, "seed": b.seed, "workers": b.n_workers, "stats": s,
           "point": {"central": g.point.central, "low": g.point.low, "high": g.point.high},
           "axes_used": [ax.key for ax in b.used], "axes_skipped": [ax.name for ax in b.axes if not ax.in_core],
           "contributions": b.contributions(), "seconds": {"grid": t_grid, "band": b.seconds},
           "full_estimate": full_estimate(b, U.full_draws(A)), "full_draws": U.full_draws(A)}
    if axes:
        t1 = time.perf_counter()
        out["judgements"] = U.judgements(A, cf, b)
        out["axis_prices"] = U.axis_prices(A, cf, b, n_workers=n_workers)
        out["seconds"]["axes"] = time.perf_counter() - t1
    out["seconds"]["total"] = time.perf_counter() - t0
    return out


def report(res: dict) -> list[str]:
    s, step, p = res["stats"], res["print_step"], res["point"]
    pr = lambda x: fmt(round_to_step(x, step))          # noqa: E731
    lines = [f"быстрые числа: книга {res['book_version']}, дата оценки {res['asof']}, рынок "
             f"{fmt(res['market_price'], 1)} ₽, λ {fmt(res['lambda'], 2)}",
             f"  медиана (заголовок) {pr(s['median'])} ₽ (точно {fmt(s['median'], 1)}); полоса P10–P90 "
             f"{pr(s['p10'])}–{pr(s['p90'])} ₽ ({fmt(s['p10'], 1)}–{fmt(s['p90'], 1)}); P25–P75 "
             f"{pr(s['p25'])}–{pr(s['p75'])} ₽; среднее {fmt(s['mean'], 1)} ₽; P(ниже рынка) {pct(s['p_below'])}",
             f"  точка {fmt(p['central'])} ₽ (точно {fmt(p['central'], 1)}): низ «рыночные ставки как есть» "
             f"{fmt(p['low'])} ₽, верх «свой макро-взгляд» {fmt(p['high'])} ₽",
             f"  малая полоса: {res['draws']} прогонов, семя {res['seed']}, процессов {res['workers']}; осей "
             f"{len(res['axes_used'])} из {len(res['axes_used']) + len(res['axes_skipped'])}"
             + "".join(f"; ФЛАГ: ось «{n}» ядро не считает — в полосу не входит" for n in res["axes_skipped"])]
    lines.append("  вклад осей: " + "; ".join(f"{c['key']} {pct(c['share'], 0)}" for c in
                                              sorted(res["contributions"], key=lambda c: -c["share"])))
    for j in res.get("judgements") or []:
        if j["in_core"]:
            lines.append(f"  суждение {j['name']}: книга {j['book']:.4g}, края {j['low']:.4g} … {j['high']:.4g} → "
                         f"точка {fmt(j['price_low'])} … {fmt(j['price_high'])} ₽ (размах {fmt(j['swing'])})")
    for a in res.get("axis_prices") or []:
        lines.append(f"  цена оси {a['name']}: медиана без оси {fmt(a['median_without'], 1)} ₽, цена "
                     f"{fmt(a['price'], 1)} ₽")
    t, e = res["seconds"], res["full_estimate"]
    lines.append(f"  время: сетка {fmt(t['grid'], 2)} с, полоса {fmt(t['band'], 1)} с"
                 + (f", оси {fmt(t['axes'], 1)} с" if "axes" in t else "") + f", всего {fmt(t['total'], 1)} с; "
                 f"полная полоса {res['full_draws']} прогонов ≈ {fmt(e['same_workers'], 0)} с на {e['workers']} "
                 f"процессах, ≈ {fmt(e['all_cores'], 0)} с на {e['cores']}")
    lines.append(f"ИТОГ: медиана {pr(s['median'])} ₽; полоса {pr(s['p10'])}–{pr(s['p90'])} ₽; точка "
                 f"{fmt(p['central'])} ₽; время {fmt(t['total'], 1)} с")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m model.quick", description="быстрые числа: сетка и малая полоса")
    ap.add_argument("--draws", type=int, default=None, help="прогонов (по умолчанию valuation.uncertainty.small_draws)")
    ap.add_argument("--workers", type=int, default=None,
                    help=f"процессов (по умолчанию {U.SMALL_WORKERS}; {U.WORKERS_ENV} — если задан)")
    ap.add_argument("--axes", action="store_true", help="ещё суждения по цене ошибки и цены осей")
    ap.add_argument("--book", default=None)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--json", default=None, help="записать числа в JSON-файл")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    k = args.workers if args.workers else (U.workers() if os.environ.get(U.WORKERS_ENV) else U.SMALL_WORKERS)
    try:
        A = load_book(args.book)
        res = run(A, core_facts(load_facts(), A["meta"]["anchor_period"]), asof=args.asof, draws=args.draws,
                  n_workers=k, axes=args.axes)
    except (BookError, FactsError, CellError, ValueError) as exc:
        print(f"ФЛАГ: быстрые числа не посчитаны: {exc}")
        return 2
    finally:
        U.close_pool()
    print("\n".join(report(res)))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
