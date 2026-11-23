# -*- coding: utf-8 -*-
"""Лист фактов модели: реестр документов и листы периодов → файлы фактов модели.

Каталог листа — `data/facts/sheets/` модели:
  documents.csv   `doc_id, title, issuer_url, published, sha256` — реестр документов (самих документов в репозитории
                  нет): `published` — дата ГГГГ-ММ-ДД, `sha256` — полный хэш файла документа (64 знака), адрес — http(s);
  <период>.csv    `metric, segment, period, value, unit, doc_id, page, note` — каждое число со ссылкой на документ и
                  страницу (для xlsx — лист и ячейка); `segment` может быть пустым (число группы); `value` — число с
                  точкой; единица — из UNITS; период строки — в формате PERIOD_RE (лист периода несёт и ряды прошлых
                  периодов); ключ строки (`metric`, `segment`, `period`) в листе один раз.

Файлы — UTF-8 (без BOM), разделитель — запятая, первая строка — ровно эти столбцы.

Сборка фактов: `to_facts(sheet, mapping)` → {имя файла: текст JSON}. Отображение (`facts_mapping` переходника) —
{имя файла фактов: дерево}; в дереве лист `Fact(metric, segment, period)` заменяется числом листа, `Ref(...)` —
ссылкой {doc_id, page}, функция `f(get)` — своим результатом (`get(metric, segment, period)` — число листа);
остальное идёт как есть. Тест модели «факты = сборка из листа» — `check_built(sheets_dir, mapping, facts_dir)`.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

DOC_COLUMNS = ("doc_id", "title", "issuer_url", "published", "sha256")
ROW_COLUMNS = ("metric", "segment", "period", "value", "unit", "doc_id", "page", "note")
UNITS = ("bn_rub", "mln_shares", "pct", "rub", "count")
PERIOD_RE = re.compile(r"^\d{4}(H[12]|Q[1-4])?$")         # год 2026, полугодие 2026H1, квартал 2026Q3
DOCUMENTS = "documents.csv"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


class SheetError(ValueError):
    """Лист не прошёл проверку: `problems` — все найденные расхождения."""

    def __init__(self, problems: list[str]):
        super().__init__("лист фактов:\n  " + "\n  ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    issuer_url: str
    published: str
    sha256: str


@dataclass(frozen=True)
class Row:
    metric: str
    segment: str
    period: str
    value: Decimal
    text: str                    # число как записано в листе
    unit: str
    doc_id: str
    page: str
    note: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.metric, self.segment, self.period)


@dataclass
class Sheet:
    period: str                              # период листа (имя файла); для набора листов — через «+»
    rows: list[Row]
    documents: dict[str, Document] = field(default_factory=dict)

    def index(self) -> dict[tuple[str, str, str], Row]:
        return {r.key: r for r in self.rows}

    def get(self, metric: str, segment: str = "", period: str | None = None) -> Decimal:
        return self.row(metric, segment, period).value

    def row(self, metric: str, segment: str = "", period: str | None = None) -> Row:
        key = (metric, segment, period or self.period)
        r = self.index().get(key)
        if r is None:
            raise KeyError(f"в листе {self.period} нет строки metric={metric!r} segment={segment!r} period={key[2]!r}")
        return r


# ----------------------------------------------------------------------------------------------- чтение
def _read_csv(path: Path, columns: tuple[str, ...], problems: list[str]) -> list[dict[str, str]]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append(f"{path.name}: файл с BOM — нужен UTF-8 без BOM")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        problems.append(f"{path.name}: не UTF-8 ({e})")
        return []
    rd = csv.reader(io.StringIO(text, newline=""))
    rows = list(rd)
    if not rows or tuple(rows[0]) != columns:
        problems.append(f"{path.name}: первая строка {rows[0] if rows else '—'} ≠ столбцам {list(columns)}")
        return []
    out = []
    for n, r in enumerate(rows[1:], 2):
        if not r:
            continue
        if len(r) != len(columns):
            problems.append(f"{path.name}:{n}: {len(r)} полей вместо {len(columns)}")
            continue
        out.append({"_line": str(n), **dict(zip(columns, r))})
    return out


def check_documents(path: Path) -> tuple[dict[str, Document], list[str]]:
    problems: list[str] = []
    docs: dict[str, Document] = {}
    for r in _read_csv(path, DOC_COLUMNS, problems):
        at = f"{path.name}:{r['_line']}"
        d = Document(**{k: r[k].strip() for k in DOC_COLUMNS})
        if not ID_RE.match(d.doc_id):
            problems.append(f"{at}: doc_id {d.doc_id!r} — латиница, цифры, «_.-»")
        if d.doc_id in docs:
            problems.append(f"{at}: doc_id {d.doc_id} повторяется")
        if not d.title:
            problems.append(f"{at}: нет названия документа")
        if not re.match(r"^https?://\S+$", d.issuer_url):
            problems.append(f"{at}: адрес {d.issuer_url!r} — не http(s)")
        try:
            date.fromisoformat(d.published)
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", d.published):
                raise ValueError
        except ValueError:
            problems.append(f"{at}: дата {d.published!r} — не ГГГГ-ММ-ДД")
        if not SHA_RE.match(d.sha256):
            problems.append(f"{at}: sha256 {d.sha256[:16]!r}… — не 64 знака 0-9a-f")
        docs[d.doc_id] = d
    return docs, problems


def check_sheet(path: Path, documents: dict[str, Document]) -> tuple[Sheet, list[str]]:
    problems: list[str] = []
    period = path.stem
    if not PERIOD_RE.match(period):
        problems.append(f"{path.name}: имя листа — не период ({PERIOD_RE.pattern})")
    rows: list[Row] = []
    seen: dict[tuple[str, str, str], str] = {}
    for r in _read_csv(path, ROW_COLUMNS, problems):
        at = f"{path.name}:{r['_line']}"
        v = {k: r[k].strip() for k in ROW_COLUMNS}
        if not v["metric"]:
            problems.append(f"{at}: нет metric")
        if not PERIOD_RE.match(v["period"]):
            problems.append(f"{at}: период {v['period']!r} не в формате")
        if v["unit"] not in UNITS:
            problems.append(f"{at}: единица {v['unit']!r} не из {list(UNITS)}")
        if v["doc_id"] not in documents:
            problems.append(f"{at}: документа {v['doc_id']!r} нет в {DOCUMENTS}")
        if not v["page"]:
            problems.append(f"{at}: нет страницы (лист и ячейки для xlsx)")
        value = None
        if NUM_RE.match(v["value"]):
            try:
                value = Decimal(v["value"])
            except InvalidOperation:
                pass
        if value is None:
            problems.append(f"{at}: значение {v['value']!r} — не число с точкой")
            value = Decimal(0)
        key = (v["metric"], v["segment"], v["period"])
        if key in seen:
            problems.append(f"{at}: строка {key} повторяет строку {seen[key]}")
        seen[key] = r["_line"]
        rows.append(Row(v["metric"], v["segment"], v["period"], value, v["value"], v["unit"], v["doc_id"],
                        v["page"], v["note"]))
    return Sheet(period, rows, documents), problems


def load_documents(sheets_dir: Path) -> dict[str, Document]:
    docs, problems = check_documents(Path(sheets_dir) / DOCUMENTS)
    if problems:
        raise SheetError(problems)
    return docs


def load_sheet(sheets_dir: Path, period: str) -> Sheet:
    """Лист периода с проверкой (и проверкой реестра документов); расхождения — SheetError со всеми сразу."""
    sheets_dir = Path(sheets_dir)
    docs, problems = check_documents(sheets_dir / DOCUMENTS)
    sheet, more = check_sheet(sheets_dir / f"{period}.csv", docs)
    if problems + more:
        raise SheetError(problems + more)
    return sheet


def sheet_periods(sheets_dir: Path) -> list[str]:
    return sorted(p.stem for p in Path(sheets_dir).glob("*.csv") if p.name != DOCUMENTS)


def check_dir(sheets_dir: Path) -> list[str]:
    """Все расхождения каталога листа (реестр и все листы периодов); пусто — лист в порядке."""
    sheets_dir = Path(sheets_dir)
    docs, problems = check_documents(sheets_dir / DOCUMENTS)
    for period in sheet_periods(sheets_dir):
        problems += check_sheet(sheets_dir / f"{period}.csv", docs)[1]
    return problems


def merge(sheets: Iterable[Sheet]) -> Sheet:
    """Несколько листов — один для сборки; одна и та же строка в двух листах — отказ."""
    sheets = list(sheets)
    rows, seen, problems = [], {}, []
    docs: dict[str, Document] = {}
    for s in sheets:
        docs.update(s.documents)
        for r in s.rows:
            if r.key in seen:
                problems.append(f"строка {r.key} есть в листах {seen[r.key]} и {s.period}")
            seen[r.key] = s.period
            rows.append(r)
    if problems:
        raise SheetError(problems)
    return Sheet("+".join(s.period for s in sheets), rows, docs)


# ----------------------------------------------------------------------------------------------- сборка фактов
@dataclass(frozen=True)
class Fact:
    """Число листа в файле фактов; `period=None` — период листа."""
    metric: str
    segment: str = ""
    period: str | None = None


@dataclass(frozen=True)
class Ref:
    """Ссылка числа листа на документ: {doc_id, page}."""
    metric: str
    segment: str = ""
    period: str | None = None


def _number(row: Row) -> int | float:
    return int(row.value) if "." not in row.text else float(row.text)


def _build(node: Any, sheet: Sheet, idx: dict, at: str) -> Any:
    def row(metric: str, segment: str = "", period: str | None = None) -> Row:
        key = (metric, segment, period or sheet.period)
        if key not in idx:
            raise SheetError([f"{at}: в листе {sheet.period} нет строки {key}"])
        return idx[key]

    if isinstance(node, Fact):
        return _number(row(node.metric, node.segment, node.period))
    if isinstance(node, Ref):
        r = row(node.metric, node.segment, node.period)
        return {"doc_id": r.doc_id, "page": r.page}
    if isinstance(node, dict):
        return {k: _build(v, sheet, idx, f"{at}.{k}") for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_build(v, sheet, idx, f"{at}[{i}]") for i, v in enumerate(node)]
    if callable(node):
        return node(lambda metric, segment="", period=None: row(metric, segment, period).value)
    return node


def dumps(doc: Any) -> str:
    """Запись файла фактов: JSON UTF-8, отступ 1, ключи в порядке отображения, LF в конце."""
    def default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() and "." not in str(o) else float(o)
        raise TypeError(f"не JSON: {type(o).__name__}")
    return json.dumps(doc, ensure_ascii=False, indent=1, default=default) + "\n"


def to_facts(sheet: Sheet | Iterable[Sheet], mapping: dict[str, Any]) -> dict[str, str]:
    """Файлы фактов модели из листа по отображению переходника: {имя файла: текст JSON}."""
    if not isinstance(sheet, Sheet):
        sheet = merge(sheet)
    idx = sheet.index()
    return {name: dumps(_build(tree, sheet, idx, name)) for name, tree in mapping.items()}


def write_facts(texts: dict[str, str], facts_dir: Path) -> list[Path]:
    out = []
    for name, text in texts.items():
        p = Path(facts_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        out.append(p)
    return out


def check_built(sheet: Sheet | Iterable[Sheet], mapping: dict[str, Any], facts_dir: Path) -> list[str]:
    """Файлы фактов модели = сборке из листа (байт в байт): расхождения по файлам."""
    bad = []
    for name, text in to_facts(sheet, mapping).items():
        p = Path(facts_dir) / name
        if not p.exists():
            bad.append(f"{name}: файла фактов нет")
        elif p.read_bytes() != text.encode("utf-8"):
            bad.append(f"{name}: не равен сборке из листа")
    return bad

