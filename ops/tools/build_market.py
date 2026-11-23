# -*- coding: utf-8 -*-
"""Ряды рынка для β активов (методика §17, `valuation.beta_u`): сборка `data/market/beta_inputs.json` из первички.

    python -B ops/tools/build_market.py [--src <каталог первички>] [--check]

Первичка — выгрузки ISS Мосбиржи вне репозитория (каталог `--src`; sha256 файлов — в выходе): история TQBR YNDX (до 14.06.2024)
и YDEX (с 24.07.2024), история индекса MCFTR. Берутся закрытия дней со сделками (`CLOSE` непуст, у акций
`NUMTRADES` > 0: заполненная биржей `LEGALCLOSEPRICE` дней без сделок — не котировка). Дивиденды — реестр
`data/market/dividends.csv` (на акцию, дата отсечки, документ и страница — как в листе фактов). В выход пишутся
sha256 входов; чисел руками здесь нет. `--check` — выход равен сборке (код 1, если нет).
Недельные доходности, окно, склейку и бету считает перевывод (`model/rederive.py::beta_rule`), а не сборка.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT.parents[1] / "850-work" / "yandex" / "src"
OUT = ROOT / "data" / "market" / "beta_inputs.json"
DIVIDENDS = ROOT / "data" / "market" / "dividends.csv"
STOCK_FILES = ("moex_YNDX_TQBR_history_2023-11_2024-07.csv", "moex_YDEX_TQBR_history_2024-07_2026-11.csv")
INDEX_FILE = "moex_MCFTR_history_2023-11_2026-11.csv"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rows(p: Path) -> list[dict]:
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build(src: Path) -> str:
    stock = []
    for name in STOCK_FILES:
        for r in _rows(src / name):
            if r["CLOSE"] and int(r["NUMTRADES"] or 0) > 0:
                stock.append([r["TRADEDATE"], r["SECID"], float(r["CLOSE"])])
    stock.sort()
    index = sorted([r["TRADEDATE"], float(r["CLOSE"])] for r in _rows(src / INDEX_FILE) if r["CLOSE"])
    with DIVIDENDS.open(encoding="utf-8", newline="") as f:
        divs = [{"for": r["for"], "per_share_rub": float(r["per_share_rub"]), "record_date": r["record_date"],
                 "doc": r["doc"], "page": r["page"]} for r in csv.DictReader(f)]
    files = {n: _sha(src / n) for n in (*STOCK_FILES, INDEX_FILE)}
    files["data/market/dividends.csv"] = _sha(DIVIDENDS)
    out = {"schema": "yandex-beta-inputs-v1",
           "source": "ISS Мосбиржи: TQBR YNDX и YDEX (CLOSE дней со сделками), индекс MCFTR (CLOSE); дивиденды — "
                     "data/market/dividends.csv; первичка — выгрузки ISS вне репозитория (sha256 — files_sha256)",
           "files_sha256": files, "stock": stock, "index": index, "dividends": divs}
    return json.dumps(out, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_market")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    text = build(Path(args.src))
    if args.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("ИТОГ: ряды рынка = сборке" if same else "ФЛАГ: data/market/beta_inputs.json ≠ сборке из первички")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"ИТОГ: {OUT.relative_to(ROOT).as_posix()} собран")
    return 0


if __name__ == "__main__":
    sys.exit(main())
