# -*- coding: utf-8 -*-
"""Шапка выпуска — блок `header`, схема `family-header-v1` (README слоя, раздел 2; схема — `schemas/header-v1.json`).

`build(inputs)` — шапка из входов переходника (`header_inputs(payload)`): слой сам добавляет версию слоя, срок данных и
состояние по отчёту из календаря, сортирует сроки и ставит `status` по правилу слоя. `validate(header)` — список
нарушений схемы и правил (пусто — годна). Хэш выпуска модель считает после шапки, по всему выпуску;
`release.generated_at` в хэш не входит, как время сборки выпуска.

Входы `build` (словарь переходника):
- `model`, `headline`, `market` (без `upside` — его считает слой), `book`, `facts`, `worlds`, `release` — поля шапки;
- `flags` — советующие флаги модели `{name, level, title[, raised]}`: в шапку идут поднятые (`raised` не false);
- `gates` — сработавшие гейты `{key, explained, valid_until, mass}`;
- `deadlines` — сроки модели (объяснения гейтов, книга, данные, правила); срок данных по календарю слой добавит сам;
- `invariants` — нарушенные инварианты (строки; пусто — целы);
- `calendar` — календарь `family-calendar-v1` (документ или путь; нет — состояние по отчёту неизвестно, жёлтый);
- `today` — день, на который считаются сроки и состояние (ГГГГ-ММ-ДД; нет — дата `release.generated_at`);
- `common` — по желанию: версия и манифест слоя (нет — слой этого дерева).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from common import calendar as C
from common import layer as L

SCHEMA = "family-header-v1"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "header-v1.json"
SOON_DAYS = 30                       # срок ближе 30 дней — жёлтый
FIELDS = ("schema", "model", "headline", "market", "book", "facts", "worlds", "common", "release", "flags", "gates",
          "deadlines", "report", "status")
KIND_ORDER = ("explanation", "book", "data", "policy")


def _r9(x: float) -> float:
    return float(f"{x:.9g}")


def _today(inputs: dict) -> dt.date:
    t = inputs.get("today")
    if t:
        return t if isinstance(t, dt.date) else dt.date.fromisoformat(str(t))
    return dt.date.fromisoformat(str((inputs.get("release") or {}).get("generated_at", ""))[:10])


def _calendar(x: Any) -> dict | None:
    if x is None:
        return None
    return x if isinstance(x, dict) else C.load(x)


def _sort_deadlines(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda d: (d["date"], KIND_ORDER.index(d["kind"]) if d["kind"] in KIND_ORDER else 9,
                                       d["what"]))


def status(today: dt.date, *, invariants: list[str], gates: list[dict], flags: list[dict], deadlines: list[dict],
           report: dict, calendar_known: bool = True) -> dict:
    """Правило слоя. red — нарушен инвариант или истёк срок объяснения сработавшего гейта; yellow — поднят флаг
    warn/alert, срок ближе 30 дней (или прошёл), отчёт вышел, а факт не внесён, или календаря нет; иначе green."""
    red = [f"нарушен инвариант: {x}" for x in invariants]
    red += [f"истёк срок объяснения гейта {g['key']} ({g['valid_until']})" for g in gates
            if g.get("valid_until") and dt.date.fromisoformat(g["valid_until"]) < today]
    yellow = [f"флаг {f['name']} ({f['level']})" for f in flags if f["level"] in ("warn", "alert")]
    for d in deadlines:
        left = (dt.date.fromisoformat(d["date"]) - today).days
        if left < SOON_DAYS:
            yellow.append(f"срок {d['date']} ({d['kind']}): " + ("прошёл" if left < 0 else f"через {left} дн."))
    if report.get("state") == "report_out":
        yellow.append(f"отчёт вышел {report.get('out_since')}, факт не внесён")
    if not calendar_known:
        yellow.append("календаря нет: состояние по отчёту неизвестно")
    level = "red" if red else "yellow" if yellow else "green"
    return {"level": level, "reasons": red + yellow}


def build(inputs: dict) -> dict:
    """Шапка `family-header-v1` из входов переходника (см. описание модуля)."""
    today = _today(inputs)
    cal = _calendar(inputs.get("calendar"))
    facts = dict(inputs["facts"])
    headline = dict(inputs["headline"])
    market = {k: v for k, v in inputs["market"].items() if k != "upside"}
    market["upside"] = _r9(headline["printed"] / market["price"] - 1.0)
    common = inputs.get("common") or {"version": L.read_version(L.LAYER), "manifest_sha256": L.manifest_sha256(L.LAYER)}
    flags = [{"name": f["name"], "level": f["level"], "title": f["title"]} for f in inputs.get("flags") or []
             if f.get("raised", True)]
    gates = [{"key": g["key"], "explained": bool(g["explained"]), "valid_until": g.get("valid_until"),
              "mass": g.get("mass")} for g in inputs.get("gates") or []]
    deadlines = [dict(d) for d in inputs.get("deadlines") or []]
    if cal is not None:
        deadlines += C.deadlines(cal, facts["anchor_period"])
        report = C.report_state(cal, today, facts["anchor_period"])
    else:
        report = {"state": "current", "next": None, "last_fact_period": facts["anchor_period"], "out_since": None}
    deadlines = _sort_deadlines(deadlines)
    head = {"schema": SCHEMA, "model": dict(inputs["model"]), "headline": headline, "market": market,
            "book": dict(inputs["book"]), "facts": facts, "worlds": dict(inputs["worlds"]), "common": dict(common),
            "release": dict(inputs["release"]), "flags": flags, "gates": gates, "deadlines": deadlines,
            "report": report}
    head["status"] = status(today, invariants=list(inputs.get("invariants") or []), gates=gates, flags=flags,
                            deadlines=deadlines, report=report, calendar_known=cal is not None)
    return head


# ----------------------------------------------------------------------------------------------- проверка
def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(x: Any, t: str) -> bool:
    if t == "number":
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    return isinstance(x, _TYPES[t])


def _check(x: Any, s: dict, root: dict, at: str, out: list[str]) -> None:
    """Подмножество JSON Schema, которым написана схема шапки: $ref, type, const, enum, pattern, minLength,
    minimum, maximum, exclusiveMinimum, required, properties, additionalProperties, items, minItems, maxItems."""
    if "$ref" in s:
        s = root["$defs"][s["$ref"].rsplit("/", 1)[-1]]
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_is(x, t) for t in types):
            out.append(f"{at}: {x!r:.60} — ожидается {' | '.join(types)}")
            return
    if "const" in s and x != s["const"]:
        out.append(f"{at}: {x!r} ≠ {s['const']!r}")
    if "enum" in s and x not in s["enum"]:
        out.append(f"{at}: {x!r} — одно из {', '.join(map(str, s['enum']))}")
    if isinstance(x, str):
        if "pattern" in s and not re.search(s["pattern"], x):
            out.append(f"{at}: {x!r} не по образцу {s['pattern']}")
        if len(x) < s.get("minLength", 0):
            out.append(f"{at}: пустая строка")
    if _is(x, "number"):
        if "minimum" in s and x < s["minimum"] or "maximum" in s and x > s["maximum"]:
            out.append(f"{at}: {x} вне [{s.get('minimum')}; {s.get('maximum')}]")
        if "exclusiveMinimum" in s and not x > s["exclusiveMinimum"]:
            out.append(f"{at}: {x} — нужно больше {s['exclusiveMinimum']}")
    if isinstance(x, dict):
        for k in s.get("required", []):
            if k not in x:
                out.append(f"{at}: нет поля {k}")
        props = s.get("properties", {})
        extra = s.get("additionalProperties", True)
        for k, v in x.items():
            if k in props:
                _check(v, props[k], root, f"{at}.{k}", out)
            elif extra is False:
                out.append(f"{at}: лишнее поле {k}")
            elif isinstance(extra, dict):
                _check(v, extra, root, f"{at}.{k}", out)
    if isinstance(x, list):
        if len(x) < s.get("minItems", 0) or len(x) > s.get("maxItems", len(x)):
            out.append(f"{at}: элементов {len(x)}")
        if "items" in s:
            for i, v in enumerate(x):
                _check(v, s["items"], root, f"{at}[{i}]", out)


def validate(header: Any) -> list[str]:
    """Нарушения шапки: схема `schemas/header-v1.json` и правила слоя (сроки — ближний первым, upside = печать /
    цена − 1, статус = правилу слоя на дату выпуска). Пусто — годна."""
    root = _schema()
    out: list[str] = []
    _check(header, root, root, "header", out)
    if out:
        return out
    dates = [d["date"] for d in header["deadlines"]]
    if dates != sorted(dates):
        out.append("header.deadlines: не по возрастанию даты (ближний — первым)")
    up = header["headline"]["printed"] / header["market"]["price"] - 1.0
    if abs(up - header["market"]["upside"]) > 1e-8:
        out.append(f"header.market.upside = {header['market']['upside']} ≠ печать / цена − 1 = {up:.9g}")
    if header["status"]["level"] == "green" and header["status"]["reasons"]:
        out.append("header.status: зелёный с причинами")
    if header["status"]["level"] != "green" and not header["status"]["reasons"]:
        out.append(f"header.status: {header['status']['level']} без причин")
    return out


def lines(header: dict) -> list[str]:
    """Шапка для печати (`python -m common.cli state`, сборка выпуска)."""
    h, m, f, r, st = header["headline"], header["market"], header["facts"], header["report"], header["status"]
    nxt = r.get("next") or {}
    out = [f"шапка {header['schema']}: {header['model']['name']} ({', '.join(header['model']['tickers'])}), "
           f"статус {st['level']}" + (f" — {'; '.join(st['reasons'])}" if st["reasons"] else ""),
           f"  заголовок {h['printed']:g} ₽ (медиана {h['median']:g}, точка {h['point']:g}; P10–P90 "
           f"{h['band80'][0]:g}–{h['band80'][1]:g}, P25–P75 {h['band50'][0]:g}–{h['band50'][1]:g}; шаг "
           f"{h['print_step']:g}; P(ниже рынка) {h['p_below_market']:.1%}); рынок {m['ticker']} {m['price']:g} ₽ на "
           f"{m['price_date']}, потенциал {m['upside']:+.1%}",
           f"  книга {header['book']['version']} от {header['book']['date']}; якорь {f['anchor_period']} (факты на "
           f"{f['facts_date']}), открыт {f['open_period']}, закрыто {len(f['periods_closed'])}; миры "
           f"{header['worlds']['version']} на {header['worlds']['record_asof']}; слой {header['common']['version']}; "
           f"выпуск {header['release']['valuation_date']}" + (" (быстрая сборка)" if header["release"]["fast"] else ""),
           f"  отчёт: {r['state']}" + (f" с {r['out_since']}" if r.get("out_since") else "")
           + (f"; следующий {nxt.get('date')} ({nxt.get('precision')}"
              + (", подтверждён" if nxt.get("confirmed") else ", не подтверждён") + f") — {nxt.get('title')}"
              if nxt else "; следующего в календаре нет"),
           "  флаги: " + (", ".join(f"{x['name']} ({x['level']})" for x in header["flags"]) or "нет")
           + "; гейты сработали: " + (", ".join(g["key"] for g in header["gates"]) or "нет")]
    for d in header["deadlines"]:
        out.append(f"  срок {d['date']} [{d['kind']}]: {d['what']} → {d['action']}")
    return out
