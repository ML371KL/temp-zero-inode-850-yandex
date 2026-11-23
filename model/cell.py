"""Команда клетки — интерфейс для второго счёта (`model/README.md`, раздел «Интерфейс клетки»).

    python -m model.cell --world <N|H|M> --regime <stress|slow|base|strong> --capex <low|base|high>
                         [--book <путь>] [--asof <ГГГГ-ММ-ДД>] --json

Печатает JSON одной клетки: полугодия, выручка и EBITDA блоков A–G и X, строки группы, терминал, V0 клетки (EV),
требования, делитель, цена. Код выхода: 0 — посчитано; 2 — книга или факты не читаются (причина — в stderr).
"""

from __future__ import annotations

import argparse
import json
import sys

from model.book import BookError, load_book
from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
from model.core import CellError, Context, run_cell
from model.facts import FactsError, core_facts, load_facts


def cell_json(world: str, regime: str, capex: str, book: str | None = None, asof: str | None = None) -> dict:
    A = load_book(book)
    cf = core_facts(load_facts(), A["meta"]["anchor_period"])
    ctx = Context(A, cf, asof)
    out = run_cell(ctx, world, regime, capex).to_json()
    out["asof"] = ctx.v.isoformat()
    out["book_version"] = A["meta"]["version"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m model.cell", description="одна клетка сетки (§4–§7)")
    ap.add_argument("--world", required=True, choices=WORLDS)
    ap.add_argument("--regime", required=True, choices=REGIMES)
    ap.add_argument("--capex", required=True, choices=CAPEX_LEVELS)
    ap.add_argument("--book", default=None, help="путь книги (по умолчанию data/assumptions/assumptions.yaml)")
    ap.add_argument("--asof", default=None, help="дата оценки ГГГГ-ММ-ДД (по умолчанию meta.valuation_date)")
    ap.add_argument("--json", action="store_true", required=True, help="ответ — JSON (единственный формат)")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")         # ответ и причины отказа — UTF-8 на любой консоли
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        out = cell_json(args.world, args.regime, args.capex, args.book, args.asof)
    except (BookError, FactsError, CellError, ValueError) as exc:
        print(f"клетка не посчитана: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(out, ensure_ascii=False, allow_nan=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
