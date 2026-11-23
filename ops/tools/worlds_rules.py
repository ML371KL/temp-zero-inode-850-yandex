# -*- coding: utf-8 -*-
"""Правила книги Яндекса для чисел, зависящих от миров ставок (шаги `rule` манифеста
`data/assumptions/worlds_dependents.yaml`, команда `ops/tools/adopt_worlds.py`).

Правило — функция `f(ctx) -> {"file", "values", "decimals"}` (или список таких); `ctx` — контекст движка
(`ctx.record` — запись миров дерева, `ctx.entry` — запись индекса источника или None, `ctx.root`, `ctx.yaml(rel)`).
Сторож `tests/test_worlds_dependents.py` (через `adopt_worlds.check`) сверяет числа правил с книгой.

* `project_worlds` — миры книги: проекция записи миров в поля схемы Яндекса (`model/book_schema.py::_WORLD`) той же
  математикой, что у X5 (`ops/tools/worlds_rules.py` образца 9ec4506): ряды записи / 100, 5 знаков; ключевая —
  `key_avg`, ИПЦ — `cpi_yoy_avg`; узлы кривой — `fair_zero_curve_today`, уровень в терминале — `curve_lt`, π в долгую —
  `lt_inflation`. Продовольственный ИПЦ модель не читает (методика §3). Веса слоя «свой макро-взгляд» —
  `probabilities` записи (`joint.world_prob`). Происхождение: `worlds.source.version`, `sha256` записи, `record_asof`,
  `curve_date` = `meta.curve_as_of`, `book` — книга-источник записи (`origin` индекса).
"""
from __future__ import annotations

import hashlib

BOOK = "data/assumptions/assumptions.yaml"
RECORD = "data/assumptions/worlds_source.json"
WORLDS = ("N", "H", "M")
SERIES = {"key_rate": "key_avg", "cpi": "cpi_yoy_avg"}
ND = 5


def projection(record: dict) -> dict:
    """Миры записи в полях схемы Яндекса (без `name`: имена миров — суждение книги)."""
    if not record.get("curve_lt") or not record.get("lt_inflation"):
        raise ValueError("запись без блоков curve_lt / lt_inflation — уровень терминала и π в долгую не по правилу")
    out = {}
    for w in WORLDS:
        rows = [r for r in record["rows"] if r["world"] == w]
        d = {f: {r["period"]: round(r[src] * 0.01, ND) for r in rows} for f, src in SERIES.items()}
        fair = record["fair_zero_curve_today"][w]
        curve = {str(k): round(v / 100, ND) for k, v in sorted(fair.items(), key=lambda kv: float(kv[0]))}
        curve["LT"] = round(float(record["curve_lt"][w]) / 100.0, ND)
        d["zero_curve"] = curve
        d["lt"] = {"inflation": round(float(record["lt_inflation"][w]) / 100.0, ND)}
        out[w] = d
    return out


def curve_date(record: dict) -> str:
    return str((record.get("m_inflation") or {}).get("curve_date") or record["asof"])


def values_of(record: dict, record_bytes: bytes, entry: dict | None, state: dict | None = None) -> dict:
    """Адреса книги → числа и строки правила (общая часть правила и генератора книги)."""
    P = projection(record)
    values: dict = {}
    for w in WORLDS:
        for f in SERIES:
            for period, x in P[w][f].items():
                values[f"worlds.{w}.{f}.{period}"] = x
        for node, x in P[w]["zero_curve"].items():
            values[f"worlds.{w}.zero_curve.{node}"] = x
        values[f"worlds.{w}.lt.inflation"] = P[w]["lt"]["inflation"]
        values[f"joint.world_prob.{w}"] = round(float(record["probabilities"][w]), ND)
    day = curve_date(record)
    values.update({"worlds.source.sha256": hashlib.sha256(record_bytes).hexdigest(),
                   "worlds.source.record_asof": str(record["asof"]),
                   "worlds.source.curve_date": day, "meta.curve_as_of": day})
    if entry:
        values["worlds.source.book"] = entry["origin"]
        values["worlds.source.version"] = str(entry["version"])
    elif state:
        values["worlds.source.book"] = state["origin"]
        values["worlds.source.version"] = str(state["version"])
    return values


def project_worlds(ctx) -> list[dict]:
    raw = (ctx.root / RECORD).read_bytes()
    state = ctx.yaml("data/assumptions/worlds_state.json")
    values = values_of(ctx.record, raw, ctx.entry, state)
    decimals = {k: ND for k, v in values.items() if isinstance(v, float)}
    return [{"file": BOOK, "values": values, "decimals": decimals}]
