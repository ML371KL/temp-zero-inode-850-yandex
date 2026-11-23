"""Закрытая схема книги допущений Яндекса (YDEX): ключи, типы, обязательность, инварианты.

Ключ, которого нет в схеме, пропуск ключа и значение не того типа — отказ `BookError` с путём и причиной: ядро не
угадывает опечаток (docs/MODEL.md, вступление). Новый ключ книги регистрируется здесь тем же изменением, что и код,
который его читает. Числа книги здесь не встречаются: только имена ключей, форма значений и правила согласованности
(§0.1, §3, §3.1, §4.3–4.5, §6.2):

* траектории миров — ровно полугодия от `first_period` до `last_period`; траектории компании — без ключа полугодия на
  `last_period` (терминал прочёл бы его как `LT`);
* вероятности слоёв, режимов и уровней капвложений — в сумме 1;
* среднее четырёх режимов каждого режимного суждения = правилу центра (инвариант §3.1, §13.1): цель маржи — центру,
  сдвиг роста — нулю;
* края суждений: низ ≤ центр ≤ верх;
* годы: схода роста и целей маржи — не позже `last_period`; у блоков с ненулевым сдвигом год схода позже конца сдвига (§4.3);
  путь автономных технологий упорядочен (§4.5); год схода вложений позже конца уровней (§4.9); при ключе
  `capex.rule: kappa` — ключи правила κ есть, ключей прежнего правила нет, уровни κ упорядочены (§4.9);
* `worlds.source.sha256` — хэш записи миров (сверку с записью дерева делает сторож приёма миров).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any


class BookError(ValueError):
    """Книга допущений не читается однозначно — считать по ней нельзя."""


# Имена осей сетки (docs/MODEL.md §3) и блоков (§2). Порядок — порядок печати.
WORLDS = ("N", "H", "M")
REGIMES = ("stress", "slow", "base", "strong")
CAPEX_LEVELS = ("low", "base", "high")
KAPPA_RULE = "kappa"                              # значение ключа `capex.rule` правила капиталоёмкости (§4.9)
BLOCKS = ("A", "B", "C", "D", "E", "G")          # блоки с выручкой и маржой по путям §4.2–4.4
ALL_BLOCKS = ("A", "B", "C", "D", "E", "F", "G")  # блоки потока (F — расход, §4.5)
SHIFT_BLOCKS = ("A", "B", "C")                    # блоки с диапазоном сдвига роста (суждения 1–3)
CURVE_NODES = ("1", "3", "5", "10", "LT")
BRIDGE_SIDES = ("asset", "liability")

PERIOD_RE = re.compile(r"^(\d{4})H([12])$")
QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")
YEAR_RE = re.compile(r"^\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Допуски проверок книги (точность записи чисел, а не допущение).
PROB_SUM_TOL = 1e-9
CENTER_TOL = 1e-9


def _keyed(keys: tuple[str, ...], spec: Any) -> tuple:
    """Словарь ровно с ключами `keys`, значения — `spec`."""
    return ("keyed", keys, spec)


def _list(spec: Any) -> tuple:
    return ("list", spec)


_WORLD = {"name": "str", "key_rate": "wtraj", "cpi": "wtraj", "zero_curve": "curve", "lt": {"inflation": "num"}}
_REG = _keyed(REGIMES, "num")
_BRIDGE_LINE = {"fact": "str", "side": "side", "label": "str"}
_AXIS = {"key": "str", "name": "str", "kind": "axkind", "paths": _list("str"), "range": ("opt", "str"),
         "offset": ("opt", "str"),          # книга 1.2 (§9): края — сдвиги от значения книги
         "unit": "str", "bounds": "bounds",
         "bounds_from": ("opt", "pathbounds")}   # книга 1.4 (§9): граница — значение ключа книги (путь) или null
AXIS_KINDS = ("value", "path")

SCHEMA: dict[str, Any] = {
    "meta": {"version": "str", "date": "date", "facts_date": "date", "anchor_period": "period",
             "first_period": "period", "last_period": "period", "valuation_date": "date",
             "market_price": "pos", "curve_as_of": "date", "observation_quarter": "quarter",
             "window_start": "quarter",
             "balance_base": ("opt", "str"),          # книга 1.2 (§0): `ex_fs` — доли баланса к выручке без ФС
             "release_rules": ("opt", "bool")},       # книга 1.4 (§14): правила капитала и подписи моста в выпуске
    "worlds": {"source": {"version": "str", "book": "str", "sha256": "sha256", "record_asof": "date",
                          "curve_date": "date"},
               **{w: _WORLD for w in WORLDS}},
    "joint": {"world_prob": _keyed(WORLDS, "prob"), "market_implied_prob": _keyed(WORLDS, "prob"),
              "neutral_world": "world", "lambda": "prob",
              "regime_prob": _keyed(REGIMES, "prob"), "capex_prob": _keyed(CAPEX_LEVELS, "prob"),
              # условность уровня вложений по режиму (§3, как у X5); нет ключа — capex_prob во всех режимах
              "capex_prob_given_regime": ("opt", _keyed(REGIMES, _keyed(CAPEX_LEVELS, "prob"))),
              "expectation": ("opt", "str")},      # книга 1.4 (§4.9, §13.2): `headline` — ожидание с весами заголовка
    "path_2026h2": {"real_growth": _keyed(BLOCKS, "num"), "margin": _keyed(BLOCKS, "num"), "f_ebitda": "num",
                    "b_growth": ("opt", "str")},      # книга 1.2 (§4.2): `revenue` — рост B по выручке
    "attack": {"ebitda_2026h2": "num", "range": "range"},
    "growth": {"lt_real": "num", "lt_premium": _keyed(BLOCKS, "num"), "converge_year": _keyed(BLOCKS, "year"),
               "A": {"y2027_real": "num", "autoru_share": "prob", "autoru_sale_date": "date",
                     # книга 1.2 (§4.2, §0.3): `multiply` — Авто.ру множителем; `monthly` — ИПЦ 12 мес. по месяцам
                     "autoru_form": ("opt", "str"), "cpi_12m": ("opt", "str"),
                     # книга 1.4 (§0.5): эффект смены внутригрупповых расчётов по кварталам — факты одного состава
                     "intragroup_2025": ("opt", "qmap")},
               "shift_range": _keyed(SHIFT_BLOCKS, "range"), "shift": _keyed(BLOCKS, _REG), "shift_to": "year"},
    "margin": {"center": _keyed(BLOCKS, "num"), "range": _keyed(BLOCKS, "range"),
               "B": {"gtv_range": "range"}, "C": {"ozon_trade_gmv": "num"},
               "targets": _keyed(BLOCKS, _REG), "target_year": _keyed(BLOCKS, "year"),
               "season_gap": _keyed(BLOCKS, "num"),
               # книга 1.3 (§4.4): добавка взносов IT IV кв. 2025 г. по ставке 2026 г. и доли блоков
               "it_contrib_4q25": ("opt", {"amount": "nonneg", "A": "prob", "B": "prob", "F_o": "prob"})},
    "f": {"revenue_share": "num", "other_share": "num",
          "at": {"growth_nominal": "num", "growth_to": "year", "real_to": "year", "zero_from": "year",
                 "perpetual_share": "nonneg", "high_zero_from": ("opt", "year"), "position": ("opt", "num")}},
    "excl": {"revenue_share": "num", "ebitda_share": "num",
             "revenue_period": ("opt", "str")},         # книга 1.2 (§4.1): `ltm` — доля исключений за 12 мес.
    "bank": {"capital_to_revenue": "nonneg",
             "anchor_capital": ("opt", "str")},         # книга 1.3 (§4.6): `fact` — капитал банка на якоре по факту
    "sbc": {"share": "nonneg", "h1_share": "prob", "range": ("opt", "range"),
            # книга 1.2: края оси сдвигами от центра (§9); годы сезона (§4.8)
            "range_offset": ("opt", "range"), "h1_years": ("opt", _list("year"))},
    "capex": {"levels": ("opt", _keyed(CAPEX_LEVELS, "nonneg")), "levels_to": "year", "lease_share": "nonneg",
              "converge_year": ("opt", "year"),
              "asset_life_years": "life",
              # ключ правила капиталоёмкости (§4.9, §17): `rule: kappa`; нет ключа — доли уровней `levels`
              "rule": ("opt", "str"), "kappa_levels": ("opt", _keyed(CAPEX_LEVELS, "pos")),
              "kappa_base_capex": ("opt", "prob"), "kappa_high_capex": ("opt", "prob"),
              "first_share": ("opt", "prob"), "floor_da": ("opt", "prob"),
              # книга 1.2 (§4.9): `ltm` — окно базового κ; `mirror` — высокий уровень зеркален низкому
              "kappa_window": ("opt", "str"), "kappa_high": ("opt", "str"),
              # книга 1.3 (§4.9): остаток капитала якоря; провал κ на 30.06 (цель доли и δ — перевывод); доля окна —
              # ожидание всех клеток
              "anchor_rest": ("opt", "str"), "season": ("opt", "str"), "h1_share": ("opt", "prob"),
              "h1_dip": ("opt", "nonneg"), "kappa_target": ("opt", "str"),
              # книга 1.4 (§4.9, §14): право пользования землёй по факту; доля клетки базового уровня D_b (перевывод)
              "anchor_land": ("opt", {"da_half": "pos"}), "kappa_base_cell_capex": ("opt", "prob")},
    "ma": {"share": "nonneg", "until": "year", "range": "range"},
    "nwc": {"share_h1": "num", "share_h2": "num",
            # книга 1.2 (§4.10): строки моста, вычитаемые из оборотного; место налога на прибыль к уплате и авансов
            "less_bridge": ("opt", _list("str")), "income_tax_place": ("opt", "str")},
    "tax": {"general": "prob", "it_rate": "traj", "it_rate_lt_range": ("opt", "range"), "it_share": "prob",
            "it_rate_lt_offset": ("opt", "range"),     # книга 1.2 (§9): края оси 9 сдвигами
            "base_rule": ("opt", "str"),               # книга 1.2 (§4.11): `f_other` — «прочие» F уменьшают базу
            # книга 1.4 (§4.11): блоки общей ставки; `none` — без доли амортизации
            "general_blocks": ("opt", _list("str")), "general_blocks_da": ("opt", "str")},
    "terminal": {"real_growth": "num", "real_growth_range": "range", "roic": "pos",
                 "reinvestment_excludes_nwc": ("opt", "bool"),
                 "da_cohort_years": ("opt", "count"),   # книга 1.2 (§6.3): лет полугодий когортами до Гордона
                 "shield_cohort": ("opt", "bool"),      # книга 1.3 (§6.4): щит первых H лет — на ставке когорт
                 "gordon_dip": ("opt", "bool")},        # книга 1.4 (§6.3): строки Гордона с провалом κ
    "financing": {"spread": "num", "floaters": _list("str"),
                  "shield_cap": ("opt", "bool")},       # книга 1.2 (§4.13): ставка щита ≤ tax.general
    "valuation": {"beta_u": "nonneg_or_null",
                  "beta_rule": {"window_years": "pos", "debt_beta": "num", "skip_week_ends": _list("date")},
                  "erp": "num", "governance_discount": "discount",
                  "governance_range": "range", "net_exercise_k": "prob", "net_exercise_range": "range",
                  "options_recognized": ("opt", "prob"),   # книга 1.4 (§7.2): признанная часть неисполнимых опционов
                  "headline": {"print_step": "pos"},
                  "uncertainty": {"draws": "count", "small_draws": "count", "seed": "int", "axes": _list(_AXIS)},
                  "reverse_dcf": {"subsample": "count", "search_half_ranges": "pos", "axes": _list("str")}},
    "bridge": {"include": _list(_BRIDGE_LINE),
               "dividend_paid": ("opt", "bool"),        # книга 1.2 (§7.1): дивиденд по выплате ОДДС
               "net_exercise_tax": ("opt", "prob")},    # книга 1.3 (§7.1): налог при нетто-исполнении — доля стоимости
    "checks": {"facts_identity_tol": "pos", "da_tol": "pos", "ebitda_2026": "range", "flow_9m26_tol": "pos",
               "tax_2026_27": "range", "ebitda_2027_brokers": "pos", "ebitda_2027_tol": "pos",
               "roic_tol": "pos", "ev_ebitda_2027": "range", "terminal_share": "range",
               "brokers_price": "range", "paper_price": "range",
               # коридоры гейтов §13.2: нет ключа — гейт не считается (книга 1.0)
               "terminal_share_cell": ("opt", "range"), "margin_range": ("opt", "range"),
               "capex_range": ("opt", "range"), "real_rate": ("opt", "range"), "min_v0_to_d": ("opt", "pos"),
               # книга 1.2 (§13.2): масса гейтов клеток по точке; поток 9М26 и коридор оборотного на 30.09
               "cell_mass": ("opt", "str"), "flow_9m26_capex": ("opt", "str"), "flow_9m26_nwc_tol": ("opt", "pos"),
               # книга 1.3 (§13.2): гейт отдачи по одной ставке; поток 9М26 без потоков моста; клетки с весом 0
               "roic_tax": ("opt", "str"), "flow_9m26_bridge": ("opt", "str"), "zero_weight_cells": ("opt", "str"),
               # книга 1.4 (§13.2): поток 9М26 без движения требований моста; отдача прироста одного сезона
               "flow_9m26_claims": ("opt", {"withheld_tax": "nonneg"}), "roic_one_season": ("opt", "bool")},
    "forks": {"stress_weight": "prob",
              "rederive": ("opt", "bool")},             # книга 1.4 (§13.5): развилки с перевыводом
}


# ------------------------------------------------------------------ листья


def is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def is_trajectory(x: Any) -> bool:
    return isinstance(x, dict) and bool(x) and all(is_number(v) for v in x.values())


def _traj_keys_ok(spec: dict) -> bool:
    return all(PERIOD_RE.match(str(k)) or YEAR_RE.match(str(k)) or k in ("LT", "LT_from") for k in spec)


def _check_leaf(kind: str, x: Any, where: str, A: dict) -> None:
    def bad(why: str) -> None:
        raise BookError(f"книга: {where} = {x!r} — {why}")

    if kind == "str":
        if not isinstance(x, str) or not x:
            bad("ожидается непустая строка")
    elif kind == "date":
        if not (isinstance(x, str) and DATE_RE.match(x)):
            bad("ожидается дата 'ГГГГ-ММ-ДД' строкой")
        dt.date.fromisoformat(x)
    elif kind == "period":
        if not (isinstance(x, str) and PERIOD_RE.match(x)):
            bad("ожидается полугодие ГГГГH1|ГГГГH2")
    elif kind == "quarter":
        if not (isinstance(x, str) and QUARTER_RE.match(x)):
            bad("ожидается квартал ГГГГQn")
    elif kind == "year":
        if not (isinstance(x, int) and not isinstance(x, bool) and 2000 <= x <= 2100):
            bad("ожидается год целым числом")
    elif kind == "sha256":
        if not (isinstance(x, str) and SHA_RE.match(x)):
            bad("ожидается sha256 (64 шестнадцатеричных знака)")
    elif kind == "world":
        if x not in WORLDS:
            bad(f"ожидается мир из {WORLDS}")
    elif kind == "side":
        if x not in BRIDGE_SIDES:
            bad(f"ожидается сторона из {BRIDGE_SIDES}")
    elif kind == "count":
        if not (isinstance(x, int) and not isinstance(x, bool) and x >= 1):
            bad("ожидается целое ≥ 1")
    elif kind == "int":
        if not (isinstance(x, int) and not isinstance(x, bool)):
            bad("ожидается целое")
    elif kind == "axkind":
        if x not in AXIS_KINDS:
            bad(f"ожидается вид оси из {AXIS_KINDS}")
    elif kind == "bounds":
        if not (isinstance(x, list) and len(x) == 2 and all(v is None or is_number(v) for v in x)
                and (None in x or x[0] <= x[1])):
            bad("ожидаются границы [низ | null, верх | null]")
    elif kind == "bool":
        if not isinstance(x, bool):
            bad("ожидается true или false")
    elif kind == "qmap":                    # книга 1.4 (§0.5): квартал → неотрицательное число
        if not (isinstance(x, dict) and x and all(isinstance(k, str) and QUARTER_RE.match(k) and is_number(v)
                                                  and v >= 0 for k, v in x.items())):
            bad("ожидается словарь {ГГГГQn: число ≥ 0}")
    elif kind == "pathbounds":              # книга 1.4 (§9): [путь | null, путь | null]
        if not (isinstance(x, list) and len(x) == 2 and all(v is None or (isinstance(v, str) and v) for v in x)):
            bad("ожидаются границы [путь ключа | null, путь ключа | null]")
        for v in x:
            if v is not None and not is_number(get_node(A, v)):
                bad(f"путь {v} ведёт не к числу")
    elif kind == "num":
        if not is_number(x):
            bad("ожидается число")
    elif kind == "pos":
        if not (is_number(x) and x > 0):
            bad("ожидается положительное число")
    elif kind == "nonneg":
        if not (is_number(x) and x >= 0):
            bad("ожидается неотрицательное число")
    elif kind == "nonneg_or_null":
        if x is not None and not (is_number(x) and x >= 0):
            bad("ожидается неотрицательное число или null (ещё не выведено)")
    elif kind == "prob":
        if not (is_number(x) and 0 <= x <= 1):
            bad("ожидается доля из [0; 1]")
    elif kind == "discount":
        if not (is_number(x) and 0 <= x < 1):
            bad("ожидается доля из [0; 1)")
    elif kind == "range":
        if not (isinstance(x, list) and len(x) == 2 and all(is_number(v) for v in x) and x[0] <= x[1]):
            bad("ожидается [низ, верх], низ ≤ верх")
    elif kind == "life":
        if not (is_number(x) and 1 <= x <= 15 and abs(2 * x - round(2 * x)) < 1e-12):
            bad("ожидается срок из {1; 1,5; …; 15} лет (кратен половине года)")
    elif kind == "curve":
        if not (isinstance(x, dict) and set(map(str, x)) == set(CURVE_NODES) and all(is_number(v) for v in x.values())):
            bad(f"ожидается кривая с узлами {CURVE_NODES}")
    elif kind == "wtraj":
        want = _halves(A["meta"]["first_period"], A["meta"]["last_period"])
        if not (is_trajectory(x) and sorted(map(str, x)) == sorted(want)):
            bad("траектория мира — ровно полугодия от first_period до last_period")
    elif kind == "traj":
        if not (is_trajectory(x) and _traj_keys_ok(x)):
            bad("ожидается траектория {полугодие|год|LT|LT_from: число}")
        if A["meta"]["last_period"] in x:
            bad(f"ключ полугодия {A['meta']['last_period']} в траектории компании запрещён (§0.1)")
    else:  # pragma: no cover — опечатка схемы
        raise AssertionError(f"схема: неизвестный тип {kind}")


def _halves(first: str, last: str) -> list[str]:
    def idx(p: str) -> int:
        return 2 * int(p[:4]) + int(p[5]) - 1
    return [f"{i // 2}H{i % 2 + 1}" for i in range(idx(first), idx(last) + 1)]


def _walk(spec: Any, x: Any, where: str, A: dict) -> None:
    if isinstance(spec, dict):
        if not isinstance(x, dict):
            raise BookError(f"книга: {where} — ожидается блок с ключами {sorted(spec)}")
        extra = sorted(set(map(str, x)) - set(spec))
        if extra:
            raise BookError(f"книга: {where}: незнакомые ключи {extra} (закрытая схема model/book_schema.py)")
        missing = sorted(k for k in set(spec) - set(map(str, x))
                         if not (isinstance(spec[k], tuple) and spec[k][0] == "opt"))
        if missing:
            raise BookError(f"книга: {where}: нет ключей {missing}")
        for k, sub in spec.items():
            if isinstance(sub, tuple) and sub[0] == "opt":
                if k not in x:
                    continue                # необязательный ключ: нет ключа — прежняя механика ядра
                sub = sub[1]
            _walk(sub, x[k], f"{where}.{k}" if where else k, A)
    elif isinstance(spec, tuple) and spec[0] == "keyed":
        _, keys, sub = spec
        if not isinstance(x, dict) or set(map(str, x)) != set(keys):
            raise BookError(f"книга: {where} — ожидаются ровно ключи {list(keys)}")
        for k in keys:
            _walk(sub, x[k], f"{where}.{k}", A)
    elif isinstance(spec, tuple) and spec[0] == "list":
        if not isinstance(x, list):
            raise BookError(f"книга: {where} — ожидается список")
        for i, item in enumerate(x):
            _walk(spec[1], item, f"{where}[{i}]", A)
    else:
        _check_leaf(spec, x, where, A)


def get_node(A: dict, path: str) -> Any:
    """Узел книги по пути через точку; нет пути — `BookError`."""
    node: Any = A
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise BookError(f"книга: нет пути {path}")
        node = node[part]
    return node


# ------------------------------------------------------------- согласованность


def _sum_one(w: dict, where: str) -> None:
    if abs(math.fsum(float(v) for v in w.values()) - 1.0) > PROB_SUM_TOL:
        raise BookError(f"книга: {where} — вероятности в сумме не 1")


def _within(rng: list, c: float, where: str) -> None:
    if not rng[0] - CENTER_TOL <= c <= rng[1] + CENTER_TOL:
        raise BookError(f"книга: {where}: центр {c!r} вне краёв {rng!r}")


def company_trajectories(A: dict) -> list[tuple[str, dict]]:
    """Траектории компании книги (для замечаний `book_warnings`)."""
    return [("tax.it_rate", A["tax"]["it_rate"])]


def _consistency(A: dict) -> None:
    m = A["meta"]
    first, last, anchor = m["first_period"], m["last_period"], m["anchor_period"]
    if _halves(anchor, first) != [anchor, first]:
        raise BookError("книга: meta.first_period — следующее полугодие после anchor_period")
    if len(_halves(first, last)) < 2 or not last.endswith("H2"):
        raise BookError("книга: meta.last_period — второе полугодие года после first_period")
    last_year = int(last[:4])
    y0 = int(first[:4])                 # год первого прогнозного полугодия: годы проверок — от календаря книги
    j = A["joint"]
    for key in ("world_prob", "market_implied_prob", "regime_prob", "capex_prob"):
        _sum_one(j[key], f"joint.{key}")
    if "capex_prob_given_regime" in j:
        cond = j["capex_prob_given_regime"]
        for r in REGIMES:
            _sum_one(cond[r], f"joint.capex_prob_given_regime.{r}")
        for c in CAPEX_LEVELS:
            mean = math.fsum(float(j["regime_prob"][r]) * float(cond[r][c]) for r in REGIMES)
            if abs(mean - float(j["capex_prob"][c])) > PROB_SUM_TOL:
                raise BookError(f"книга: joint.capex_prob_given_regime: среднее по режимам уровня {c} {mean:.6f} ≠ "
                                f"joint.capex_prob.{c} (§3)")
    g, mg = A["growth"], A["margin"]
    for b in BLOCKS:
        shifts = [float(g["shift"][b][r]) for r in REGIMES]
        if abs(math.fsum(shifts) / len(shifts)) > CENTER_TOL:
            raise BookError(f"книга: growth.shift.{b}: среднее режимов ≠ 0 (правило центра, §3.1)")
        targets = [float(mg["targets"][b][r]) for r in REGIMES]
        if abs(math.fsum(targets) / len(targets) - float(mg["center"][b])) > CENTER_TOL:
            raise BookError(f"книга: margin.targets.{b}: среднее режимов ≠ margin.center (§3.1)")
        _within(mg["range"][b], float(mg["center"][b]), f"margin.range.{b}")
        y, ym = g["converge_year"][b], mg["target_year"][b]
        if not (y0 + 1 < y <= last_year and y0 < ym <= last_year):
            raise BookError(f"книга: год схода блока {b} — в ({y0 + 1}; {last_year}], год цели — в ({y0}; "
                            f"{last_year}] (§4.3, §4.4)")
        if any(abs(s) > 0 for s in shifts) and y <= g["shift_to"]:
            raise BookError(f"книга: growth.converge_year.{b} ≤ growth.shift_to при ненулевом сдвиге (§4.3)")
    for b in SHIFT_BLOCKS:
        _within(g["shift_range"][b], 0.0, f"growth.shift_range.{b}")
    at = A["f"]["at"]
    if not y0 < at["growth_to"] <= at["real_to"] < at["zero_from"] <= last_year:
        raise BookError("книга: f.at — годы пути расхода: год first_period < growth_to ≤ real_to < zero_from ≤ "
                        "last_period")
    if "position" in at and "high_zero_from" not in at:
        raise BookError("книга: f.at.position без f.at.high_zero_from — верхний край оси 6 не задан (§4.5, §9)")
    c = A["capex"]
    kappa_keys = ("kappa_levels", "kappa_base_capex", "kappa_high_capex", "first_share", "floor_da")
    if "rule" in c:
        if c["rule"] != KAPPA_RULE:
            raise BookError(f"книга: capex.rule = {c['rule']!r} — известно только правило {KAPPA_RULE!r} (§4.9)")
        missing = [k for k in kappa_keys if k not in c]
        extra = [k for k in ("levels", "converge_year") if k in c]
        if "reinvestment_excludes_nwc" in A["terminal"]:
            extra.append("terminal.reinvestment_excludes_nwc")
        if missing or extra:
            raise BookError("книга: capex.rule: kappa — " + "; ".join(
                ([f"нет ключей {missing}"] if missing else [])
                + ([f"ключи прежнего правила не действуют и запрещены: {extra}"] if extra else [])) + " (§4.9, §17)")
        if not y0 < c["levels_to"] <= last_year:
            raise BookError("книга: capex.levels_to — в (год first_period; last_period] (§4.9)")
        k = c["kappa_levels"]
        if not float(k["low"]) <= float(k["base"]) <= float(k["high"]):
            raise BookError("книга: capex.kappa_levels — низкий ≤ базовый ≤ высокий (§4.9)")
    else:
        stray = [k for k in kappa_keys if k in c]
        if stray or "levels" not in c or "converge_year" not in c:
            raise BookError(f"книга: без capex.rule нужны capex.levels и capex.converge_year, ключи правила κ {stray} "
                            "не действуют (§4.9)")
        if not c["levels_to"] < c["converge_year"] <= last_year:
            raise BookError("книга: capex.converge_year — в (levels_to; last_period] (§4.9)")
    _keys_1_2(A)
    for where, rng, center in (("attack.range", A["attack"]["range"], A["attack"]["ebitda_2026h2"]),
                               ("ma.range", A["ma"]["range"], A["ma"]["share"]),
                               ("terminal.real_growth_range", A["terminal"]["real_growth_range"],
                                A["terminal"]["real_growth"]),
                               ("valuation.governance_range", A["valuation"]["governance_range"],
                                A["valuation"]["governance_discount"]),
                               ("valuation.net_exercise_range", A["valuation"]["net_exercise_range"],
                                A["valuation"]["net_exercise_k"])):
        _within(rng, float(center), where)
    if "range" in A["sbc"]:
        _within(A["sbc"]["range"], float(A["sbc"]["share"]), "sbc.range")


# Ключи книги 1.2 (§17, «Ключи книги 1.2»): допустимые значения; ключи только при правиле κ
KEYS_1_2 = {("meta", "balance_base"): ("ex_fs",), ("path_2026h2", "b_growth"): ("revenue",),
            ("growth", "A", "autoru_form"): ("multiply",), ("growth", "A", "cpi_12m"): ("monthly",),
            ("excl", "revenue_period"): ("ltm",), ("capex", "kappa_window"): ("ltm",),
            ("capex", "kappa_high"): ("mirror",), ("tax", "base_rule"): ("f_other",),
            ("nwc", "income_tax_place"): ("none", "nwc", "bridge"), ("checks", "cell_mass"): ("point",),
            ("checks", "flow_9m26_capex"): ("season",)}
# Ключи книги 1.3 (§17, «Ключи книги 1.3»): допустимые значения
KEYS_1_3 = {("capex", "anchor_rest"): ("cohort",), ("capex", "season"): ("h1_dip",),
            ("capex", "kappa_target"): ("all_cells",), ("bank", "anchor_capital"): ("fact",),
            ("checks", "roic_tax"): ("general",), ("checks", "flow_9m26_bridge"): ("cash",),
            ("checks", "zero_weight_cells"): ("pass",), ("terminal", "shield_cohort"): (True,)}
# Ключи книги 1.4 (§17, «Ключи книги 1.4»): допустимые значения
KEYS_1_4 = {("joint", "expectation"): ("headline",), ("tax", "general_blocks_da"): ("none",),
            ("terminal", "gordon_dip"): (True,), ("checks", "roic_one_season"): (True,), ("forks", "rederive"): (True,),
            ("meta", "release_rules"): (True,)}
KAPPA_ONLY = (("capex", "kappa_window"), ("capex", "kappa_high"), ("terminal", "da_cohort_years"),
              ("checks", "flow_9m26_capex"), ("capex", "anchor_rest"), ("capex", "season"), ("capex", "h1_share"),
              ("capex", "h1_dip"), ("capex", "kappa_target"), ("terminal", "shield_cohort"), ("checks", "flow_9m26_bridge"),
              # книга 1.4: право пользования землёй, Гордон с провалом, D_b, поток 9М26 без движения требований
              ("capex", "anchor_land"), ("terminal", "gordon_dip"), ("capex", "kappa_base_cell_capex"),
              ("checks", "flow_9m26_claims"))


def opt(A: dict, *path: str, default: Any = None) -> Any:
    """Необязательный ключ книги по пути; нет ключа — `default` (прежняя механика)."""
    node: Any = A
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _keys_1_2(A: dict) -> None:
    for path, allowed in {**KEYS_1_2, **KEYS_1_3, **KEYS_1_4}.items():
        v = opt(A, *path)
        if v is not None and v not in allowed:
            raise BookError(f"книга: {'.'.join(path)} = {v!r} — допустимо {list(allowed)} (§17, ключи книг 1.2–1.4)")
    for path, need_path, need_val in ((("capex", "anchor_land"), ("capex", "anchor_rest"), "cohort"),
                                      (("terminal", "gordon_dip"), ("capex", "season"), "h1_dip"),
                                      (("tax", "general_blocks_da"), ("tax", "general_blocks"), None),
                                      (("checks", "flow_9m26_claims"), ("checks", "flow_9m26_bridge"), "cash"),
                                      (("capex", "kappa_base_cell_capex"), ("meta", "release_rules"), True)):
        if opt(A, *path) is not None and (opt(A, *need_path) is None
                                          or (need_val is not None and opt(A, *need_path) != need_val)):
            raise BookError(f"книга: {'.'.join(path)} без {'.'.join(need_path)}"
                            f"{'' if need_val is None else ': ' + str(need_val)} (§17, ключи книги 1.4)")
    gb = opt(A, "tax", "general_blocks")
    if gb is not None and (not gb or any(b not in BLOCKS for b in gb)):
        raise BookError(f"книга: tax.general_blocks = {gb!r} — непустой список блоков из {list(BLOCKS)} (§4.11)")
    if opt(A, "capex", "rule") != KAPPA_RULE:
        stray = [".".join(p) for p in KAPPA_ONLY if opt(A, *p) is not None]
        if stray:
            raise BookError(f"книга: ключи {stray} действуют только с capex.rule: kappa (§17)")
    if opt(A, "checks", "flow_9m26_capex") is not None and opt(A, "checks", "flow_9m26_nwc_tol") is None:
        raise BookError("книга: checks.flow_9m26_capex без checks.flow_9m26_nwc_tol (§13.2)")
    if opt(A, "checks", "flow_9m26_bridge") is not None and opt(A, "checks", "flow_9m26_capex") is None:
        raise BookError("книга: checks.flow_9m26_bridge без checks.flow_9m26_capex (§13.2)")
    if opt(A, "capex", "season") is not None and (opt(A, "capex", "h1_dip") is None or opt(A, "capex", "h1_share") is None):
        raise BookError("книга: capex.season без capex.h1_dip и capex.h1_share (§4.9, перевывод)")
    if opt(A, "terminal", "shield_cohort") is not None and opt(A, "terminal", "da_cohort_years") is None:
        raise BookError("книга: terminal.shield_cohort без terminal.da_cohort_years (§6.4)")
    itc = opt(A, "margin", "it_contrib_4q25")
    if itc is not None and float(itc["A"]) + float(itc["B"]) + float(itc["F_o"]) > 1.0:
        raise BookError("книга: margin.it_contrib_4q25 — доли блоков в сумме не больше 1 (§4.4)")
    for where in (("sbc", "range_offset"), ("tax", "it_rate_lt_offset")):
        off = opt(A, *where)
        if off is not None and not float(off[0]) <= 0.0 <= float(off[1]):
            raise BookError(f"книга: {'.'.join(where)} — сдвиги краёв [≤ 0; ≥ 0] (§9)")
    for line in opt(A, "nwc", "less_bridge", default=[]):
        if line not in [b["fact"] for b in A["bridge"]["include"]]:
            raise BookError(f"книга: nwc.less_bridge: {line} — не строка bridge.include (§4.10)")


def _axes_consistency(A: dict) -> None:
    """Оси полосы и обратного DCF (§9, §11): пути и края существуют, ключи осей уникальны, оси обратного DCF —
    оси полосы вида value."""
    axes = A["valuation"]["uncertainty"]["axes"]
    keys = [a["key"] for a in axes]
    if len(set(keys)) != len(keys):
        raise BookError("книга: valuation.uncertainty.axes — ключи осей повторяются")
    for a in axes:
        where = f"valuation.uncertainty.axes[{a['key']}]"
        if a["kind"] == "value":
            if ("range" in a) == ("offset" in a):
                raise BookError(f"книга: {where}: у оси value нужен ровно один из ключей range (края суждения) или "
                                "offset (сдвиги краёв, §9)")
            edge = a["range"] if "range" in a else a["offset"]
            _check_leaf("range", get_node(A, edge), edge, A)
            vals = [float(get_node(A, p)) for p in a["paths"] if is_number(get_node(A, p))]
            if len(vals) == len(a["paths"]) and max(vals) - min(vals) > CENTER_TOL:
                raise BookError(f"книга: {where}: пути оси {a['paths']} — одно суждение, значения книги должны "
                                "совпадать (§9)")
            for p in a["paths"]:
                if not is_number(get_node(A, p)):
                    raise BookError(f"книга: {where}: путь {p} ведёт не к числу")
    kinds = dict(zip(keys, (a["kind"] for a in axes)))
    for k in A["valuation"]["reverse_dcf"]["axes"]:
        if k not in kinds:
            raise BookError(f"книга: valuation.reverse_dcf.axes: оси {k} нет в valuation.uncertainty.axes")
    for a in axes:
        if a["kind"] == "path":
            for p in a["paths"]:
                try:
                    node = get_node(A, p)
                except BookError:
                    continue                # нет ключа положения — ось ядро не считает (прежняя механика)
                if not is_number(node):
                    raise BookError(f"книга: ось {a['key']}: путь {p} — положение пути, число (книга — 0)")


def validate_book(A: Any) -> None:
    """Проверка книги закрытой схемой и правилами согласованности; отказ — `BookError`."""
    if not isinstance(A, dict) or not isinstance(A.get("meta"), dict):
        raise BookError("книга: нет блока meta")
    for k in ("first_period", "last_period"):
        _check_leaf("period", A["meta"].get(k), f"meta.{k}", A)
    _walk(SCHEMA, A, "", A)
    _consistency(A)
    _axes_consistency(A)
