"""Факты Яндекса: загрузка `data/facts/*.json` (сборка листа фактов, `ops/common_adapter.py::facts_mapping`).

Значение факта — узел `{"v": …, "src": …}` или `{"v": …, "calc": …}`: ядро читает `v`. `src` — ссылка на документ
(`{"doc_id", "page"}`) или текст; узел без `src` и без `calc` — отказ `FactsError` (число без источника не считается).
Нераскрытое — `null` → `None`, не 0: ядро требует значение там, где его читает, и на `None` отказывает с путём.

`core_facts(F)` — факты, которые читают перевывод книги (§17) и проход клетки (§4–7): ряды блоков и группы, баланс
якоря, акции, реестр дивидендов. Каждое прочитанное число — узел с источником и конечным `v`.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model.book import ROOT

FACTS_DIR = ROOT / "data" / "facts"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "facts"
CORE_FILES = ("segments", "group", "balance", "macro", "equity")
SERIES_BLOCKS = ("A", "B", "C", "D", "E", "F", "F_at", "F_o", "G", "X", "group")


class FactsError(ValueError):
    """Факты не читаются однозначно — считать по ним нельзя."""


class FactsFallbackWarning(UserWarning):
    """Фактов `data/facts` нет — читается фикстура тестов (имя держит фильтр предупреждений `pytest.ini`)."""


def default_facts_dir() -> Path:
    return FACTS_DIR


def _has_text(x: Any) -> bool:
    return isinstance(x, str) and any(ch.isalnum() for ch in x)


def has_source(node: Any) -> bool:
    """У узла `{"v": …}` есть источник: `calc` — текст; `src` — текст или ссылка `{"doc_id": …}` с непустым
    `doc_id` (пробел, «?», «—» источником не считаются)."""
    if not isinstance(node, dict):
        return False
    src = node.get("src")
    return (_has_text(node.get("calc")) or _has_text(src)
            or (isinstance(src, dict) and _has_text(src.get("doc_id"))))


def unwrap(node: Any, where: str) -> Any:
    """Раскрывает узлы `{"v": …}` в значения; число без `src` и `calc` — отказ."""
    if isinstance(node, dict):
        if "v" in node:
            if node["v"] is not None and not has_source(node):
                raise FactsError(f"факты: {where} — значение без src и без calc")
            return unwrap(node["v"], f"{where}.v")
        return {k: unwrap(v, f"{where}.{k}") for k, v in node.items()}
    if isinstance(node, list):
        return [unwrap(v, f"{where}[{i}]") for i, v in enumerate(node)]
    return node


@dataclass(frozen=True)
class Facts:
    """Факты как есть (`raw`) и с раскрытыми значениями (`data`), по имени файла."""

    root: Path
    fixture: bool
    raw: dict = field(repr=False)
    data: dict = field(repr=False)

    def get(self, path: str) -> Any:
        """Значение по пути "файл.ключ.ключ"; нет пути — `FactsError`."""
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise FactsError(f"факты: нет {path} ({self.root.name})")
            node = node[part]
        return node


def load_facts(path: Path | str | None = None) -> Facts:
    """Читает все `*.json` каталога фактов; нет файла ядра — `FactsError`."""
    root = Path(path) if path is not None else default_facts_dir()
    raw, data = {}, {}
    for file in sorted(root.glob("*.json")):
        try:
            raw[file.stem] = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FactsError(f"факты: {file.name} — не JSON ({exc})") from None
        data[file.stem] = unwrap(raw[file.stem], file.stem)
    missing = [name for name in CORE_FILES if name not in data]
    if missing:
        raise FactsError(f"факты: в {root} нет {', '.join(n + '.json' for n in missing)}")
    return Facts(root=root, fixture=root.resolve() == FIXTURE_DIR.resolve(), raw=raw, data=data)


# ------------------------------------------------------------- что читают перевывод и ядро

_PATH_PART = re.compile(r"[^.\[\]]+")


def _raw_node(F: Facts, path: str) -> Any:
    node: Any = F.raw
    for part in _PATH_PART.findall(path):
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def need(F: Facts, path: str, bare: bool = False) -> float:
    """Число факта по пути "файл.ключ…" — узел `{"v": число, "src"|"calc"}`; иначе `FactsError` с путём.

    `bare` — суммы сборки листа без своего узла (`segments.blocks`, `segments.gtv`: блок = сумма сегментов, полугодие
    = сумма кварталов; состав — `segments.composition`, сверка со сборкой — тест листа фактов)."""
    node = _raw_node(F, path)
    if node is None:
        raise FactsError(f"факты: нет {path}")
    if bare and not isinstance(node, dict):
        node = {"v": node, "calc": "сумма сборки листа"}
    if not (isinstance(node, dict) and "v" in node and has_source(node)):
        raise FactsError(f"факты: {path} — число без узла {{v, src|calc}}")
    value = node["v"]
    if value is None:
        raise FactsError(f"факты: {path} не раскрыт (null) — ядро не считает его нулём")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FactsError(f"факты: {path} = {value!r} — ожидается конечное число")
    return float(value)


@dataclass(frozen=True)
class DeclaredDividend:
    label: str
    amount: float           # млрд ₽
    record_date: dt.date    # отсечка: первый день без права на дивиденд (§7.1)
    paid: str               # период выплаты (квартал или полугодие)
    paid_amount: float | None = None   # выплата по ОДДС, млрд ₽ (`paid_bn_rub`), если есть (§7.1, книга 1.2)


@dataclass(frozen=True)
class CoreFacts:
    """Факты перевывода и клетки — проверенные, без `None`. Деньги — млрд ₽, акции — млн шт., ставки — доли."""

    F: Facts = field(repr=False)
    anchor: str
    facts_date: str
    rev: dict               # блок → {период → выручка}: A–G, F_at, F_o, X, group; кварталы, полугодия, годы
    ebitda: dict            # блок → {период → скорр. EBITDA}
    gtv_B: dict             # период → GTV Райдтеха
    gtv_C: dict             # период → GTV электронной коммерции и доставки
    sbc: dict               # период → выплаты акциями
    capex: dict             # период → капвложения в ОС и НМА (ОДДС)
    lease_add: dict         # полугодие → поступления права пользования машин, земли и прочего; `ltm` — 12 мес.
    da: dict                # период → амортизация без права пользования офисами
    tax_rate_diff: dict     # полугодие → строка «разница в ставках» сверки налога МСФО
    interest_expense: dict  # период → процентные расходы
    office_lease_interest: dict
    interest_income: dict
    cpi: dict               # период → ИПЦ г/г факта, доля
    key_rate_avg: dict      # период → средняя ключевая ставка, доля
    ad_market: dict         # {"akar_2026Q1", "arir_2026H1"} → рост интернет-рекламы г/г, доля
    nd_nonbank: dict        # дата → чистый долг без банка (§7.1)
    nd_company: float       # скорр. чистый долг компании на якорь
    bank_money: float       # деньги банка на якорь (§7.1)
    bank_capital: dict      # дата → капитал банка (ф.123)
    nwc: dict               # дата → оборотный капитал без банка
    fa_anchor: float        # ОС + НМА без гудвила + право пользования машин, земли и прочего на якорь
    bridge: dict            # имя строки баланса → сумма на якорь (активы моста, §7.1)
    shares_mln: float       # акции в обращении на якорь
    options_vested_mln: float  # заработанные неисполненные опционы на якорь
    dividends: tuple        # DeclaredDividend — объявленные после якоря с отсечкой
    balance_dates: dict = field(default_factory=dict)
    composition: tuple = ()  # книга 1.4 (§0.5): эффект смены внутригрупповых расчётов, приложенный к фактам


_ANCHOR_DATE = {"H1": "-06-30", "H2": "-12-31"}


def _series(F: Facts, base: str, keys, bare: bool = False) -> dict:
    return {k: need(F, f"{base}.{k}", bare) for k in keys}


def core_facts(F: Facts, anchor: str = "2026H1") -> CoreFacts:
    """Факты, которые читают перевывод (§17) и клетка (§4–7), — каждое число с источником."""
    seg = F.raw["segments"]["blocks"]
    rev = {b: _series(F, f"segments.blocks.{b}.revenue", seg[b]["revenue"], True) for b in SERIES_BLOCKS}
    ebitda = {b: _series(F, f"segments.blocks.{b}.adj_ebitda", seg[b]["adj_ebitda"], True) for b in SERIES_BLOCKS}
    gtv = F.raw["segments"]["gtv"]
    g = F.raw["group"]
    lease_add = _series(F, "group.rou_additions_nonpremises", g["rou_additions_nonpremises"])
    lease_add["ltm"] = lease_add.pop("ltm_2026H1")
    macro = F.raw["macro"]
    pct = 0.01
    cpi = {k: need(F, f"macro.cpi_yoy.{k}") * pct for k in macro["cpi_yoy"]}
    key = {k: need(F, f"macro.key_rate_avg.{k}") * pct for k in macro["key_rate_avg"]}
    ad = {k: need(F, f"macro.ad_market_internet_yoy.{k}") * pct for k in ("akar_2026Q1", "arir_2026H1")}
    bal = F.raw["balance"]
    dates = [d for d in bal if re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
    adate = anchor[:4] + _ANCHOR_DATE[anchor[4:]]
    nd = {d: need(F, f"balance.{d}.net_debt_nonbank") for d in dates}
    kb = {d: need(F, f"balance.{d}.bank_capital_f123") for d in dates}
    nwc = {d: need(F, f"balance.{d}.nwc_nonbank") for d in dates if "nwc_nonbank" in bal[d]}
    fa = (need(F, f"balance.{adate}.bs_ppe") + need(F, f"balance.{adate}.bs_intangibles_other")
          + sum(need(F, f"balance.{adate}.rou_{c}") for c in ("cars", "land", "other")))
    bridge = {k: need(F, f"balance.{adate}.{k}") for k in bal[adate] if k.startswith("bridge_")}
    eq = F.raw["equity"]
    divs = []
    for i, d in enumerate(eq["dividends"]):
        if not d.get("record_date"):
            continue                        # выплачен до якоря: деньги уже вне кассы (§7.1)
        try:
            rd = dt.date.fromisoformat(d["record_date"])
        except ValueError:
            raise FactsError(f"факты: equity.dividends[{i}].record_date — не дата") from None
        paid_amount = need(F, f"equity.dividends.{i}.paid_bn_rub") if "paid_bn_rub" in d else None
        divs.append(DeclaredDividend(label=str(d.get("for")), amount=need(F, f"equity.dividends.{i}.total_bn_rub"),
                                     record_date=rd, paid=str(d.get("paid")), paid_amount=paid_amount))
    return CoreFacts(
        F=F, anchor=anchor, facts_date=adate, rev=rev, ebitda=ebitda,
        gtv_B=_series(F, "segments.gtv.B", list(gtv["B"]), True),
        gtv_C=_series(F, "segments.gtv.C", list(gtv["C"]), True),
        sbc=_series(F, "group.sbc", g["sbc"]), capex=_series(F, "group.capex", g["capex"]),
        lease_add=lease_add, da=_series(F, "group.da_ex_office", g["da_ex_office"]),
        tax_rate_diff=_series(F, "group.tax_rate_diff", g["tax_rate_diff"]),
        interest_expense=_series(F, "group.interest_expense", g["interest_expense"]),
        office_lease_interest=_series(F, "group.office_lease_interest", g["office_lease_interest"]),
        interest_income=_series(F, "group.interest_income", g["interest_income"]),
        cpi=cpi, key_rate_avg=key, ad_market=ad, nd_nonbank=nd,
        nd_company=need(F, f"balance.{adate}.net_debt_company"), bank_money=need(F, f"balance.{adate}.bank_money"),
        bank_capital=kb, nwc=nwc, fa_anchor=fa, bridge=bridge,
        shares_mln=need(F, f"equity.{adate}.shares_outstanding"),
        options_vested_mln=need(F, f"equity.{adate}.options_exercisable"),
        dividends=tuple(divs), balance_dates={d: d for d in dates})


def _contains(period: str, q: str) -> bool:
    """Период факта (квартал ГГГГQn, полугодие ГГГГHn, год ГГГГ) содержит квартал q."""
    y, n = q.split("Q")
    return period in (q, f"{y}H{1 if int(n) <= 2 else 2}", y)


def one_composition(A: dict, cf: CoreFacts) -> CoreFacts:
    """§0.5, ключ `growth.A.intragroup_2025` (книга 1.4): факты одного состава внутригрупповых расчётов — эффект e(q)
    вычитается из выручки и EBITDA A и прибавляется к выручке исключений X и EBITDA F и «прочих» F во всех периодах
    фактов, содержащих квартал q. Группа не меняется. Без ключа — те же факты (книга 1.3); повторно не прикладывается."""
    from dataclasses import replace

    from model.book_schema import opt

    eff = opt(A, "growth", "A", "intragroup_2025")
    if not eff:
        return cf
    tag = tuple(sorted((str(q), float(e)) for q, e in eff.items()))
    if cf.composition:
        if cf.composition != tag:
            raise ValueError(f"факты уже приведены к другому составу {cf.composition} (§0.5)")
        return cf
    rev = {b: dict(s) for b, s in cf.rev.items()}
    ebitda = {b: dict(s) for b, s in cf.ebitda.items()}
    for q, e in tag:
        for series, sign in ((rev["A"], -1.0), (ebitda["A"], -1.0), (rev["X"], 1.0), (ebitda["F"], 1.0),
                             (ebitda["F_o"], 1.0)):
            for k in series:
                if _contains(k, q):
                    series[k] = series[k] + sign * e
    return replace(cf, rev=rev, ebitda=ebitda, composition=tag)
