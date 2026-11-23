# -*- coding: utf-8 -*-
"""Приём отчёта одной командой (README слоя, раздел 5).

    python -m common.cli report --period П [--dry-run] [--out var/report-П]

Лист периода → факты (`common.facts_sheet`, отображение `facts_mapping` переходника) → перенос книги функцией
переходника `reanchor(facts, period, out_dir)` → разложение скачка заголовка на «перекат / механика / сюрприз» →
`<out>/REPORT.md` и `<out>/report.json`. В `data/` не пишет: кандидат (книга и факты) лежит в `--out`; внутри дерева
модели `--out` — только под `var/`. Сухой прогон (`--dry-run`) листа не читает: факты = ожидаемый путь модели
`expected_path(period)`, сюрприз = 0, механика должна быть ≈ 0.

Разложение — на тех же случайных числах, что у базы, по медиане малой полосы и по точке:
- перекат = rolled − before: прежняя книга, перенесённая по своему ожидаемому пути на новый якорь и дату оценки;
- механика = after(ожидаемый путь) − rolled: перенос книги на своём же пути — инвариант «на ожидаемом пути = перекат»;
- сюрприз = after(факты листа) − after(ожидаемый путь): отличие фактов от ожидания.

Ответ переходника `reanchor`: `{"before": числа, "rolled": числа, "after": числа, "valuation_date", "anchor_before",
"candidate": {что: путь}, "notes": [строки]}`, числа — `{"median", "point"[, "printed", "draws", "seed"]}`. Перенос,
которого модель не умеет, — `NotImplementedError` с причиной (код 2, «не подключено»).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

METRICS = ("median", "point")
METRIC_TITLES = {"median": "Медиана малой полосы, ₽", "point": "Точка, ₽"}
DEFAULT_TOLERANCE = 0.5          # ₽: механика ≈ 0 (переходник может задать свой допуск REANCHOR_TOLERANCE)
NOT_CONNECTED = 2


class ReportError(Exception):
    """Отказ приёма отчёта: текст — причина."""


def tolerance(adapter: Any) -> float:
    return float(getattr(adapter, "REANCHOR_TOLERANCE", DEFAULT_TOLERANCE))


def decompose(expected: dict, actual: dict | None = None) -> dict:
    """Разложение скачка: `expected` — перенос на ожидаемом пути, `actual` — на фактах листа (нет — сухой прогон)."""
    out = {}
    for m in METRICS:
        b, r, e = (float(expected[k][m]) for k in ("before", "rolled", "after"))
        a = float(actual["after"][m]) if actual is not None else e
        out[m] = {"before": b, "rolled": r, "expected": e, "after": a, "rollover": r - b, "mechanics": e - r,
                  "surprise": a - e, "total": a - b}
    return out


def mechanics_problems(dec: dict, tol: float) -> list[str]:
    return [f"{m}: механика {dec[m]['mechanics']:+.4f} ₽ при допуске ±{tol:g} (перекат до {dec[m]['rolled']:.2f}, "
            f"перенос на ожидаемом пути {dec[m]['expected']:.2f})" for m in METRICS if abs(dec[m]["mechanics"]) > tol]


def facts_from_sheet(root: Path, adapter: Any, period: str) -> dict[str, str]:
    """Факты периода из листа `data/facts/sheets/` по отображению переходника; листы периода — `facts_sheets(period)`
    переходника (нет — лист самого периода)."""
    from common import facts_sheet as FS
    sheets_dir = root / "data" / "facts" / "sheets"
    fn = getattr(adapter, "facts_sheets", None)
    names = list(fn(period)) if callable(fn) else [period]
    return FS.to_facts([FS.load_sheet(sheets_dir, p) for p in names], adapter.facts_mapping)


def check_out(root: Path, out_dir: Path) -> None:
    """В `data/` не пишем; внутри дерева модели — только под `var/`."""
    root, out = root.resolve(), out_dir.resolve()
    if out == root or root in out.parents:
        rel = out.relative_to(root)
        if not rel.parts or rel.parts[0] != "var":
            raise ReportError(f"--out {rel.as_posix() or '.'}: внутри дерева модели кандидат пишется только под var/ "
                              "(в data/ приём отчёта не пишет)")


def _fmt(x: float, sign: bool = False) -> str:
    s = f"{x:+,.2f}" if sign else f"{x:,.2f}"
    return s.replace(",", " ").replace(".", ",")


def render(slug: str, period: str, dry_run: bool, exp: dict, act: dict | None, dec: dict, tol: float) -> str:
    bad = mechanics_problems(dec, tol)
    rows = [("До: книга и факты модели", "before", False), ("Перекат: прежняя книга по своему пути", "rollover", True),
            ("Механика: перенос на своём пути (≈ 0)", "mechanics", True),
            ("Сюрприз: факты против ожидания", "surprise", True), ("После: кандидат", "after", False),
            ("Всего: после − до", "total", True)]
    out = [f"# Приём отчёта за {period} — {slug}", "",
           "Режим: " + ("сухой прогон — факты = ожидаемый путь модели (лист не читается, сюрприз = 0)." if dry_run
                        else "факты листа `data/facts/sheets/` против ожидаемого пути модели."),
           f"Якорь до: {exp.get('anchor_before')}; дата оценки: {exp.get('valuation_date')}. Разложение — на тех же "
           "случайных числах, что у базы.", "",
           "| | " + " | ".join(METRIC_TITLES[m] for m in METRICS) + " |", "|---|" + "---:|" * len(METRICS)]
    for title, key, sign in rows:
        out.append(f"| {title} | " + " | ".join(_fmt(dec[m][key], sign) for m in METRICS) + " |")
    out += ["", ("**Механика в допуске** ±" + _fmt(tol) + " ₽: на ожидаемом пути перенос = перекат.") if not bad else
            "**МЕХАНИКА ВНЕ ДОПУСКА** — перенос книги на своём пути меняет число: " + "; ".join(bad), "",
            "## Кандидат", ""]
    for name, res in (("на ожидаемом пути", exp), ("на фактах листа", act)):
        if res is None:
            continue
        out.append(f"- {name}: " + "; ".join(f"{k} — `{v}`" for k, v in (res.get("candidate") or {}).items()))
        out += [f"  - {n}" for n in res.get("notes") or []]
    out += ["", "В `data/` ничего не записано: принять кандидата — отдельный шаг модели (книга, факты, коммит).", ""]
    return "\n".join(out)


def run(adapter: Any, root: Path, period: str, out_dir: Path, *, dry_run: bool,
        say: Callable[[str], None] = print) -> int:
    """Приём отчёта: 0 — механика в допуске, 1 — отказ или механика вне допуска, 2 — перенос не подключён."""
    slug = getattr(adapter, "SLUG", "?")
    try:
        check_out(root, out_dir)
        for name in ("expected_path", "reanchor"):
            if not callable(getattr(adapter, name, None)):
                raise NotImplementedError(f"в переходнике нет функции {name}")
        out_dir.mkdir(parents=True, exist_ok=True)
        say(f"приём отчёта за {period}: {slug}, " + ("сухой прогон (факты = ожидаемый путь)" if dry_run
                                                      else "факты листа") + f"; кандидат — {out_dir}")
        say("  шаг: ожидаемый путь модели → перенос книги")
        exp = adapter.reanchor(adapter.expected_path(period), period, str(out_dir / "expected"))
        act = None
        if not dry_run:
            say("  шаг: лист → факты → перенос книги")
            act = adapter.reanchor(facts_from_sheet(root, adapter, period), period, str(out_dir / "candidate"))
        dec = decompose(exp, act)
    except NotImplementedError as e:
        say(f"не подключено: {e}")
        return NOT_CONNECTED
    except Exception as e:  # noqa: BLE001 — любой отказ переноса — внятная строка, а не трасса
        say(f"ОТКАЗ: {type(e).__name__}: {e}")
        return 1
    tol = tolerance(adapter)
    text = render(slug, period, dry_run, exp, act, dec, tol)
    (out_dir / "REPORT.md").write_text(text, encoding="utf-8", newline="\n")
    doc = {"period": period, "model": slug, "dry_run": dry_run, "tolerance_rub": tol, "decomposition": dec,
           "expected": exp, "actual": act}
    (out_dir / "report.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                         newline="\n")
    for m in METRICS:
        d = dec[m]
        say(f"  {METRIC_TITLES[m]}: до {_fmt(d['before'])}; перекат {_fmt(d['rollover'], True)}; механика "
            f"{_fmt(d['mechanics'], True)}; сюрприз {_fmt(d['surprise'], True)}; после {_fmt(d['after'])}")
    bad = mechanics_problems(dec, tol)
    say(f"  записано: {out_dir / 'REPORT.md'}, {out_dir / 'report.json'}")
    say("ИТОГ: механика в допуске ±" + _fmt(tol) + " ₽" if not bad else "ИТОГ: МЕХАНИКА ВНЕ ДОПУСКА — " + "; ".join(bad))
    return 0 if not bad else 1


def main(argv: list[str], root: Path, adapter: Any, say: Callable[[str], None]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m common.cli report", description="приём отчёта одной командой")
    ap.add_argument("--period", required=True, help="период отчёта: 2026H2, 2027Q1, 2026")
    ap.add_argument("--dry-run", action="store_true", help="факты = ожидаемый путь модели; лист не читается")
    ap.add_argument("--out", default=None, help="каталог кандидата (по умолчанию var/report-<период>)")
    a = ap.parse_args(argv)
    if adapter is None:
        say("не подключено: нет переходника ops/common_adapter.py")
        return NOT_CONNECTED
    out = Path(a.out) if a.out else Path("var") / f"report-{a.period}"
    out = out if out.is_absolute() else root / out
    return run(adapter, root, a.period, out, dry_run=a.dry_run, say=say)
