"""Точка по сетке: 36 клеток, три слоя весов миров, λ (docs/MODEL.md §3, §7.2, §7.3, §8).

    python -m model.point [--book <путь>] [--asof <ГГГГ-ММ-ДД>] [--json <файл>]

Печать: точка, цены слоёв (низ — «рыночные ставки как есть», верх — «свой макро-взгляд»), V0 и требования слоя,
делитель, V0 по мирам, EV/EBITDA 2027 г., доля терминала, строка «ИТОГ: …». Слои, полоса и выпуск — задача плана 5б;
здесь — только точка на клетках ядра.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from model.book import BookError, load_book
from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
from model.core import CellError, Context, run_cell
from model.facts import FactsError, core_facts, load_facts

fsum = math.fsum
LAYERS = ("analytical", "market_implied", "macro_neutral")


def layer_weights(A: dict) -> dict:
    j = A["joint"]
    return {"analytical": {w: float(j["world_prob"][w]) for w in WORLDS},
            "market_implied": {w: float(j["market_implied_prob"][w]) for w in WORLDS},
            "macro_neutral": {w: 1.0 if w == j["neutral_world"] else 0.0 for w in WORLDS}}


def evaluate(A: dict, cf, asof=None) -> dict:
    """Все 36 клеток и числа точки."""
    ctx = Context(A, cf, asof)
    cells = {(w, r, c): run_cell(ctx, w, r, c) for w in WORLDS for r in REGIMES for c in CAPEX_LEVELS}
    pr = {r: float(A["joint"]["regime_prob"][r]) for r in REGIMES}
    from model.grid import capex_probs_by_regime
    pc = capex_probs_by_regime(A)                       # P(C | ρ), §3; без ключа — P(C) во всех режимах
    y27 = int(A["meta"]["first_period"][:4]) + 1

    def within(w: str, f) -> float:
        return fsum(pr[r] * pc[r][c] * f(cells[(w, r, c)]) for r in REGIMES for c in CAPEX_LEVELS)

    per_world = {w: {"v0": within(w, lambda x: x.ev), "d": within(w, lambda x: x.claims["total"]),
                     "pv_terminal": within(w, lambda x: x.pv_tv + x.pv_tv_shield),
                     "ebitda_2027": within(w, lambda x: x.ebitda_year(y27)),
                     "price_mean": within(w, lambda x: x.price)} for w in WORLDS}
    layers = {}
    for name, wts in layer_weights(A).items():
        v0 = fsum(wts[w] * per_world[w]["v0"] for w in WORLDS)
        d = fsum(wts[w] * per_world[w]["d"] for w in WORLDS)
        pvt = fsum(wts[w] * per_world[w]["pv_terminal"] for w in WORLDS)
        e27 = fsum(wts[w] * per_world[w]["ebitda_2027"] for w in WORLDS)
        layers[name] = {"v0": v0, "d": d, "equity": v0 - d, "price": ctx.price_of(v0 - d),
                        "terminal_share": pvt / v0, "ebitda_2027": e27, "ev_ebitda_2027": v0 / e27,
                        "v0_to_d": v0 / d if d > 0 else float("inf")}
    lam = float(A["joint"]["lambda"])
    low, high = layers["macro_neutral"]["price"], layers["analytical"]["price"]
    point = low + lam * (high - low)
    d_an = layers["analytical"]["d"]
    v0_point = ctx.v0_of(point, d_an)
    e27_an = layers["analytical"]["ebitda_2027"]
    if A["joint"].get("expectation") == "headline":         # книга 1.4 (MODEL.md §8): знаменатель на весах заголовка
        e27_an = lam * e27_an + (1.0 - lam) * layers["macro_neutral"]["ebitda_2027"]
    min_cell = min(cells.values(), key=lambda x: x.price)
    return {
        "asof": ctx.v.isoformat(), "book_version": A["meta"]["version"], "lambda": lam,
        "point": point, "low": low, "high": high, "view_on_rates": high - low,
        "v0_point": v0_point, "ev_ebitda_2027_point": v0_point / e27_an, "ebitda_2027": e27_an,
        # доля терминала точки — одной формой с гейтом (§8, §13.2): низ + λ × (верх − низ) долей слоёв
        "terminal_share": layers["macro_neutral"]["terminal_share"] + lam * (layers["analytical"]["terminal_share"]
                                                                             - layers["macro_neutral"]["terminal_share"]),
        "claims": _layer_claims(next(iter(cells.values())).claims, d_an),
        "shares": ctx.n_eff, "rub_per_1pct_ev": ctx.rub_per_1pct(v0_point),
        "layers": layers, "worlds": per_world,
        "min_cell": {"cell": f"{min_cell.world}/{min_cell.regime}/{min_cell.capex}", "price": min_cell.price,
                     "equity": min_cell.ev - min_cell.claims["total"]},
        "warnings": sorted({f"{k[0]}/{k[1]}/{k[2]}: {w}" for k, x in cells.items() for w in x.warnings}),
        "cells": {f"{w}/{r}/{c}": {"v0": x.ev, "d": x.claims["total"], "price": x.price, "c_T": x.c_T,
                                    "terminal_share": (x.pv_tv + x.pv_tv_shield) / x.ev}
                  for (w, r, c), x in cells.items()},
    }


def _layer_claims(cell_claims: dict, d_layer: float) -> dict:
    """Статьи требований слоя: всё, кроме переката, одинаково во всех клетках; перекат слоя — остаток до D слоя."""
    out = {k: v for k, v in cell_claims.items() if k not in ("rollover", "total")}
    static = out["net_debt_nonbank"] + fsum(v for k, v in out.items() if k.startswith("bridge.")) + out["dividends"]
    out["rollover"] = d_layer - static
    out["total"] = d_layer
    return out


def _fmt(x: float, nd: int = 1) -> str:
    return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def report(res: dict) -> list[str]:
    L = res["layers"]
    lines = [f"точка {_fmt(res['point'], 0)} ₽ (дата оценки {res['asof']}, книга {res['book_version']}, λ "
             f"{_fmt(res['lambda'], 2)}): низ «рыночные ставки как есть» {_fmt(res['low'], 0)} ₽, верх «свой "
             f"макро-взгляд» {_fmt(res['high'], 0)} ₽, вменённые рынком {_fmt(L['market_implied']['price'], 0)} ₽"]
    for name in LAYERS:
        x = L[name]
        lines.append(f"  слой {name}: V0 {_fmt(x['v0'])}, требования {_fmt(x['d'])}, капитал {_fmt(x['equity'])} "
                     f"млрд ₽; цена {_fmt(x['price'], 0)} ₽; доля терминала {_fmt(100 * x['terminal_share'])} %; "
                     f"EV/EBITDA 2027 {_fmt(x['ev_ebitda_2027'], 2)}×")
    lines.append("  V0 по мирам: " + "; ".join(f"{w} {_fmt(v['v0'])} млрд ₽ (EBITDA 2027 {_fmt(v['ebitda_2027'])})"
                                                for w, v in res["worlds"].items()))
    c = res["claims"]
    lines.append(f"  требования (свой взгляд) {_fmt(c['total'])} млрд ₽: чистый долг без банка "
                 f"{_fmt(c['net_debt_nonbank'])} (компании {_fmt(c['net_debt_company'])} + деньги банка "
                 f"{_fmt(c['bank_money'])}), мост " + ", ".join(f"{k.removeprefix('bridge.')} {_fmt(v)}" for k, v in c.items()
                                                                  if k.startswith("bridge.")) +
                 f", перекат {_fmt(c['rollover'])}, дивиденды {_fmt(c['dividends'])}")
    lines.append(f"  делитель {_fmt(res['shares'], 3)} млн акций; V0 точки {_fmt(res['v0_point'])} млрд ₽; EV/EBITDA "
                 f"2027 точки {_fmt(res['ev_ebitda_2027_point'], 2)}× (EBITDA 2027 {_fmt(res['ebitda_2027'])}); доля "
                 f"терминала {_fmt(100 * res['terminal_share'])} %; 1 % EV = {_fmt(res['rub_per_1pct_ev'], 0)} ₽")
    m = res["min_cell"]
    lines.append(f"  худшая клетка {m['cell']}: {_fmt(m['price'], 0)} ₽, капитал {_fmt(m['equity'])} млрд ₽")
    for w in res["warnings"]:
        lines.append(f"  предупреждение: {w}")
    lines.append(f"ИТОГ: точка {_fmt(res['point'], 0)} ₽; V0 точки {_fmt(res['v0_point'])} млрд ₽; EV/EBITDA 2027 "
                 f"{_fmt(res['ev_ebitda_2027_point'], 2)}×; доля терминала {_fmt(100 * res['terminal_share'])} %")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m model.point", description="точка по сетке 36 клеток (§8)")
    ap.add_argument("--book", default=None)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--json", default=None, help="записать все числа точки в JSON-файл")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        A = load_book(args.book)
        res = evaluate(A, core_facts(load_facts(), A["meta"]["anchor_period"]), args.asof)
    except (BookError, FactsError, CellError) as exc:
        print(f"ФЛАГ: точка не посчитана: {exc}")
        return 2
    print("\n".join(report(res)))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
