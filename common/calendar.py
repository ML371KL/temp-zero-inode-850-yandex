# -*- coding: utf-8 -*-
"""Календарь эмитента — `data/calendar.json`, схема `family-calendar-v1` (README слоя, раздел 3).

    python -m common.calendar [data/calendar.json]          # проверить календарь модели (код 1 — есть расхождения)

`load(path)` — чтение и проверка (отказ — `CalendarError` со всеми расхождениями разом); `check(doc)` — список
расхождений; `next_fact(cal, today)` — ближнее событие с фактами; `closing_report(cal, period)` — отчёт, закрывающий
период; `report_state(cal, today, anchor_period)` — поле `header.report`; `deadlines(cal, anchor_period)` — срок
данных для `header.deadlines`. «Сегодня» — только параметром: модуль часов не читает.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "family-calendar-v1"
KINDS = ("report", "operating", "guidance", "dividend", "agm", "strategy", "cbr", "other")
PRECISIONS = ("day", "window", "month", "estimate")
FACT_KINDS = ("report", "operating")            # события, которые приносят факты (`next_fact`)
REQUIRED = ("id", "kind", "date", "precision", "confirmed", "title", "src")
OPTIONAL = ("earliest", "latest", "covers", "gives", "note")
TOP_KEYS = ("schema", "issuer", "as_of", "events")
PERIOD_RE = re.compile(r"^\d{4}(H[12]|Q[1-4]|M(0[1-9]|1[0-2]))?$")   # 2026, 2026H2, 2026Q4, 2026M09
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPORT_STATES = ("current", "report_out", "closing_due", "overdue")


class CalendarError(ValueError):
    """Календарь не проходит проверку: `problems` — все расхождения."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ----------------------------------------------------------------------------------------------- периоды
def _day(x: Any) -> dt.date | None:
    if not isinstance(x, str) or not DAY_RE.match(x):
        return None
    try:
        return dt.date.fromisoformat(x)
    except ValueError:
        return None


def _as_day(x: dt.date | str) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(x)


def period_end(p: str) -> dt.date:
    """Последний день периода: год, полугодие, квартал, месяц."""
    y = int(p[:4])
    tail = p[4:]
    month = 12 if not tail else {"H": 6, "Q": 3, "M": 1}[tail[0]] * int(tail[1:])
    nxt = dt.date(y + (month == 12), month % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def next_period(p: str) -> str:
    """Следующий период той же длины: 2026H1 → 2026H2, 2026H2 → 2027H1, 2026Q4 → 2027Q1, 2026 → 2027."""
    y, tail = int(p[:4]), p[4:]
    if not tail:
        return str(y + 1)
    kind, n = tail[0], int(tail[1:])
    size = {"H": 2, "Q": 4, "M": 12}[kind]
    if n == size:
        return f"{y + 1}{kind}{1:0{len(tail) - 1}d}"
    return f"{y}{kind}{n + 1:0{len(tail) - 1}d}"


# ----------------------------------------------------------------------------------------------- проверка
def check(doc: Any) -> list[str]:
    """Расхождения календаря со схемой `family-calendar-v1` (пусто — годен)."""
    if not isinstance(doc, dict):
        return ["календарь — не объект"]
    out = []
    if doc.get("schema") != SCHEMA:
        out.append(f"schema = {doc.get('schema')!r}, ожидается {SCHEMA}")
    extra = sorted(set(doc) - set(TOP_KEYS))
    if extra:
        out.append(f"лишние поля верхнего уровня: {', '.join(extra)}")
    issuer = doc.get("issuer")
    if not isinstance(issuer, str) or not SLUG_RE.match(issuer):
        out.append(f"issuer = {issuer!r} — нужно имя модели в реестре (строчные латинские, цифры, дефис)")
    if _day(doc.get("as_of")) is None:
        out.append(f"as_of = {doc.get('as_of')!r} — нужна дата ГГГГ-ММ-ДД")
    events = doc.get("events")
    if not isinstance(events, list):
        return out + ["events — не список"]
    seen: set[str] = set()
    for i, e in enumerate(events):
        out += [f"events[{i}]{' ' + repr(e.get('id')) if isinstance(e, dict) else ''}: {p}"
                for p in _check_event(e, issuer if isinstance(issuer, str) else "")]
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            if e["id"] in seen:
                out.append(f"events[{i}]: id {e['id']!r} повторяется")
            seen.add(e["id"])
    return out


def _check_event(e: Any, issuer: str) -> list[str]:
    if not isinstance(e, dict):
        return ["событие — не объект"]
    out = []
    missing = [k for k in REQUIRED if k not in e]
    if missing:
        out.append(f"нет полей {', '.join(missing)}")
    extra = sorted(set(e) - set(REQUIRED) - set(OPTIONAL))
    if extra:
        out.append(f"лишние поля {', '.join(extra)}")
    kind, prec, covers = e.get("kind"), e.get("precision"), e.get("covers")
    if kind not in KINDS:
        out.append(f"kind = {kind!r} — одно из {', '.join(KINDS)}")
    if prec not in PRECISIONS:
        out.append(f"precision = {prec!r} — одно из {', '.join(PRECISIONS)}")
    day = _day(e.get("date"))
    if day is None:
        out.append(f"date = {e.get('date')!r} — нужна дата ГГГГ-ММ-ДД")
    if not isinstance(e.get("confirmed"), bool):
        out.append("confirmed — true или false")
    elif e["confirmed"] and prec != "day":
        out.append("подтверждённая дата — precision day")
    for k in ("title", "src"):
        if not isinstance(e.get(k), str) or not e[k].strip():
            out.append(f"{k} пуст — у каждого события {'источник' if k == 'src' else 'заголовок'}")
    if e.get("note") is not None and not isinstance(e.get("note"), str):
        out.append("note — строка или null")
    if covers is not None and (not isinstance(covers, str) or not PERIOD_RE.match(covers)):
        out.append(f"covers = {covers!r} — период ГГГГ, ГГГГH1, ГГГГQ4, ГГГГM09 или null")
    if kind == "report" and covers is None:
        out.append("закрывающий отчёт (kind report) без covers — какой период он закрывает")
    gives = e.get("gives", [])
    if not isinstance(gives, list) or not all(isinstance(g, str) and g for g in gives):
        out.append("gives — список непустых строк")
    lo, hi = e.get("earliest"), e.get("latest")
    dlo, dhi = _day(lo), _day(hi)
    if lo is not None and dlo is None or hi is not None and dhi is None:
        out.append("earliest / latest — дата ГГГГ-ММ-ДД или null")
    if prec in ("window", "estimate") and (dlo is None or dhi is None):
        out.append(f"precision {prec}: нужны earliest и latest — окно даты")
    if prec == "day" and any(d is not None and d != day for d in (dlo, dhi)):
        out.append("precision day: earliest и latest — null или та же дата")
    if day is not None and (dlo is not None and dlo > day or dhi is not None and dhi < day):
        out.append("date вне окна earliest–latest")
    if isinstance(e.get("id"), str) and kind in KINDS and issuer:
        tag = covers if isinstance(covers, str) else (e.get("date") or "")
        if not re.match(rf"^{re.escape(issuer)}\.{kind}\.{re.escape(tag)}(\.[a-z0-9-]+)?$", e["id"]):
            out.append(f"id {e['id']!r} — нужно «{issuer}.{kind}.{tag}[.уточнение]» (<модель>.<вид>.<период или дата>)")
    elif not isinstance(e.get("id"), str):
        out.append("id — строка")
    return out


def load(path: Path | str) -> dict:
    """Календарь из файла; не проходит проверку — `CalendarError`."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CalendarError([f"{p.name}: не читается: {e}"]) from e
    bad = check(doc)
    if bad:
        raise CalendarError(bad)
    return doc


# ----------------------------------------------------------------------------------------------- запросы
def _events(cal: dict) -> list[dict]:
    return sorted(cal.get("events") or [], key=lambda e: (e["date"], e["id"]))


def _last_day(e: dict) -> dt.date:
    """Последний день, когда событие ещё может случиться: дата, а у неподтверждённой — конец окна."""
    d = dt.date.fromisoformat(e["date"])
    if not e.get("confirmed") and e.get("latest"):
        d = max(d, dt.date.fromisoformat(e["latest"]))
    return d


def _short(e: dict | None) -> dict | None:
    if e is None:
        return None
    return {k: e.get(k) for k in ("date", "title", "covers", "precision", "confirmed")}


def next_fact(cal: dict, today: dt.date | str) -> dict | None:
    """Ближнее событие, которое приносит факты (`report`, `operating`), ещё не прошедшее на `today`."""
    t = _as_day(today)
    return next((e for e in _events(cal) if e["kind"] in FACT_KINDS and _last_day(e) >= t), None)


def closing_report(cal: dict, period: str) -> dict | None:
    """Отчёт, закрывающий период: `kind == "report"` и `covers == period` (первый по дате)."""
    return next((e for e in _events(cal) if e["kind"] == "report" and e.get("covers") == period), None)


def report_state(cal: dict, today: dt.date | str, anchor_period: str) -> dict:
    """Поле `header.report`: ждём ли отчёт, закрывающий период после якоря.

    `current` — период после якоря ещё идёт; `closing_due` — период кончился, отчёт ещё впереди (или даты нет);
    `report_out` — подтверждённая дата отчёта наступила, а якорь прежний: факт не внесён; `overdue` — окно
    неподтверждённой даты прошло, отчёта нет (календарь пора сверить с эмитентом)."""
    t = _as_day(today)
    period = next_period(anchor_period)
    closing = closing_report(cal, period)
    out_since = None
    if t <= period_end(period):
        state = "current"
    elif closing is None:
        state = "closing_due"
    elif closing.get("confirmed") and dt.date.fromisoformat(closing["date"]) <= t:
        state, out_since = "report_out", closing["date"]
    elif _last_day(closing) < t:
        state = "overdue"
    else:
        state = "closing_due"
    return {"state": state, "next": _short(next_fact(cal, t)), "last_fact_period": anchor_period,
            "out_since": out_since}


def deadlines(cal: dict, anchor_period: str) -> list[dict]:
    """Срок данных: закрывающий отчёт периода после якоря — после него факты модели устарели."""
    period = next_period(anchor_period)
    e = closing_report(cal, period)
    if e is None:
        return []
    when = e["date"] if e.get("confirmed") else (f"{e['date']} (окно {e.get('earliest')} – {e.get('latest')}, "
                                                f"{e['precision']})")
    return [{"date": e["date"], "kind": "data",
             "what": f"закрывающий отчёт за {period} — {e['title']}, {when}: после него факты якоря {anchor_period} "
                     "устарели",
             "action": f"принять отчёт: python -m common.cli report --period {period}"}]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else Path("data/calendar.json")
    try:
        cal = load(path)
    except CalendarError as e:
        print(f"{path}: расхождений {len(e.problems)}")
        for p in e.problems:
            print(f"  - {p}")
        return 1
    print(f"{path}: {SCHEMA}, {cal['issuer']}, на {cal['as_of']}, событий {len(cal['events'])} — годен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
