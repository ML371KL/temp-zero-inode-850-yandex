#!/usr/bin/env python
"""Второй счёт модели Яндекса (YDEX): клетка, сетка и точка по тексту методики `docs/MODEL.md`.

Написан только по методике, уставу (`850-work/yandex/CHARTER.md`), фактам `data/facts/*.json` и записи миров
`data/assumptions/worlds_source.json` — без чтения ядра (`model/`). Импортирует только стандартную библиотеку
(и PyYAML — лишь в режиме `--book`).

Что считает:
* выведенные ключи книги (§17) — сам, из фактов, по правилам методики (`derive`);
* клетку «мир × режим × уровень капвложений» (§4–§7): блоки A–G и исключения, поток группы, налог, амортизация
  когортами, щит, дисконт с перекатом, терминал с долей вложений c_T, требования, делитель, цена;
* сетку 36 клеток, слои миров и точку (§3, §8).

Режим «на книге ядра» (`--book <путь>`): выведенные значения, суждения и миры, которые есть в книге ядра по путям
§17, заменяют свои — так расхождения перевывода отделяются от расхождений прохода клетки.

Команды (из корня копии):
    python tests/independent_model.py cell --world N --regime base --capex base [--book P] [--asof Д] --json
    python tests/independent_model.py point [--book P] [--asof Д]
    python tests/independent_model.py derive          — выведенные ключи и промежуточные числа
    python tests/independent_model.py check           — самопроверки без ядра
    python tests/independent_model.py band|gates|forks|traps [--book P] [--draws N] — малая полоса §9, гейты §13.2,
                                                         развилки §13.5, ловушка «ухудшение» §13.3 (JSON)

Прочтения методики там, где она молчит или допускает два прочтения, названы в комментариях «ПРОЧТЕНИЕ n» и
перечислены в отчётах задач (`850-work/yandex/out/108-control.md`; правка 10б — `out/204-control.md`; книга 1.3 —
`out/402-control.md`; книга 1.4 — `out/413-control.md`). Своя книга по умолчанию — 1.4 (`--version 1.3|1.2|1.1`).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
FACTS_DIR = ROOT / "data" / "facts"
WORLDS_RECORD = ROOT / "data" / "assumptions" / "worlds_source.json"
CONTROL_FACTS = Path(__file__).resolve().parent / "control_facts.json"   # строки книги 1.3, снятые вторым счётом

BLOCKS = ("A", "B", "C", "D", "E", "F", "G")
FLOW = ("A", "B", "C", "D", "E", "G")           # блоки с путём роста и маржи (§4.3, §4.4)
REGIMES = ("stress", "slow", "base", "strong")
CAPEX_LEVELS = ("low", "base", "high")
WORLDS = ("N", "H", "M")
LAYERS = ("analytical", "market_implied", "macro_neutral")

# «12 мес.» и «окно» (§0)
Q12 = ("2025Q4", "2026Q1", "2026Q2", "2026Q3")
Q12_PREV = ("2024Q4", "2025Q1", "2025Q2", "2025Q3")
WINDOW_Q = tuple(f"{y}Q{q}" for y in (2024, 2025) for q in (1, 2, 3, 4)) + ("2026Q1", "2026Q2", "2026Q3")
FULL_YEARS = (2024, 2025)
CPI_12M = "12m_2026Q3"           # факт ИПЦ «12 мес.» по месячному ряду (§0.3, книга 1.2)
NINE_M = ("2026H1", "2026Q3")    # 9М26 (§13.2, поток 9М26)
NINE_M_PREV = ("2025H1", "2025Q3")
Q9M = ("2026Q1", "2026Q2", "2026Q3")

DEFAULT_ASOF = "2026-11-20"     # дата рыночной цены устава (раздел 5); тесты передают дату явно

# ----------------------------------------------------------------------------------------------------------------
# Суждения устава и правила семейства — единственное место с числами допущений. Всё остальное — из фактов,
# записи миров и правил §17. В режиме --book значения книги ядра по тем же путям их заменяют.
# ----------------------------------------------------------------------------------------------------------------
JUDGMENTS: dict = {
    "meta": {"anchor_period": "2026H1", "facts_date": "2026-06-30",
             "first_period": "2026H2", "last_period": "2036H2",
             "balance_base": "ex_fs",              # книга 1.2 (В1): доли баланса и доли вложений уровней κ — к R°
             "date": "2026-11-20",                 # устав, раздел 5: дата рыночной цены — конец окна β (ПРОЧТЕНИЕ 15)
             "valuation_date": "2026-11-20",       # устав, раздел 5
             "market_price": 3277.5},              # устав, раздел 5: рыночная цена сверки (20.11.2026)
    "joint": {
        "regime_prob": {"stress": 0.25, "slow": 0.25, "base": 0.25, "strong": 0.25},   # устав, раздел 2
        "capex_prob": {"low": 0.25, "base": 0.5, "high": 0.25},                        # суждение 7: средние
        # решение архитектора (вопрос 1 этапа 2в, вариант «г»; устав, суждение 7): уровень вложений зависит от
        # режима при неизменных средних — в стрессе высокого нет, в сильном — низкого
        "capex_prob_given_regime": {"stress": {"low": 0.5, "base": 0.5, "high": 0.0},
                                 "slow": {"low": 0.25, "base": 0.5, "high": 0.25},
                                 "base": {"low": 0.25, "base": 0.5, "high": 0.25},
                                 "strong": {"low": 0.0, "base": 0.5, "high": 0.5}},
        "neutral_world": "M",                   # семья (X5): «рыночные ставки как есть» — мир M записи
        "lambda": 0.5,                            # семья (X5)
        "market_implied_prob": {"N": 0.1, "H": 0.25, "M": 0.65},   # семья (X5), справочный слой
    },
    "attack": {"ebitda_2026h2": -10.0, "range": [-25.0, 0.0]},   # устав, раздел 1: ущерб атак 4К26 −10 (0–25)
    "growth": {
        "lt_real": 0.015,                                                  # устав, раздел 2
        "lt_premium": {"A": 0.0, "B": 0.01, "C": 0.0, "D": 0.0, "E": 0.0, "G": 0.0},   # суждение 2
        "converge_year": {"A": 2031, "B": 2031, "C": 2033, "D": 2033, "E": 2033, "G": 2033},
        "A": {"autoru_share": 0.02,                                        # решение архитектора (вопрос 5)
              "autoru_form": "multiply",                                   # книга 1.2 (В15): Авто.ру множителем
              "cpi_12m": "monthly"},                                       # книга 1.2: ИПЦ 12 мес. — месячный ряд
        "shift_range": {"A": [-0.03, 0.02], "B": [-0.04, 0.02], "C": [-0.04, 0.03]},   # суждения 1–3
    },
    "margin": {
        "center": {"D": 0.08, "E": 0.25, "G": 0.08},                       # суждения 4, 5
        "range": {"A": [0.36, 0.48], "C": [-0.01, 0.06], "D": [0.02, 0.15], "E": [0.15, 0.30],
                  "G": [0.0, 0.15]},                                       # суждения 1, 3–5
        "B": {"range_gtv": [0.055, 0.085]},                                # суждение 2: доли GTV
        "C": {"ozon_trade_gmv": 0.0225},         # суждение 3: середина «2–2,5 % GMV» (решение архитектора, точка 1)
        "target_year": {"A": 2030, "B": 2030, "C": 2030, "D": 2030, "E": 2030, "G": 2030},
    },
    "f": {"at": {"growth_nominal": 0.20, "growth_to": 2028, "real_to": 2030, "zero_from": 2033,
                 "perpetual_share": 0.02, "high_zero_from": 2028,           # суждение 6: края
                 "position": 0.0}},                                        # ось 6 (§4.5, §9): 0 — центр
    # суждение 7 (устав; решения архитектора этапа 2): вложения через капиталоёмкость κ (§4.9, §6.2). Уровни κ на
    # конец 2030 г. выводит перевывод (`derive_kappa`): низкий — κ на 31.12.2026; базовый — средняя доля капвложений
    # 2027–2030 = уровню окна (среднее 2025 г. и 9М26, выводится из фактов); высокий — = середине прогноза 11 %.
    # Книга 1.2 (решение В5): окно базового κ — «12 мес.», высокий уровень зеркален низкому — `kappa_high_capex`
    # выводит перевывод; книга 1.1 — суждение 11 % (JUDGMENTS_11).
    "capex": {"rule": "kappa", "first_share": 0.11, "floor_da": 0.5, "levels_to": 2030,
              "kappa_window": "ltm", "kappa_high": "mirror"},
    "ma": {"share": 0.005, "until": 2030, "range": [0.0, 0.01]},           # устав, раздел 2
    "sbc": {"range": [0.015, 0.035],                                       # суждение 8
            "range_offset": [-0.0083, 0.0117],                             # книга 1.2 (В8): края — сдвиги от центра
            "h1_years": [2025]},                                           # книга 1.2 (В15): сезон выплат — 2025 г.
    "tax": {"general": 0.25, "it_rate": {"2025": 0.05, "2030": 0.05, "LT": 0.15, "LT_from": 2031},  # закон; с. 9
            "it_rate_lt_range": [0.05, 0.25],                              # суждение 9
            "it_rate_lt_offset": [-0.10, 0.10],                            # книга 1.2 (ошибка 11): сдвиги от центра
            "base_rule": "f_other"},                                       # книга 1.2 (В7): «прочие» F в базе
    "terminal": {"real_growth": 0.015, "real_growth_range": [0.005, 0.025],   # суждение 10
                 "da_cohort_years": 40},                                   # книга 1.2 (ошибка 6): когорты 40 лет
    # приёмка устава (раздел 5) и решения архитектора о коридорах (§13.2)
    "checks": {"ebitda_2026": [300.0, 350.0], "tax_2026_27": [0.166, 0.246], "ebitda_2027_brokers": 355.0,
               "ebitda_2027_tol": 0.15, "roic_tol": 0.25, "ev_ebitda_2027": [3.0, 7.0],
               "terminal_share": [0.4, 0.75], "terminal_share_cell": [0.15, 0.85], "margin_range": [0.12, 0.35],
               "capex_range": [0.05, 0.15], "real_rate": [0.02, 0.15], "min_v0_to_d": 3.0,
               "flow_9m26_tol": 25.0,                                      # устав, раздел 5: ± 25 млрд
               "flow_9m26_nwc_tol": 5.0,                                   # книга 1.2 (В14): коридор оценки NWC
               "cell_mass": "point",                                       # книга 1.2 (В14): масса клеток по точке
               "flow_9m26_capex": "season"},                               # книга 1.2 (В14): поток 9М26 считается
    "forks": {"stress_weight": 0.4},                                       # устав, раздел 3
    "valuation": {
        "erp": 0.0557,                                                     # устав, раздел 2
        "beta_rule": {"window_years": 3, "debt_beta": 0.1,                 # правило семейства (X5 A-V1)
                      "skip_week_ends": ["2024-06-21", "2024-07-26"]},     # решение ведущего: склейка YNDX → YDEX
        "governance_discount": 0.03, "governance_range": [0.01, 0.06],   # устав, раздел 2
        "net_exercise_k": 0.70, "net_exercise_range": [0.64, 0.74],      # особая статья устава (раздел 4)
        # полоса §9: малая полоса, семя — дата книги 1.0 (§17); оси — суждения с диапазоном вне сетки, по порядку §9
        "uncertainty": {"small_draws": 400, "seed": 20261123, "axes": [
            {"key": "at_expense", "kind": "path", "paths": ["f.at.position"], "bounds": [None, None]},
            {"key": "sbc", "kind": "value", "paths": ["sbc.share"], "offset": "sbc.range_offset",
             "bounds": [0.0, 1.0]},
            {"key": "it_tax", "kind": "value", "paths": ["tax.it_rate.LT"], "offset": "tax.it_rate_lt_offset",
             "bounds": [0.0, 1.0]},
            {"key": "terminal_growth", "kind": "value", "paths": ["terminal.real_growth", "growth.lt_real"],
             "range": "terminal.real_growth_range", "bounds": [None, None]},
            {"key": "governance", "kind": "value", "paths": ["valuation.governance_discount"],
             "range": "valuation.governance_range", "bounds": [0.0, 1.0]},
            {"key": "ma", "kind": "value", "paths": ["ma.share"], "range": "ma.range", "bounds": [0.0, 1.0]},
            {"key": "attack", "kind": "value", "paths": ["attack.ebitda_2026h2"], "range": "attack.range",
             "bounds": [None, 0.0]},
            {"key": "net_exercise", "kind": "value", "paths": ["valuation.net_exercise_k"],
             "range": "valuation.net_exercise_range", "bounds": [0.0, 1.0]}]},
    },
    "bridge": {"include": [                                                # устав, раздел 4; решение архитектора
        {"fact": "bridge_fin_investments", "side": "asset", "label": "финансовые вложения"},
        {"fact": "bridge_loans_issued", "side": "asset", "label": "займы выданные краткосрочные"},
        {"fact": "loans_issued_noncurrent", "side": "asset", "label": "займы выданные долгосрочные"},
        {"fact": "bridge_autoru_receivable", "side": "asset", "label": "остаток оплаты за Авто.ру"},
        {"fact": "oca_net_lease_investment", "side": "asset", "label": "чистые инвестиции в аренду"}],   # книга 1.2
        "dividend_paid": True},                                            # книга 1.2 (ошибка 8): выплата по ОДДС
    "financing": {"floaters": ["1R3", "1R4"],                              # решение архитектора
                  "shield_cap": True},                                     # книга 1.2 (ошибка 9): t_S ≤ τ_g
    "path_2026h2": {"b_growth": "revenue"},                                # книга 1.2 (В3): рост B 2П26 по выручке
    "excl": {"revenue_period": "ltm"},                                     # книга 1.2 (В4): доля исключений — 12 мес.
    "nwc": {"less_bridge": ["bridge_autoru_receivable"],                   # книга 1.2 (ошибка 8): Авто.ру вне NWC
            "income_tax_place": "none"},             # книга 1.2 (40f36e7): налог к уплате и авансы — вне NWC и моста
}

# Ключи книги 1.2 (§17, «Ключи книги 1.2»): книга без ключа считается прежним правилом (книга 1.1).
BOOK12_KEYS = ("meta.balance_base", "path_2026h2.b_growth", "growth.A.autoru_form", "growth.A.cpi_12m",
               "excl.revenue_period", "financing.shield_cap", "capex.kappa_window", "capex.kappa_high",
               "tax.base_rule", "sbc.h1_years", "sbc.range_offset", "tax.it_rate_lt_offset",
               "terminal.da_cohort_years", "nwc.less_bridge", "nwc.income_tax_place", "bridge.dividend_paid",
               "checks.cell_mass",
               "checks.flow_9m26_capex", "checks.flow_9m26_nwc_tol")
BOOK12_BRIDGE = ("oca_net_lease_investment",)
OFFSET_TO_RANGE = {"sbc.range_offset": "sbc.range", "tax.it_rate_lt_offset": "tax.it_rate_lt_range"}

# Книга 1.1: те же суждения без ключей книги 1.2; высокий уровень κ — суждение 7 (11 %, середина прогноза компании).
JUDGMENTS_11 = {"capex": {"kappa_high_capex": 0.11}}

# Книга 1.3 (§17, «Ключи книги 1.3»; решения точки 3 Г1–Г11, рабочий файл, раздел 4): книга 1.2 и ключи ниже, без
# `tax.base_rule` (Г6: база налога книги 1.1). `axis_bounds` и `bridge_rows` — не ключи книги, а правка оси и моста.
JUDGMENTS_13: dict = {
    "capex": {"anchor_rest": "cohort",            # Г1: остаток капитала якоря — когортой с 2026H2 (§4.9)
              "season": "h1_dip",                 # Г2: провал κ на 30.06 на δ (§4.9, §6.3)
              "kappa_target": "all_cells"},       # Г3: доля окна — ожидание всех клеток (§4.9)
    "margin": {"it_contrib_4q25": {"amount": 2.0, "A": 0.35, "B": 0.15, "F_o": 0.15}},   # Г7 (§4.4)
    "nwc": {"income_tax_place": "bridge"},        # Г8: налог к уплате минус авансы — требование (§4.10, §7.1)
    "bridge": {"net_exercise_tax": 0.111},        # Г8: налог при нетто-исполнении — 11,1 % стоимости (§7.1)
    "bank": {"anchor_capital": "fact"},           # Г8: прирост капитала банка 2026H2 — от факта на якоре (§4.6)
    "terminal": {"shield_cohort": True},          # ниже порога 1: щит хвоста когорт (§6.4)
    "checks": {"roic_tax": "general",             # Г4: гейт отдачи прироста по τ_g (§13.2)
               "tax_2026_27": [0.202, 0.282],     # Г5: 24,2 % ± 4 п.п. без дохода от Авто.ру (§13.2)
               "flow_9m26_bridge": "cash",        # Г9: поток 9М26 без потоков активов моста (§13.2)
               "zero_weight_cells": "pass"},      # Г11: клетки с весом 0 не срабатывают (§13.2)
    "axis_bounds": {"it_tax": [0.0, 0.25]},       # Г4, ошибка 7: ставка IT после 2030 г. не выше τ_g (§9)
    "bridge_rows": [{"fact": "onca_net_lease_investment", "side": "asset",     # Г8, ошибка 8 (§7.1)
                     "label": "долгосрочные чистые инвестиции в аренду"}],
}

# Ключи книги 1.3 (§17): каждый включает одно правило; без ключа — книга 1.2 бит в бит.
BOOK13_KEYS = ("capex.anchor_rest", "capex.season", "capex.kappa_target", "margin.it_contrib_4q25",
               "bridge.net_exercise_tax", "bank.anchor_capital", "terminal.shield_cohort", "checks.roic_tax",
               "checks.flow_9m26_bridge", "checks.zero_weight_cells")

# Книга 1.4 (§17, «Ключи книги 1.4»; решения точки 3-2, ошибки волны 3 и список ниже порога — рабочий файл, раздел 4;
# методика 3f8e99a + 166eff9): книга 1.3 и ключи ниже. `axis_bounds_from` — не ключ книги, а правка оси 9 (§9).
JUDGMENTS_14: dict = {
    "growth": {"A": {"intragroup_2025": {"2024Q4": 4.3, "2025Q1": 8.7}}},   # §0.5: факты одного состава (ошибка 1)
    "tax": {"general_blocks": ["G"], "general_blocks_da": "none"},        # §4.11: банк по τ_g (ошибка 2)
    "joint": {"expectation": "headline"},                                 # §4.9, §13.2: веса миров заголовка
    "capex": {"anchor_land": {"da_half": 0.118}},                         # §4.9: право пользования землёй (ошибка 3)
    "terminal": {"gordon_dip": True},                                     # §6.3: Гордон с провалом κ (ниже порога 1)
    "valuation": {"options_recognized": 0.535},                           # §7.1, §7.2: признанная часть опционов
    "checks": {"tax_2026_27": [0.208, 0.288],                             # §13.2: 24,8 % ± 4 п.п. (ошибка 9)
               "flow_9m26_claims": {"withheld_tax": 2.376},               # §13.2: без движения требований (ошибка 7)
               "roic_one_season": True},                                  # §13.2: капитал одного сезона (ошибка 8)
    "forks": {"rederive": True},                                          # §13.5: развилки с перевыводом (ошибка 6)
    "meta": {"release_rules": True},                                      # §14: правила капитала в выпуске
    "axis_bounds_from": {"it_tax": [None, "tax.general"]},                # §9: граница оси 9 — τ_g книги (ошибка 10)
}

# Ключи книги 1.4 (§17): каждый включает одно правило; без ключа — книга 1.3 бит в бит.
BOOK14_KEYS = ("growth.A.intragroup_2025", "tax.general_blocks", "tax.general_blocks_da", "joint.expectation",
               "capex.anchor_land", "terminal.gordon_dip", "valuation.options_recognized", "checks.flow_9m26_claims",
               "checks.roic_one_season", "forks.rederive", "meta.release_rules")
VERSIONS = ("1.1", "1.2", "1.3", "1.4")


def judgments(version: str = "1.4") -> dict:
    """Суждения второго счёта: книга 1.4 (ключи §17 книг 1.2–1.4), 1.3, 1.2 или 1.1 (без ключей — прежние правила)."""
    J = copy.deepcopy(JUDGMENTS)
    if version == "1.2":
        return J
    if version == "1.1":
        strip_book12(J)
        return J
    if version not in ("1.3", "1.4"):
        raise ValueError(f"версия книги {version!r}: {', '.join(VERSIONS)}")
    add = copy.deepcopy(JUDGMENTS_13)
    bounds, rows = add.pop("axis_bounds"), add.pop("bridge_rows")
    J = deep_merge(J, add)
    J["tax"].pop("base_rule", None)                       # Г6: решение В7 отменено — «прочие» F в блоке F
    J["bridge"]["include"] += rows
    for ax in J["valuation"]["uncertainty"]["axes"]:
        if ax["key"] in bounds:
            ax["bounds"] = list(bounds[ax["key"]])
    if version == "1.3":
        return J
    add = copy.deepcopy(JUDGMENTS_14)
    bounds_from = add.pop("axis_bounds_from")
    J = deep_merge(J, add)
    for ax in J["valuation"]["uncertainty"]["axes"]:
        if ax["key"] in bounds_from:
            ax["bounds_from"] = list(bounds_from[ax["key"]])
    return J


def book_version(book: dict) -> str:
    """Версия книги ядра по ключам §17: ключ книги 1.4 — 1.4; книги 1.3 — 1.3; книги 1.2 — 1.2; иначе 1.1 (или 1.0)."""
    if any(dget(book, k, None) is not None for k in BOOK14_KEYS):
        return "1.4"
    if any(dget(book, k, None) is not None for k in BOOK13_KEYS):
        return "1.3"
    return "1.2" if any(dget(book, k, None) is not None for k in BOOK12_KEYS) else "1.1"


def take_rule_keys(J: dict, book: dict) -> None:
    """Режим «на книге ядра»: ключи правил §17 (книг 1.2–1.4) — как в книге: есть — значение книги, нет — ключа
    нет (прежнее правило). Без ключа `capex.kappa_high` высокий уровень κ — суждение книги 1.1."""
    for path in BOOK12_KEYS + BOOK13_KEYS + BOOK14_KEYS:
        v = dget(book, path, None)
        if v is not None:
            dset(J, path, copy.deepcopy(v))
            continue
        parts = path.split(".")
        node = dget(J, ".".join(parts[:-1]), None)
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    if "kappa_high" not in J["capex"]:
        J["capex"].setdefault("kappa_high_capex", JUDGMENTS_11["capex"]["kappa_high_capex"])


def strip_book12(J: dict, book: dict | None = None) -> None:
    """Убрать из ключей модели правила книги 1.2, которых нет в книге `book` (нет книги — все); без ключа
    `capex.kappa_high` высокий уровень κ — суждение книги 1.1."""
    for path in BOOK12_KEYS:
        if book is not None and dget(book, path, None) is not None:
            continue
        parts = path.split(".")
        node = dget(J, ".".join(parts[:-1]), None)
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    if "kappa_high" not in J["capex"]:
        J["capex"].setdefault("kappa_high_capex", JUDGMENTS_11["capex"]["kappa_high_capex"])
    if book is None:
        J["bridge"]["include"] = [r for r in J["bridge"]["include"] if r["fact"] not in BOOK12_BRIDGE]
        for ax in J["valuation"]["uncertainty"]["axes"]:
            if "offset" in ax:
                ax["range"] = OFFSET_TO_RANGE[ax.pop("offset")]
            ax["paths"] = ax["paths"][:1]

_N = NormalDist()


# ================================================================================================ вспомогательное

def val(x):
    """Число факта: лист пишет то число, то {"v": число, ...}."""
    return x.get("v") if isinstance(x, dict) else x


def dget(d: dict, path: str, default=KeyError):
    node = d
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, dict) and part.isdigit() and int(part) in node:
            node = node[int(part)]
        else:
            if default is KeyError:
                raise KeyError(path)
            return default
    return node


def dset(d: dict, path: str, value) -> None:
    parts = path.split(".")
    node = d
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and not _is_leaf_dict(k):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


_LEAF_DICTS = ("key_rate", "cpi", "food_cpi", "zero_curve", "it_rate")


def _is_leaf_dict(key: str) -> bool:
    return key in _LEAF_DICTS


def half(label: str) -> tuple[int, int]:
    return int(label[:4]), int(label[5])


def label(y: int, h: int) -> str:
    return f"{y}H{h}"


def shift_half(y: int, h: int, n: int) -> tuple[int, int]:
    i = 2 * y + (h - 1) + n
    return i // 2, i % 2 + 1


def halves(first: str, last: str) -> list[tuple[int, int]]:
    y, h = half(first)
    ly, lh = half(last)
    out = []
    while (y, h) <= (ly, lh):
        out.append((y, h))
        y, h = shift_half(y, h, 1)
    return out


def half_bounds(y: int, h: int) -> tuple[dt.date, dt.date]:
    return (dt.date(y, 1, 1), dt.date(y, 7, 1)) if h == 1 else (dt.date(y, 7, 1), dt.date(y + 1, 1, 1))


def pos(d: dt.date) -> float:
    """Положение даты на линейке полугодий: номер полугодия + доля (§5)."""
    h = 1 if d.month <= 6 else 2
    a, b = half_bounds(d.year, h)
    return 2 * d.year + (h - 1) + (d - a).days / (b - a).days


def traj(t, y: int, h: int):
    """Значение траектории книги в полугодии (§0.1)."""
    if not isinstance(t, dict):
        return t
    n = {str(k): v for k, v in t.items()}
    if label(y, h) in n:
        return n[label(y, h)]
    if str(y) in n:
        return n[str(y)]
    years = sorted(int(k) for k in n if k.isdigit() and len(k) == 4)
    hk = sorted(k for k in n if len(k) == 6 and k[4] == "H")
    if years:
        if y < years[0]:
            return n[str(years[0])]
        if y > years[-1]:
            last = n[str(years[-1])]
            if "LT" not in n:
                return last
            if "LT_from" not in n:
                return n["LT"]
            yf = int(n["LT_from"])
            if y >= yf:
                return n["LT"]
            return last + (n["LT"] - last) * (y - years[-1]) / (yf - years[-1])
        lo = max(k for k in years if k <= y)
        hi = min(k for k in years if k >= y)
        return n[str(lo)] + (n[str(hi)] - n[str(lo)]) * (y - lo) / (hi - lo)
    if hk:
        if label(y, h) < hk[0]:
            return n[hk[0]]
        return n.get("LT", n[hk[-1]])
    raise KeyError(f"траектория без ключей: {t}")


def traj_lt(t):
    """Значение траектории в терминале: LT, если задан, иначе последнее (§0.1, §6.1)."""
    if not isinstance(t, dict):
        return t
    n = {str(k): v for k, v in t.items()}
    if "LT" in n:
        return n["LT"]
    keys = sorted(k for k in n if k[:4].isdigit())
    return n[keys[-1]]


def zero_rate(zc: dict, t: float) -> float:
    """Бескупонная ставка мира на срок t лет (§0.2)."""
    z = {str(k): v for k, v in zc.items()}
    nodes = [(1.0, z["1"]), (3.0, z["3"]), (5.0, z["5"]), (10.0, z["10"])]
    if t <= nodes[0][0]:
        return nodes[0][1]
    for (t0, z0), (t1, z1) in zip(nodes, nodes[1:]):
        if t <= t1:
            return z0 + (z1 - z0) * (t - t0) / (t1 - t0)
    return ((1 + z["10"]) ** 10 * (1 + z["LT"]) ** (t - 10)) ** (1 / t) - 1


def regime_values(c: float, a: float, b: float) -> dict:
    """Значения режимов суждения: середины квартилей двукусочного нормального, сдвиг к среднему = центр (§3.1)."""
    z90 = _N.inv_cdf(0.9)
    sl, sr = (c - a) / z90, (b - c) / z90
    xs = []
    for k in range(1, 5):
        u = (2 * k - 1) / 8
        xs.append(c + (sl if u <= 0.5 else sr) * _N.inv_cdf(u))
    d = c - sum(xs) / 4
    return {rho: x + d for rho, x in zip(REGIMES, xs)}


# ================================================================================================ факты

class Facts:
    def __init__(self, folder: Path = FACTS_DIR, extra: Path | None = CONTROL_FACTS):
        load = lambda n: json.loads((folder / n).read_text(encoding="utf-8"))
        self.seg = load("segments.json")
        self.grp = load("group.json")
        self.bal = load("balance.json")
        self.eq = load("equity.json")
        self.mac = load("macro.json")
        # строки книги 1.3, снятые вторым счётом с первички (`tests/control_facts.json`). Баланс: лист главнее, эти —
        # вместо недостающих (`extra_used`). Движение денег активов моста второй счёт берёт только своё (`ctrl_cash`,
        # величины без знака): лист ядра пишет те же строки со знаком ОДДС — сверка с ним в тесте
        self.extra = json.loads(extra.read_text(encoding="utf-8")) if extra and extra.exists() else {}
        self.extra_used = []
        for date, rows in self.extra.get("balance", {}).items():
            node = self.bal.setdefault(date, {})
            for k, v in rows.items():
                if k not in node:
                    node[k] = v
                    self.extra_used.append(f"balance.{date}.{k}")
        self.ctrl_cash = self.extra.get("cash_2026", {})

    # сегменты: A–G, F_at, F_o, X, group
    def r(self, b: str, p: str) -> float:
        return val(self.seg["blocks"][b]["revenue"][p])

    def e(self, b: str, p: str) -> float:
        return val(self.seg["blocks"][b]["adj_ebitda"][p])

    def rs(self, b: str, ps) -> float:
        return sum(self.r(b, p) for p in ps)

    def es(self, b: str, ps) -> float:
        return sum(self.e(b, p) for p in ps)

    def gtv(self, b: str, p: str) -> float:
        return val(self.seg["gtv"][b][p])

    def g(self, metric: str, p: str) -> float:
        return val(self.grp[metric][p])

    def gs(self, metric: str, ps) -> float:
        return sum(self.g(metric, p) for p in ps)

    def b(self, date: str, key: str) -> float:
        return val(self.bal[date][key])

    def cpi(self, p: str) -> float:
        return val(self.mac["cpi_yoy"][p]) / 100

    def has(self, date: str, key: str) -> bool:
        return key in self.bal.get(date, {})

    def r_exfs(self, ps) -> float:
        """R°(P) = R(P) × Σ_{A..F} R_b(P) / Σ_{A..G} R_b(P) — выручка без финсервисов за период P (§0); P —
        один период или список периодов, суммы — за весь P."""
        ps = [ps] if isinstance(ps, str) else list(ps)
        a_f = sum(self.rs(b, ps) for b in BLOCKS if b != "G")
        return self.rs("group", ps) * a_f / (a_f + self.rs("G", ps))

    def cx_hist(self, y: int, h: int) -> float:
        """Вложения полугодия до якоря: капвложения ОС и НМА + поступления права пользования машин, земли и
        прочего; нераскрытые полугодия — плоско назад тем же полугодием первого раскрытого года части (§4.9)."""
        cap = self.grp["capex"]
        rou = self.grp["rou_additions_nonpremises"]
        return _flat_back(cap, y, h) + _flat_back(rou, y, h)

    def with_intragroup(self, eff: dict) -> "Facts":
        """Факты одного состава (§0.5, книга 1.4): для каждого квартала q с эффектом e(q) и каждого периода факта
        P ∋ q (квартал, полугодие, год) — R_A, E_A минус e(q); R_X, E_F, E_o плюс e(q). Группа не меняется."""
        G = copy.copy(self)
        G.seg = copy.deepcopy(self.seg)
        G.__dict__.pop("_composed", None)
        blocks = G.seg["blocks"]
        for b, metric, sign in (("A", "revenue", -1), ("A", "adj_ebitda", -1), ("X", "revenue", 1),
                                ("F", "adj_ebitda", 1), ("F_o", "adj_ebitda", 1)):
            series = blocks[b][metric]
            for per in list(series):
                e = sum(float(v) for q, v in eff.items() if str(q) in quarters_in(per))
                if e:
                    series[per] = val(series[per]) + sign * e
        return G


def quarters_in(period: str) -> tuple:
    """Кварталы периода факта: квартал — он сам; полугодие — два квартала; год — четыре; прочие ключи — ни одного."""
    if len(period) == 6 and period[4] == "Q":
        return (period,)
    if len(period) == 6 and period[4] == "H":
        y, h = half(period)
        return (f"{y}Q{2 * h - 1}", f"{y}Q{2 * h}")
    if len(period) == 4 and period.isdigit():
        return tuple(f"{period}Q{i}" for i in (1, 2, 3, 4))
    return ()


def one_composition(F: Facts, P: dict) -> Facts:
    """Факты в одном составе внутригрупповых расчётов (§0.5, ключ `growth.A.intragroup_2025`); без ключа — как в
    отчётности (книга 1.3). Копия фактов — одна на эффект (память в исходных фактах)."""
    eff = dget(P, "growth.A.intragroup_2025", None)
    if not eff:
        return F
    key = tuple(sorted((str(k), float(v)) for k, v in eff.items()))
    if getattr(F, "_intragroup", None) is not None:          # уже в одном составе — второй раз не сдвигаем
        if F._intragroup != key:
            raise ValueError("факты уже в одном составе с другим эффектом (§0.5)")
        return F
    cache = F.__dict__.setdefault("_composed", {})
    if key not in cache:
        cache[key] = F.with_intragroup(dict(key))
        cache[key]._intragroup = key
    return cache[key]


def _flat_back(series: dict, y: int, h: int) -> float:
    years = sorted({int(k[:4]) for k in series if len(k) == 6 and k[4] == "H"})
    yy = y
    while label(yy, h) not in series:
        if yy >= years[-1]:
            raise KeyError(f"нет полугодия {label(y, h)}")
        yy += 1
    return val(series[label(yy, h)])


# ================================================================================================ миры

def worlds_from_record(path: Path = WORLDS_RECORD) -> dict:
    """Проекция записи миров в ключи книги (ПРОЧТЕНИЕ 1): ключевая — key_avg, ИПЦ — cpi_yoy_avg, кривая —
    fair_zero_curve_today с LT = curve_lt, долгосрочная инфляция — lt_inflation; веса «своего взгляда» —
    probabilities; дата кривой — asof записи."""
    rec = json.loads(path.read_text(encoding="utf-8"))
    worlds = {}
    for w in WORLDS:
        rows = [r for r in rec["rows"] if r["world"] == w]
        worlds[w] = {
            "key_rate": {r["period"]: r["key_avg"] / 100 for r in rows},
            "cpi": {r["period"]: r["cpi_yoy_avg"] / 100 for r in rows},
            "zero_curve": {**{k: v / 100 for k, v in rec["fair_zero_curve_today"][w].items()},
                           "LT": rec["curve_lt"][w] / 100},
            "lt": {"inflation": rec["lt_inflation"][w] / 100},
        }
    return {"worlds": worlds, "joint": {"world_prob": dict(rec["probabilities"])},
            "meta": {"curve_as_of": rec["asof"]}}


# ================================================================================================ перевывод (§17)

def tau_bar(P: dict, y: int | None) -> float:
    s = P["tax"]["it_share"]
    it = traj_lt(P["tax"]["it_rate"]) if y is None else traj(P["tax"]["it_rate"], y, 1)
    return s * it + (1 - s) * P["tax"]["general"]


def tax_base(e_blocks: dict, da: float, e_x: float, e_atk: float = 0.0, e_o: float | None = None) -> float:
    """База налога §4.11: EBITDA прибыльных блоков − вся амортизация + исключения + атаки. `e_o` нет (книга 1.1) —
    F одним блоком в `e_blocks`; `e_o` дан (ключ `tax.base_rule: f_other`, книга 1.2) — F в `e_blocks` нет,
    расходы «прочих» F уменьшают базу, автономные технологии — нет."""
    base = sum(v for b, v in e_blocks.items() if v > 0) - da + e_x + e_atk
    return base if e_o is None else base + e_o


def f_other_rule(J: dict) -> bool:
    return dget(J, "tax.base_rule", None) == "f_other"


def fact_tax_base(F: Facts, J: dict, ps, da: float) -> float:
    """§4.11 на фактах за период ps одним периодом (суммы периодов); книга 1.2 — с расходами «прочих» F."""
    if f_other_rule(J):
        return tax_base({b: F.es(b, ps) for b in FLOW}, da, F.es("X", ps), e_o=F.es("F_o", ps))
    return tax_base({b: F.es(b, ps) for b in BLOCKS}, da, F.es("X", ps))


def general_pg(J: dict, e_blocks: dict) -> float:
    """pg — прибыль блоков общей ставки (§4.11, книга 1.4, ключ `tax.general_blocks`): Σ_{b ∈ список ∩ П} E_b при
    `tax.general_blocks_da: none`; без ключа — 0 (книга 1.3). Вариант с долей амортизации второй счёт не считает: в
    книге 1.4 его нет."""
    gb = dget(J, "tax.general_blocks", None) or []
    if not gb:
        return 0.0
    if dget(J, "tax.general_blocks_da", None) != "none":
        raise ValueError("tax.general_blocks_da: второй счёт считает только none (книга 1.4)")
    return sum(e_blocks[b] for b in gb if b in e_blocks and e_blocks[b] > 0)


def tax_with_pg(J: dict, rate: float, base: float, pg: float) -> float:
    """tax_u = τ̄ × max(0, base − pg) + τ_g × max(0, pg) (§4.11, книга 1.4); pg = 0 — τ̄ × max(0, base)."""
    if not pg:
        return rate * max(0.0, base)
    return rate * max(0.0, base - pg) + J["tax"]["general"] * max(0.0, pg)


def fact_pg(F: Facts, J: dict, ps) -> float:
    """pg на фактах за период ps одним периодом: прибыльный блок — по сумме периода (§4.11)."""
    return general_pg(J, {b: F.es(b, ps) for b in BLOCKS})


def derive(F: Facts, J: dict) -> tuple[dict, dict]:
    """Выведенные ключи книги по правилам §17 из фактов. Возвращает (ключи, промежуточные числа)."""
    D: dict = {}
    T: dict = {}
    # книга 1.4 (§0.5, ключ `growth.A.intragroup_2025`): все факты — в одном составе внутригрупповых расчётов
    F = one_composition(F, J)
    if getattr(F, "_intragroup", None):
        T["intragroup_2025"] = dict(F._intragroup)
    alpha = J["growth"]["A"]["autoru_share"]
    ex_fs = dget(J, "meta.balance_base", None) == "ex_fs"
    r_base = (lambda ps: F.r_exfs(ps)) if ex_fs else (lambda ps: F.rs("group", [ps] if isinstance(ps, str) else ps))

    # --- 2П2026 (§4.2)
    cpi_q3 = F.cpi("2026Q3")
    b_by_revenue = dget(J, "path_2026h2.b_growth", None) == "revenue"       # книга 1.2 (В3)
    a_multiply = dget(J, "growth.A.autoru_form", None) == "multiply"        # книга 1.2 (В15)
    rg, mg = {}, {}
    for b in FLOW:
        if b == "B" and not b_by_revenue:
            g = F.gtv("B", "2026Q3") / F.gtv("B", "2025Q3") - 1
        else:
            g = F.r(b, "2026Q3") / F.r(b, "2025Q3") - 1
        T[f"growth_2026Q3.{b}"] = g
        if b == "A" and a_multiply:
            rg[b] = (1 + g) / (1 - alpha) / (1 + cpi_q3) - 1
        else:
            rg[b] = ((1 + g + alpha) if b == "A" else (1 + g)) / (1 + cpi_q3) - 1
        m = lambda p: F.e(b, p) / F.r(b, p)
        mg[b] = m("2026Q3") + (m("2025H2") - m("2025Q3"))
    dset(D, "path_2026h2.real_growth", rg)
    dset(D, "path_2026h2.margin", mg)
    dset(D, "path_2026h2.f_ebitda", 2 * F.e("F", "2026Q3"))

    # --- рост A 2027 г. (§17): ½ × (собственный без Авто.ру + рекламный)
    # Книга 1.1 (ПРОЧТЕНИЕ 4): ИПЦ «12 мес.» — среднее четырёх квартальных г/г 4К25–3К26. Книга 1.2 (ключ
    # `growth.A.cpi_12m: monthly`, §0.3): факт `macro.cpi_yoy.12m_2026Q3` — по месячному ряду. ПРОЧТЕНИЕ 26: факта
    # нет (факты до правки) — прежнее правило и флаг `cpi_12m.fallback` (отличие ≈ 0,005 п.п.).
    if dget(J, "growth.A.cpi_12m", None) == "monthly" and CPI_12M in F.mac["cpi_yoy"]:
        cpi12 = F.cpi(CPI_12M)
    else:
        cpi12 = sum(F.cpi(q) for q in Q12) / 4
        if dget(J, "growth.A.cpi_12m", None) == "monthly":
            T["cpi_12m.fallback"] = f"нет факта macro.cpi_yoy.{CPI_12M}: среднее кварталов"
            print(f"второй счёт: нет факта macro.cpi_yoy.{CPI_12M} — ИПЦ 12 мес. средним кварталов (флаг)",
                  file=sys.stderr)
    own_nom = F.rs("A", Q12) * (1 - alpha * 8 / 12) / (F.rs("A", Q12_PREV) * (1 - alpha)) - 1
    own = (1 + own_nom) / (1 + cpi12) - 1
    ad = F.mac["ad_market_internet_yoy"]
    akar = (1 + val(ad["akar_2026Q1"]) / 100) / (1 + F.cpi("2026Q1")) - 1
    arir = (1 + val(ad["arir_2026H1"]) / 100) / (1 + F.cpi("2026H1")) - 1
    T.update({"cpi_12m": cpi12, "a27_own_nominal": own_nom, "a27_own_real": own, "a27_akar_real": akar,
              "a27_arir_real": arir})
    dset(D, "growth.A.y2027_real", 0.5 * (own + 0.5 * (akar + arir)))

    # --- режимы роста (§3.1): центр 0, края — суждения
    shifts = {}
    for b in FLOW:
        rng = J["growth"]["shift_range"].get(b)
        shifts[b] = regime_values(0.0, rng[0], rng[1]) if rng else {rho: 0.0 for rho in REGIMES}
    dset(D, "growth.shift", shifts)

    # --- цели маржи: центр и края (§17), режимы (§3.1)
    gtv_r_b = sum(F.gtv("B", q) for q in Q12) / F.rs("B", Q12)
    gtv_r_c = sum(F.gtv("C", q) for q in Q12) / F.rs("C", Q12)
    T.update({"gtv_to_revenue_B_12m": gtv_r_b, "gtv_to_revenue_C_12m": gtv_r_c})
    # книга 1.3 (Г7, ключ `margin.it_contrib_4q25`, §4.4): EBITDA «12 мес.» A, B и «прочих» F — минус доли x_b добавки
    # взносов IT IV кв. 2025 г. по ставке 2026 г.; без ключа — факт «12 мес.» (книга 1.2)
    ic = dget(J, "margin.it_contrib_4q25", None)
    e12 = {b: F.es(b, Q12) for b in ("A", "B", "F_o")}
    if ic is not None:
        e12 = {b: e12[b] - ic[b] * ic["amount"] for b in e12}
        T.update({f"it_contrib.{b}": ic[b] * ic["amount"] for b in ("A", "B", "F_o")})
    center = {"A": e12["A"] / F.rs("A", Q12),
              "B": (e12["B"] / sum(F.gtv("B", q) for q in Q12)) * gtv_r_b,
              "C": 0.5 * J["margin"]["C"]["ozon_trade_gmv"] * gtv_r_c}
    for b in ("D", "E", "G"):
        center[b] = J["margin"]["center"][b]
    mrange = {b: list(J["margin"]["range"][b]) for b in ("A", "C", "D", "E", "G")}
    mrange["B"] = [x * gtv_r_b for x in J["margin"]["B"]["range_gtv"]]
    dset(D, "margin.center", center)
    dset(D, "margin.range", mrange)
    dset(D, "margin.targets", {b: regime_values(center[b], *mrange[b]) for b in FLOW})

    # --- сезон маржи: m(H1) − m(H2), среднее полных лет окна
    gap = {}
    for b in FLOW:
        m = lambda p: F.e(b, p) / F.r(b, p)
        gap[b] = sum(m(f"{y}H1") - m(f"{y}H2") for y in FULL_YEARS) / len(FULL_YEARS)
    dset(D, "margin.season_gap", gap)

    # --- F, исключения, банк, выплаты акциями
    r12 = F.rs("group", Q12)
    T["revenue_group_12m"] = r12
    dset(D, "f.revenue_share", F.rs("F", Q12) / r12)
    dset(D, "f.other_share", e12["F_o"] / r12)
    blocks_window = sum(F.rs(b, WINDOW_Q) for b in BLOCKS)
    xr_q = Q12 if dget(J, "excl.revenue_period", None) == "ltm" else WINDOW_Q    # книга 1.2 (В4): «12 мес.»
    dset(D, "excl.revenue_share", -F.rs("X", xr_q) / sum(F.rs(b, xr_q) for b in BLOCKS))
    dset(D, "excl.ebitda_share", F.es("X", WINDOW_Q) / blocks_window)
    dset(D, "bank.capital_to_revenue", F.b("2026-09-30", "bank_capital_f123") / F.rs("G", Q12))
    dset(D, "sbc.share", sum(F.g("sbc", q) for q in Q12) / r12)
    sbc_years = tuple(dget(J, "sbc.h1_years", None) or FULL_YEARS)              # книга 1.2 (В15): 2025 г.
    dset(D, "sbc.h1_share", sum(F.g("sbc", f"{y}H1") for y in sbc_years) / sum(F.g("sbc", str(y)) for y in sbc_years))

    # --- аренда в вложениях (ℓ): поступления права пользования машин, земли и прочего за 12 мес. до якоря
    r_lease = F.r("group", "2025H2") + F.r("group", "2026H1")
    dset(D, "capex.lease_share", val(F.grp["rou_additions_nonpremises"]["ltm_2026H1"]) / r_lease)

    # --- уровень вложений окна для базового κ (решение архитектора, вопрос 1 этапа 2в): «среднее 2025 г. и 9М26» —
    # среднее двух долей капвложений в ОС и НМА (без новой аренды) в выручке своего периода. ПРОЧТЕНИЕ 19: решение
    # печатает 9,95 % (среднее округлённых 10,1 и 9,8 %); по фактам — 9,96 %.
    # Книга 1.2: доли — к R° (В1), второй период — «12 мес.» 2025Q4–2026Q3 (В5, ключ `capex.kappa_window: ltm`).
    cap = F.grp["capex"]
    if not ex_fs:
        sh25 = val(cap["2025"]) / F.r("group", "2025")
        sh9m = (val(cap["2026H1"]) + val(cap["2026Q3"])) / (F.r("group", "2026H1") + F.r("group", "2026Q3"))
    else:
        sh25 = val(cap["2025"]) / r_base("2025")
        sh9m = (val(cap["2026H1"]) + val(cap["2026Q3"])) / r_base(["2026H1", "2026Q3"])
    T.update({"capex_share.2025": sh25, "capex_share.9M26": sh9m})
    if dget(J, "capex.kappa_window", None) == "ltm":
        sh12 = sum(val(cap[q]) for q in Q12) / r_base(Q12)
        T["capex_share.12m"] = sh12
        dset(D, "capex.kappa_base_capex", 0.5 * (sh25 + sh12))
    else:
        dset(D, "capex.kappa_base_capex", 0.5 * (sh25 + sh9m))

    # --- срок амортизации L (§17; решение архитектора): сроки классов МСФО, взвешенные по первоначальной стоимости
    cls = F.bal["asset_classes"]
    num = den = 0.0
    for group_key in ("ppe_2026-06-30", "intangibles_2025-12-31"):
        for name, c in cls[group_key].items():
            if "life_min" not in c:          # незавершённые и предоплаты не амортизируются
                continue
            gross, life = val(c["gross"]), 0.5 * (val(c["life_min"]) + val(c["life_max"]))
            num += gross * life
            den += gross
    L_raw = num / den
    L = math.floor(2 * L_raw + 0.5) / 2
    T["asset_life_weighted"] = L_raw
    dset(D, "capex.asset_life_years", L)
    # сверка: амортизация правила когорт на фактах вложений против факта (полные годы окна и 9М26)
    twol = int(round(2 * L))
    rule = lambda y, h: sum(F.cx_hist(*shift_half(y, h, -j)) for j in range(1, twol + 1)) / twol
    for y in FULL_YEARS:
        T[f"da_check.{y}"] = (rule(y, 1) + rule(y, 2)) / F.g("da_ex_office", str(y)) - 1
    fact_9m = F.g("da_ex_office", "2026H1") + F.g("da_ex_office", "2026Q3")
    T["da_check.9M26"] = (rule(2026, 1) + rule(2026, 2) / 2) / fact_9m - 1

    # --- оборотный капитал без банка
    # Книга 1.2: к R°_ann = R°(p) + R°(p − 1), R° каждого полугодия — по его фактам (§4.10); NWC — факт минус
    # вычеты `nwc.less_bridge` на эту дату (на 31.12.2025 остатка за Авто.ру нет).
    if not ex_fs:
        dset(D, "nwc.share_h1", F.b("2026-06-30", "nwc_nonbank") / (F.r("group", "2026H1") + F.r("group", "2025H2")))
        dset(D, "nwc.share_h2", F.b("2025-12-31", "nwc_nonbank") / (F.r("group", "2025H2") + F.r("group", "2025H1")))
    else:
        dset(D, "nwc.share_h1", nwc_fact(F, J, "2026-06-30") / (r_base("2026H1") + r_base("2025H2")))
        dset(D, "nwc.share_h2", nwc_fact(F, J, "2025-12-31") / (r_base("2025H2") + r_base("2025H1")))

    # --- доля базы по IT-ставке s (§17)
    # ПРОЧТЕНИЕ 5: «разница в ставках» в фактах — со знаком минус (уменьшает налог); s берётся по модулю.
    # Книга 1.4 (§4.11, ключ `tax.general_blocks`): знаменатель — по base(p) − pg(p), банк платит общую ставку.
    base_sum, diff_sum = 0.0, 0.0
    for p in ("2025H1", "2025H2", "2026H1"):
        base = fact_tax_base(F, J, [p], F.g("da_ex_office", p))
        pg = fact_pg(F, J, [p])
        T[f"tax_base_rule.{p}"] = base
        if pg:
            T[f"tax_pg.{p}"] = pg
        base_sum += max(0.0, base - pg)
        diff_sum += F.g("tax_rate_diff", p)
    it_now = traj(J["tax"]["it_rate"], 2025, 1)
    s = -diff_sum / ((J["tax"]["general"] - it_now) * base_sum)
    dset(D, "tax.it_share", s)

    # --- отдача капитала окна (§17; решение архитектора): NOPAT 12 мес. / FA(якорь), без оборотного капитала
    # Налог — §4.11 на суммах 12 мес. одним периодом, τ̄ года наблюдения. Книга 1.3: E — минус добавка взносов x
    # целиком (Г7); налог окна — §4.11 от EBITDA блоков «12 мес.», поправленных долями x_A, x_B, x_Fo (у F — в блоке F;
    # с `tax.base_rule: f_other` — в E_o), остаток добавки базу не меняет (§4.4, 8b6f1f8; было ПРОЧТЕНИЕ 30); с ключом
    # `checks.roic_tax: general` налог — τ_g × max(0, база) (Г4).
    eg12 = F.es("group", Q12) - (ic["amount"] if ic is not None else 0.0)
    da12 = sum(F.g("da_ex_office", q) for q in Q12)
    if ic is None:
        base12 = fact_tax_base(F, J, Q12, da12)
    else:
        x_ = {b: ic[b] * ic["amount"] for b in ("A", "B", "F_o")}
        if f_other_rule(J):
            eb = {b: F.es(b, Q12) - x_.get(b, 0.0) for b in FLOW}
            base12 = tax_base(eb, da12, F.es("X", Q12), e_o=F.es("F_o", Q12) - x_["F_o"])
        else:
            eb = {b: F.es(b, Q12) - x_.get(b, 0.0) for b in BLOCKS}
            eb["F"] = F.es("F", Q12) - x_["F_o"]
            base12 = tax_base(eb, da12, F.es("X", Q12))
        T["tax_base_12m.fact"] = fact_tax_base(F, J, Q12, da12)
    it_obs = traj(J["tax"]["it_rate"], int(Q12[-1][:4]), 1)
    # книга 1.4 (§4.11): блоки общей ставки — отдельно (при τ_g с обеих сторон — то же число, пока base ≥ pg ≥ 0)
    pg12 = fact_pg(F, J, Q12)
    if dget(J, "checks.roic_tax", None) == "general":
        tax12 = tax_with_pg(J, J["tax"]["general"], base12, pg12)
    else:
        tax12 = tax_with_pg(J, s * it_obs + (1 - s) * J["tax"]["general"], base12, pg12)
    nopat12 = eg12 - sum(F.g("sbc", q) for q in Q12) - da12 - tax12
    fa0 = fixed_assets(F, J["meta"]["facts_date"])
    T.update({"nopat_12m": nopat12, "tax_12m": tax12, "fa_anchor": fa0})
    dset(D, "terminal.roic", nopat12 / fa0)

    # --- спред долга σ_d (§17; решение архитектора): среднее спредов купона флоатеров к ключевой
    fl = [val(F.bal["floater_spread"][k]) / 100 for k in J["financing"]["floaters"]]
    dset(D, "financing.spread", sum(fl) / len(fl))

    # --- β активов (§5, §17)
    beta_u, tb = derive_beta(F, J)
    T.update(tb)
    dset(D, "valuation.beta_u", beta_u)
    return D, T


def _friday(d: dt.date) -> dt.date:
    """Конец календарной недели (пн–вс) — её пятница."""
    return d + dt.timedelta(days=4 - d.weekday())


def derive_beta(F: Facts, J: dict, market_path: Path | None = None, skip_mode: str = "drop_returns") -> tuple[float, dict]:
    """β активов по правилу семейства (§17): недели к MCFTR, окно, склейка YNDX → YDEX, Харрис — Пингл.

    Шаг склейки (§17, п. 3): выбрасываются недельные доходности недель `skip_week_ends` у обоих рядов, цены не
    удаляются — доходность недели 02.08.2024 считается от цены 26.07.2024 (`drop_returns`). Прежнее прочтение
    `drop_points` (цены недель склейки убраны, одна доходность через склейку) оставлено для сравнения."""
    mk = json.loads((market_path or ROOT / "data" / "market" / "beta_inputs.json").read_text(encoding="utf-8"))
    rule = J["valuation"]["beta_rule"]
    md = dt.date.fromisoformat(str(J["meta"]["date"]))
    d_end = md - dt.timedelta(days=(md.weekday() - 4) % 7)          # последняя пятница не позже даты книги
    d_start = dt.date(d_end.year - int(rule["window_years"]), d_end.month, d_end.day)
    s_from, s_to = (dt.date.fromisoformat(x) for x in rule["skip_week_ends"])

    def weekly(rows):
        out = {}
        for row in sorted(rows, key=lambda r: r[0]):
            d = dt.date.fromisoformat(row[0])
            out[_friday(d)] = row[-1]
        return out
    stock = weekly(mk["stock"])
    index = weekly(mk["index"])
    divs = [(dt.date.fromisoformat(d["record_date"]), val(d["per_share_rub"])) for d in mk["dividends"]]
    skip = lambda w: s_from <= w <= s_to
    if skip_mode == "drop_points":
        weeks = sorted(w for w in set(stock) & set(index) if not skip(w))
        pairs = list(zip(weeks, weeks[1:]))
    else:
        weeks = sorted(set(stock) & set(index))
        pairs = [(a, b) for a, b in zip(weeks, weeks[1:]) if (b - a).days == 7 and not skip(b)]
    rs, rm, dv = [], [], []
    nd_dates = sorted(k for k in F.bal if k[:2] == "20" and "net_debt_company" in F.bal[k])
    sh_dates = sorted(k for k in F.eq if k[:2] == "20")
    last_at = lambda dates, w: next((d for d in reversed(dates) if dt.date.fromisoformat(d) <= w), dates[0])
    for a, b in pairs:
        if not (d_start < b <= d_end):
            continue
        dps = sum(x for r, x in divs if a < r <= b)
        rs.append((stock[b] + dps) / stock[a] - 1)
        rm.append(index[b] / index[a] - 1)
        debt = net_debt_nonbank(F, last_at(nd_dates, b))
        eq = val(F.eq[last_at(sh_dates, b)]["shares_outstanding"]) * stock[b] / 1000
        dv.append(debt / (debt + eq))
    n = len(rs)
    ms, mm = sum(rs) / n, sum(rm) / n
    cov = sum((x - ms) * (y - mm) for x, y in zip(rs, rm))
    var = sum((y - mm) ** 2 for y in rm)
    beta_e = cov / var
    w_debt = sum(dv) / n
    beta_u = beta_e * (1 - w_debt) + rule["debt_beta"] * w_debt
    return beta_u, {"beta.weeks": n, "beta.equity": beta_e, "beta.debt_to_value": w_debt,
                    "beta.window": f"{d_start}..{d_end}", "beta.skip_mode": skip_mode}


def fixed_assets(F: Facts, date: str) -> float:
    """FA: ОС + НМА без гудвила + право пользования без помещений (§4.12). ПРОЧТЕНИЕ 10: НМА без гудвила —
    «прочие НМА» баланса; контентные активы — в оборотном капитале (§4.10), не в FA."""
    return (F.b(date, "bs_ppe") + F.b(date, "bs_intangibles_other")
            + F.b(date, "rou_total") - F.b(date, "rou_premises"))


INCOME_TAX_PAYABLE, INCOME_TAX_PREPAID = "bs_income_tax_payable", "oca_income_tax_prepaid"


def nwc_fact(F: Facts, J: dict, date: str) -> float:
    """Оборотный капитал без банка на дату: факт минус строки моста `nwc.less_bridge`, которые на эту дату есть
    (§4.10, книга 1.2); без ключа — факт."""
    less = dget(J, "nwc.less_bridge", None) or []
    nwc = F.b(date, "nwc_nonbank") - sum(F.b(date, k) for k in less if F.has(date, k))
    if dget(J, "nwc.income_tax_place", None) == "nwc":   # §4.10 (40f36e7): налог к уплате и авансы — в оборотном
        nwc = nwc - F.b(date, INCOME_TAX_PAYABLE) + F.b(date, INCOME_TAX_PREPAID)
    return nwc


def net_debt_nonbank(F: Facts, date: str) -> float:
    """ND без банка (§7.1): ND компании + деньги банка, деньги банка = L_FS + K_bank − Loans_FS − Sec_FS."""
    cash_bank = (F.b(date, "fs_liabilities") + F.b(date, "bank_capital_f123")
                 - F.b(date, "loans_customers") - F.b(date, "debt_securities"))
    return F.b(date, "net_debt_company") + cash_bank


# ================================================================================================ уровни κ (§17)

def _with_capex(P: dict, **kw) -> dict:
    """Книга с другими значениями `capex.*` (уровни κ, δ) — без глубокой копии: клетка книгу не меняет."""
    Q = dict(P)
    Q["capex"] = dict(P["capex"], **kw)
    return Q


def world_weights(P: dict) -> dict:
    """Веса миров вывода κ, δ, доли I полугодия и ожиданий гейтов: слой «свой взгляд» P(W); с ключом
    `joint.expectation: headline` (книга 1.4, §4.9) — веса заголовка P_H(W) = λ × P(W) + (1 − λ) × [W = W_n]."""
    wp = P["joint"]["world_prob"]
    if dget(P, "joint.expectation", None) != "headline":
        return wp
    lam, wn = P["joint"]["lambda"], P["joint"]["neutral_world"]
    return {w: lam * wp[w] + (1 - lam) * (1.0 if w == wn else 0.0) for w in WORLDS}


def h1_share_target(P: dict, F: Facts) -> tuple[float, dict]:
    """T = `capex.h1_share` (§4.9, §17; книга 1.3, Г2): средняя доля I полугодия во вложениях CX (капвложения + новые
    договоры аренды) — полные годы окна и год якоря; h(2026) — факт 2026H1 к факту 2026H1 + ожидание CX_W(2026H2)
    правила s по мирам слоя «свой взгляд» (книга 1.4 — веса заголовка; клетка (W, base, base): p_1 от режима и уровня
    не зависит)."""
    ay, _ = half(P["meta"]["anchor_period"])
    wp = world_weights(P)
    hs = {}
    for y in FULL_YEARS:
        a, b = F.cx_hist(y, 1), F.cx_hist(y, 2)
        hs[y] = a / (a + b)
    cx_h1 = F.cx_hist(ay, 1)
    Q = _with_capex(P, h1_dip=0.0)                 # p_1 (2026H2) — правило s: от δ и уровня κ не зависит
    cx_h2 = sum(wp[w] * cell(Q, F, w, "base", "base", kappa_c=0.0)["group"]["cx"][0] for w in WORLDS)
    hs[ay] = cx_h1 / (cx_h1 + cx_h2)
    return sum(hs.values()) / len(hs), {f"h1_share.{y}": v for y, v in hs.items()} | {"h1_share.cx_2026H2": cx_h2}


def solve_h1_dip(P: dict, F: Facts, levels: dict, target: float) -> tuple[float, float]:
    """δ (§4.9, §17; книга 1.3, Г2): H(δ) = T, H — средняя за 2027–`levels_to` доля I полугодия во вложениях CX
    ожидаемого пути клеток (W, base, base) с весами P(W) (книга 1.4 — P_H(W)); бисекция на [0; 0,05] до 1e-12, нет
    смены знака — отказ."""
    wp = world_weights(P)
    years = list(range(half(P["meta"]["first_period"])[0] + 1, P["capex"]["levels_to"] + 1))

    def H(d: float) -> float:
        Q = _with_capex(P, kappa_levels=levels, h1_dip=d)
        n1 = {y: 0.0 for y in years}
        ny = {y: 0.0 for y in years}
        for w in WORLDS:
            c = cell(Q, F, w, "base", "base")
            for i, lab in enumerate(c["periods"]):
                y, h = half(lab)
                if y in ny:
                    ny[y] += wp[w] * c["group"]["cx"][i]
                    if h == 1:
                        n1[y] += wp[w] * c["group"]["cx"][i]
        return sum(n1[y] / ny[y] for y in years) / len(years)

    lo, hi = 0.0, 0.05
    if not H(hi) <= target <= H(lo):
        raise ValueError(f"δ: H(0) = {H(lo):.4%}, H(0,05) = {H(hi):.4%} — цель {target:.4%} вне (§4.9, отказ перевывода)")
    while hi - lo > 1e-12:
        mid = 0.5 * (lo + hi)
        if H(mid) > target:
            lo = mid
        else:
            hi = mid
    d = 0.5 * (lo + hi)
    return d, H(d)


def derive_kappa(P: dict, F: Facts, regimes: tuple[str, ...] = ("base",), mode: str = "mean_years") -> tuple[dict, dict]:
    """Уровни κ (и книга 1.3 — δ). Без ключа `capex.season` — `kappa_levels` при δ книги (0). С ключом (§4.9, Г2) —
    неподвижная точка: по очереди уровни κ при данном δ и δ при данных уровнях, начиная с δ книги (своя книга — 0),
    пока δ не перестанет меняться больше чем на 1e-12; не больше 50 кругов, иначе отказ."""
    if P["capex"].get("season") != "h1_dip":
        return kappa_levels(P, F, regimes, mode)
    target, th = h1_share_target(P, F)
    delta = float(P["capex"].get("h1_dip", 0.0) or 0.0)
    # Методика книги 1.4 (§4.9, 3f8e99a): уровни κ и δ после каждого круга округляются до 10 знаков, круг кончается,
    # когда округлённое δ совпало с прежним. Книга 1.3 — по своему тексту (1ea268e): шаг δ ≤ 1e-12, без округления.
    round10 = book_version(P) == "1.4"
    for rnd in range(50):
        levels, trace = kappa_levels(_with_capex(P, h1_dip=delta), F, regimes, mode)
        new, h_at = solve_h1_dip(P, F, levels, target)
        if round10:
            levels = {k: round(v, 10) for k, v in levels.items()}
            new = round(new, 10)
            done = new == delta
        else:
            done = abs(new - delta) <= 1e-12
        step, delta = abs(new - delta), new
        if done:
            break
    else:
        raise ValueError("неподвижная точка δ и уровней κ: 50 кругов без схода (§4.9, отказ перевывода)")
    trace.update(th)
    trace.update({"capex.h1_share": target, "capex.h1_dip": delta, "h1_dip.H": h_at, "h1_dip.rounds": rnd + 1,
                  "h1_dip.last_step": step})
    return levels, trace


def kappa_levels(P: dict, F: Facts, regimes: tuple[str, ...] = ("base",), mode: str = "mean_years") -> tuple[dict, dict]:
    """Уровни κ на конец `capex.levels_to` (решение архитектора, вопрос 1 этапа 2в; устав, суждение 7).

    * низкий — κ на 31.12.2026 на ожидаемом пути без роста: Σ_W P(W) FA_W(p_1) / Σ_W P(W) R12_W(p_1);
    * базовый — κ_C, при которой доля капвложений в ОС и НМА (без новой аренды) за 2027–`levels_to` на ожидаемом
      пути = уровню окна `capex.kappa_base_capex`; высокий — то же для `capex.kappa_high_capex` (11 %).
    Ожидаемый путь — клетки базового режима по мирам с весами слоя «свой взгляд» (текст решения: «в клетке базового
    режима»; §4.9 dadf779); `regimes` = все режимы с P(ρ) — прочтение методики d7cc1f3 (ПРОЧТЕНИЕ 20). Доля
    (§4.9 dadf779): D(κ) = ¼ × Σ_y d(y), d(y) = Σ_W P(W) CAPEX_W(y) / Σ_W P(W) R_W(y) — среднее долей лет (`mean_years`);
    `pooled` — отношение сумм по годам и мирам (первое прочтение второго счёта, ПРОЧТЕНИЕ 22). Корень — бисекция на
    [κ_low; 1] до 1e-12. Книга 1.4 (ключ `joint.expectation: headline`, §4.9): веса миров — P_H(W) везде здесь (κ_low,
    D(κ), D̄(D_b) через q)."""
    wp = world_weights(P)
    rp = {rho: (P["joint"]["regime_prob"][rho] if len(regimes) > 1 else 1.0) for rho in regimes}
    num = den = 0.0
    for w in WORLDS:
        k = cell(P, F, w, "base", "base", kappa_c=0.0)["kappa"]
        num += wp[w] * k["fa_first"]
        den += wp[w] * k["r12_first"]
    k_low = num / den
    y_from = half(P["meta"]["first_period"])[0] + 1
    y_to = P["capex"]["levels_to"]

    years = list(range(y_from, y_to + 1))

    def share(kc: float, rev_key: str = "revenue_exfs") -> float:
        """D(κ): доля капвложений без новой аренды к R° (книга 1.2; книга 1.1 — R° = R); `revenue` — к R."""
        cx = {y: 0.0 for y in years}
        rv = {y: 0.0 for y in years}
        for w in WORLDS:
            for rho in regimes:
                c = cell(P, F, w, rho, "base", kappa_c=kc)
                for i, lab in enumerate(c["periods"]):
                    y = int(lab[:4])
                    if y in cx:
                        cx[y] += wp[w] * rp[rho] * c["group"]["capex"][i]
                        rv[y] += wp[w] * rp[rho] * c["group"][rev_key][i]
        if mode == "pooled":
            return sum(cx.values()) / sum(rv.values())
        return sum(cx[y] / rv[y] for y in years) / len(years)

    def root(b: float) -> float:
        if clamp_low and b <= d_low:          # книга 1.3 (§4.9, 8b6f1f8): κ(d) = κ_low при d ≤ D(κ_low)
            return k_low
        lo, hi = k_low, 1.0
        if not share(lo) <= b <= share(hi):
            raise ValueError(f"доля {b:.4%} вне [{share(lo):.4%}; {share(hi):.4%}] на [κ_low; 1] (§17)")
        while hi - lo > 1e-12:
            mid = 0.5 * (lo + hi)
            if share(mid) > b:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

    clamp_low = False
    d_low = share(k_low)
    d_base = P["capex"]["kappa_base_capex"]
    mirror = P["capex"].get("kappa_high") == "mirror"
    extra = {}
    if P["capex"].get("kappa_target") == "all_cells":
        # книга 1.3 (Г3, §4.9): D_b клетки базового режима — так, что средняя доля 2027–`levels_to` по всем клеткам
        # с q = P(W) × P(ρ) × P(C | ρ) > 0 равна доле окна; высокий — зеркальный низкому: D(κ_high) = 2 D_b − D(κ_low)
        d_win = d_base
        rp_all = P["joint"]["regime_prob"]

        def dbar(d_b: float) -> float:
            lv = {"low": k_low, "base": root(d_b), "high": root(2 * d_b - d_low if mirror else P["capex"]["kappa_high_capex"])}
            Q = _with_capex(P, kappa_levels=lv)
            cx = {y: 0.0 for y in years}
            rv = {y: 0.0 for y in years}
            for w in WORLDS:
                for rho in REGIMES:
                    for lev in CAPEX_LEVELS:
                        q = wp[w] * rp_all[rho] * capex_prob(P, rho, lev)
                        if q <= 0:
                            continue
                        c = cell(Q, F, w, rho, lev)
                        for i, lab in enumerate(c["periods"]):
                            y = int(lab[:4])
                            if y in cx:
                                cx[y] += q * c["group"]["capex"][i]
                                rv[y] += q * c["group"]["revenue_exfs"][i]
            return sum(cx[y] / rv[y] for y in years) / len(years)

        # §4.9 (8b6f1f8): отрезок [max(D_окна − 0,01; D(κ_low)); D_окна + 0,01], κ(d) = κ_low при d ≤ D(κ_low) (было
        # ПРОЧТЕНИЕ 32). Бисекция считает середины; сход проверяет самопроверка (доля всех клеток = доле окна до 1e-9).
        clamp_low = True
        lo, hi = max(d_win - 0.01, d_low), d_win + 0.01
        while hi - lo > 1e-12:
            mid = 0.5 * (lo + hi)
            if dbar(mid) > d_win:
                hi = mid
            else:
                lo = mid
        d_base = 0.5 * (lo + hi)
        extra = {"kappa.window_share": d_win, "kappa.D_b": d_base, "kappa.all_cells_share": dbar(d_base)}
    # книга 1.2 (В5, ключ `capex.kappa_high: mirror`): D_high = 2 × D_base − D(κ_low) — выведенное число
    d_high = 2 * d_base - d_low if mirror else P["capex"]["kappa_high_capex"]
    levels = {"low": k_low, "base": root(d_base), "high": root(d_high)}
    trace = {"kappa.share_at_low": d_low, "kappa.target_base": d_base, "kappa.target_high": d_high,
             "kappa.share_at_base": share(levels["base"]), "kappa.share_at_high": share(levels["high"]),
             "kappa.regimes": ",".join(regimes), "kappa.mode": mode}
    trace.update(extra)
    if P["meta"].get("balance_base") == "ex_fs":     # печать перевывода (§4.9): доли уровней к выручке группы R
        trace.update({f"kappa.share_group.{lv}": share(levels[lv], "revenue") for lv in ("low", "high")})
    return levels, trace


# ================================================================================================ книга

def build_params(F: Facts, book: dict | None = None, version: str = "1.4",
                 derive_levels: bool | None = None) -> tuple[dict, dict]:
    """Ключи модели: суждения книги `version` + миры + выведенные. Книга ядра (если дана): версия — по её ключам, ключи
    правил §17 — как в ней (`take_rule_keys`), её значения заменяют совпавшие пути. `derive_levels` — выводить ли свои
    уровни κ и δ (по умолчанию — если книга их не дала: книга их всё равно заменит, а неподвижная точка книги 1.3
    долгая)."""
    if book is not None:
        version = book_version(book)
    P = deep_merge(judgments(version), worlds_from_record())
    if book is not None:
        take_rule_keys(P, book)
    if derive_levels is None:
        derive_levels = book is None or dget(book, "capex.kappa_levels", None) is None
    D, T = derive(F, P)
    P = deep_merge(P, D)
    if P["capex"].get("rule") == "kappa" and derive_levels:
        levels, tk = derive_kappa(P, F)
        P["capex"]["kappa_levels"] = levels
        if P["capex"].get("kappa_high") == "mirror":
            P["capex"]["kappa_high_capex"] = tk["kappa.target_high"]
        if P["capex"].get("season") == "h1_dip":
            P["capex"]["h1_share"] = tk["capex.h1_share"]
            P["capex"]["h1_dip"] = tk["capex.h1_dip"]
        T.update(tk)
    if book is not None:
        P, info = overlay_book(P, book)
        # режимные значения из книги не трогаем, если книга их дала; иначе — пересчёт от центра и краёв книги
        T["book"] = info
    if P["capex"].get("rule") == "kappa":
        # сверка срока амортизации по ходу ядра (§17, печать): 9М26 = DA_факт(2026H1) + DA(2026H2) / 2 против факта
        da_h2 = cell(P, F, WORLDS[0], "base", "base")["group"]["da"][0]
        T["da_check.9M26_path"] = ((F.g("da_ex_office", P["meta"]["anchor_period"]) + da_h2 / 2)
                                   / F.gs("da_ex_office", NINE_M) - 1)
    return P, T


def _leaf_paths(d: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in d.items():
        p = f"{prefix}{k}"
        if isinstance(v, dict) and not _is_leaf_dict(str(k)):
            out.extend(_leaf_paths(v, p + "."))
        else:
            out.append(p)
    return out


def _norm_range(v):
    if isinstance(v, dict):
        for lo, hi in (("low", "high"), ("lo", "hi"), ("p10", "p90"), ("min", "max")):
            if lo in v and hi in v:
                return [v[lo], v[hi]]
    return v


def overlay_book(P: dict, book: dict) -> tuple[dict, dict]:
    """Значения книги ядра по путям §17 заменяют свои (режим «на книге ядра»)."""
    out = copy.deepcopy(P)
    taken, missing = [], []
    for path in _leaf_paths(P):
        v = dget(book, path, None)
        if v is None:
            missing.append(path)
            continue
        if path.endswith("range") or ".range." in path or ".shift_range." in path:
            v = _norm_range(v)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(dget(P, path), (int, float)):
            v = float(v) if isinstance(dget(P, path), float) else v
        dset(out, path, v)
        taken.append(path)
    for path in BOOK_ONLY_KEYS:                 # ключи прежнего правила (книга 1.0) и выведенные уровни κ книги
        v = dget(book, path, None)
        if v is not None:
            dset(out, path, copy.deepcopy(v))
            taken.append(path)
    if isinstance(book.get("capex"), dict) and "rule" not in book["capex"]:
        out["capex"].pop("rule", None)          # §17: нет ключа — прежние доли уровней и c_T по отдаче окна
    if isinstance(book.get("joint"), dict) and "capex_prob_given_regime" not in book["joint"]:
        out["joint"].pop("capex_prob_given_regime", None)   # §3: нет ключа — P(C | ρ) = P(C) (книга 1.0)
    return out, {"taken": taken, "missing": missing}


BOOK_ONLY_KEYS = ("capex.levels", "capex.converge_year", "terminal.reinvestment_excludes_nwc", "terminal.roic",
                  "capex.kappa_levels", "capex.kappa_high_capex", "capex.h1_share", "capex.h1_dip")


def load_book(path: str | Path) -> dict:
    import yaml   # только в режиме --book
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ================================================================================================ клетка (§4–§7)

def cell(P: dict, F: Facts, world: str, regime: str, capex: str, asof: str | dt.date = DEFAULT_ASOF,
         kappa_c: float | None = None) -> dict:
    """Проход клетки §4–§7. `kappa_c` — уровень κ вместо уровня книги (только перевывод уровней κ, §17)."""
    F = one_composition(F, P)                 # книга 1.4 (§0.5): клетка читает только 2025H2 и позже — для порядка
    v = dt.date.fromisoformat(asof) if isinstance(asof, str) else asof
    W = P["worlds"][world]
    per = halves(P["meta"]["first_period"], P["meta"]["last_period"])
    ay, ah = half(P["meta"]["anchor_period"])
    anchor = (ay, ah)
    prev1 = shift_half(ay, ah, -1)                    # 2025H2
    alpha = P["growth"]["A"]["autoru_share"]
    cpi_memo: dict = {}

    def cpi(y: int, h: int) -> float:            # ИПЦ мира в полугодии (§0.3); память — только скорость полосы
        if (y, h) not in cpi_memo:
            cpi_memo[(y, h)] = traj(W["cpi"], y, h)
        return cpi_memo[(y, h)]

    # ---------- выручка блоков (§4.2, §4.3)
    R = {b: {anchor: F.r(b, label(*anchor)), prev1: F.r(b, label(*prev1))} for b in FLOW}
    r0 = P["path_2026h2"]["real_growth"]
    a27 = P["growth"]["A"]["y2027_real"]
    first = per[0]

    def base_path(b: str, y: int) -> float:
        rl = P["growth"]["lt_real"] + P["growth"]["lt_premium"].get(b, 0.0)
        yc = P["growth"]["converge_year"][b]
        if y >= yc:
            return rl
        if b == "A":
            return a27 + (rl - a27) * (y - 2027) / (yc - 2027)
        return r0[b] + (rl - r0[b]) * (y - ay) / (yc - ay)

    def shift(b: str, y: int) -> float:
        s = P["growth"]["shift"][b][regime]
        yc = P["growth"]["converge_year"][b]
        if y >= yc or s == 0.0:
            return 0.0
        if y <= 2030:
            return s
        return s * (yc - y) / (yc - 2030)

    real = {b: {} for b in FLOW}
    for (y, h) in per:
        for b in FLOW:
            if (y, h) == first:
                base = R[b][prev1] * ((1 - alpha) if b == "A" else 1.0)
                rr = r0[b]
            else:
                base = R[b][shift_half(y, h, -2)]
                if b == "A" and (y, h) == (ay + 1, 1):
                    base *= 1 - alpha * 5 / 6
                rr = base_path(b, y) + shift(b, y)
            real[b][(y, h)] = rr
            R[b][(y, h)] = base * (1 + rr) * (1 + cpi(y, h))

    # ---------- группа: замыкание выручки (§4.1)
    xR, xE, fR = P["excl"]["revenue_share"], P["excl"]["ebitda_share"], P["f"]["revenue_share"]
    S = {p: sum(R[b][p] for b in FLOW) for p in per}
    RG = {p: (1 - xR) * S[p] / (1 - fR * (1 - xR)) for p in per}
    RF = {p: fR * RG[p] for p in per}
    RX = {p: -xR * (S[p] + RF[p]) for p in per}
    EX = {p: xE * (S[p] + RF[p]) for p in per}
    RG[anchor] = F.r("group", label(*anchor))
    RG[prev1] = F.r("group", label(*prev1))
    # выручка без финсервисов R° (§0, §4.1; ключ `meta.balance_base: ex_fs`, книга 1.2): прогноз —
    # R − (1 − x_R) × R_G, якорь и 2025H2 — по фактам полугодия; без ключа R° = R
    ex_fs = P["meta"].get("balance_base") == "ex_fs"
    if ex_fs:
        RO = {p: RG[p] - (1 - xR) * R["G"][p] for p in per}
        RO[anchor] = F.r_exfs(label(*anchor))
        RO[prev1] = F.r_exfs(label(*prev1))
    else:
        RO = RG

    # ---------- маржа блоков (§4.2, §4.4)
    E = {b: {anchor: F.e(b, label(*anchor))} for b in FLOW}
    m = {b: {} for b in FLOW}
    for b in FLOW:
        m[b][first] = P["path_2026h2"]["margin"][b]
        E[b][first] = m[b][first] * R[b][first]
    years = sorted({y for (y, _) in per if y > ay})
    M = {b: {} for b in FLOW}
    for b in FLOW:
        M[b][ay] = (E[b][anchor] + E[b][first]) / (R[b][anchor] + R[b][first])
        target = P["margin"]["targets"][b][regime]
        ym = P["margin"]["target_year"][b]
        gap = P["margin"]["season_gap"][b]
        for y in years:
            M[b][y] = target if y >= ym else M[b][ay] + (target - M[b][ay]) * (y - ay) / (ym - ay)
            r1, r2 = R[b].get((y, 1)), R[b].get((y, 2))
            w1 = r1 / (r1 + r2)
            m[b][(y, 1)] = M[b][y] + gap * (1 - w1)
            m[b][(y, 2)] = M[b][y] - gap * w1
            E[b][(y, 1)] = m[b][(y, 1)] * r1
            E[b][(y, 2)] = m[b][(y, 2)] * r2

    # ---------- F: расход (§4.2, §4.5); ось суждения 6 — положение x на пути (`f.at.position`)
    fat = P["f"]["at"]
    eat0 = 2 * F.e("F_at", "2026Q3")
    x = float(fat.get("position", 0.0) or 0.0)
    Ec, Eat, Eo, EF = {}, {}, {}, {}
    for (y, h) in per:
        p = (y, h)
        if p == first:                       # 2026H2 — факт квартала, ось его не трогает
            Eat[p] = None
            Eo[p] = None
            EF[p] = P["path_2026h2"]["f_ebitda"]
            continue
        if y <= fat["growth_to"]:
            Ec[p] = eat0 * (1 + fat["growth_nominal"]) ** (y - ay)
        elif y <= fat["real_to"]:
            Ec[p] = Ec[shift_half(y, h, -2)] * (1 + cpi(y, h))
        elif y < fat["zero_from"]:
            Ec[p] = Ec[(fat["real_to"], h)] * (fat["zero_from"] - y) / (fat["zero_from"] - fat["real_to"])
        else:
            Ec[p] = 0.0
        if x == 0.0:
            Eat[p] = Ec[p]
        else:
            if x < 0:
                edge = -fat["perpetual_share"] * RG[p]                         # низ: вечный расход
            else:
                edge = Ec[p] if y < fat["high_zero_from"] else 0.0             # верх: ноль с high_zero_from
            Eat[p] = min(0.0, Ec[p] + abs(x) * (edge - Ec[p]))
        Eo[p] = P["f"]["other_share"] * RG[p]
        EF[p] = Eat[p] + Eo[p]

    Eatk = {p: (P["attack"]["ebitda_2026h2"] if p == first else 0.0) for p in per}
    EG = {p: sum(E[b][p] for b in FLOW) + EF[p] + EX[p] + Eatk[p] for p in per}

    # ---------- выплаты акциями (§4.8)
    sbc, q = P["sbc"]["share"], P["sbc"]["h1_share"]
    SBC = {}
    for (y, h) in per:
        year_r = (RG[(y, 1)] if (y, 1) in RG else 0.0) + RG[(y, 2)]
        SBC[(y, h)] = sbc * (q if h == 1 else 1 - q) * year_r

    # ---------- оборотный капитал и капитал банка (§4.10, §4.6)
    n1, n2 = P["nwc"]["share_h1"], P["nwc"]["share_h2"]
    kappa = P["bank"]["capital_to_revenue"]
    NWC = {anchor: nwc_fact(F, P, P["meta"]["facts_date"])}      # книга 1.2: минус `nwc.less_bridge` (§4.10)
    dNWC, dKb = {}, {}
    RGb = {anchor: F.r("G", label(*anchor)), prev1: F.r("G", label(*prev1))}
    RGb.update({p: R["G"][p] for p in per})
    for p in per:
        pm1 = shift_half(*p, -1)
        NWC[p] = (n1 if p[1] == 1 else n2) * (RO[p] + RO[pm1])
        dNWC[p] = NWC[p] - NWC[pm1]
        dKb[p] = kappa * (RGb[p] - RGb[shift_half(*p, -2)])
    if dget(P, "bank.anchor_capital", None) == "fact":
        # книга 1.3 (Г8, §4.6): прирост капитала банка p_1 — от факта капитала на якоре (тот же факт, что в §7.1)
        dKb[first] = kappa * (RGb[first] + RGb[anchor]) - F.b(P["meta"]["facts_date"], "bank_capital_f123")

    # ---------- терминал: g, r, строки T1, T2 и доля вложений c_T (§6)
    beta_erp = P["valuation"]["beta_u"] * P["valuation"]["erp"]
    g = (1 + P["terminal"]["real_growth"]) * (1 + W["lt"]["inflation"]) - 1
    r = W["zero_curve"]["LT"] + beta_erp
    if g >= r:
        g = r - 0.0001
    L = P["capex"]["asset_life_years"]
    twol = int(round(2 * L))
    tau_lt = tau_bar(P, None)
    ly = per[-1][0]
    ell = P["capex"]["lease_share"]
    f_other = f_other_rule(P)                  # книга 1.2 (В7): расходы «прочих» F уменьшают базу налога (§4.11)

    def terminal(c: float, cx_pair: tuple | None = None) -> dict:
        rows = {}
        for hh in (1, 2):
            last = (ly, hh)
            e_blocks = {b: E[b][last] * (1 + g) for b in FLOW}
            if not f_other:
                e_blocks["F"] = EF[last] * (1 + g)
            rows[hh] = {"R": RG[last] * (1 + g), "E": EG[last] * (1 + g), "SBC": SBC[last] * (1 + g),
                        "RG_G": RGb[last] * (1 + g), "EX": EX[last] * (1 + g), "eb": e_blocks,
                        "RO": RO[last] * (1 + g), "EO": (Eo[last] * (1 + g) if f_other else None),
                        # книга 1.4 (§4.11): pg(T_h) = pg(2036Hh) × (1 + g) — при `general_blocks_da: none` то же,
                        # что Σ max(0, E_b(T_h)) блоков списка
                        "pg": general_pg(P, {b: E[b][last] for b in FLOW} | (
                            {} if f_other else {"F": EF[last]})) * (1 + g)}
        c1, c2 = (c * rows[1]["R"], c * rows[2]["R"]) if cx_pair is None else cx_pair
        da1, da2 = steady_da(c1, c2, g, L)
        nwc1 = n1 * (rows[1]["RO"] + RO[(ly, 2)])
        nwc2 = n2 * (rows[1]["RO"] + rows[2]["RO"])
        for hh, cx, da, dn in ((1, c1, da1, nwc1 - NWC[(ly, 2)]), (2, c2, da2, nwc2 - nwc1)):
            row = rows[hh]
            pi = tax_base(row["eb"], 0.0, row["EX"], e_o=row["EO"])           # база до амортизации π(T_h) (§6.3)
            base = tax_base(row["eb"], da, row["EX"], e_o=row["EO"])
            row.update({"CX": cx, "DA": da, "dNWC": dn, "dKb": kappa * (row["RG_G"] - RGb[(ly, hh)]),
                        "base": base, "pi": pi, "tax": tax_with_pg(P, tau_lt, base, row["pg"])})
            row["EBIT"] = row["E"] - row["SBC"] - da
            row["NOPAT"] = row["EBIT"] - row["tax"]
            row["FCFF"] = row["E"] - row["SBC"] - row["tax"] - cx - dn - row["dKb"]
        return rows

    rule = P["capex"].get("rule")              # §4.9, §17: ключ `kappa` — правило κ; нет ключа — прежние доли уровней
    warn = []
    if g <= 0:
        raise ValueError("g ≤ 0 — отказ книги (§6.2)")
    if rule == "kappa":
        # §6.2: Σ_h (CX − DA)(T_h) = g × κ_C × R12(2036H2); DA(T_h) = c_T × d_h — корень в замкнутом виде
        k_c = P["capex"]["kappa_levels"][capex] if kappa_c is None else kappa_c
        r12_end = RO[(ly, 1)] + RO[(ly, 2)]           # капитал — к R° (книга 1.2), CX(T_h) = c_T × R(T_h)
        unit = terminal(1.0)
        den = unit[1]["R"] + unit[2]["R"] - unit[1]["DA"] - unit[2]["DA"]
        if den <= 0:
            raise ValueError("знаменатель c_T ≤ 0 — отказ клетки (§6.2)")
        c_t = g * k_c * r12_end / den
    elif rule is None:
        roic = P["terminal"]["roic"]
        no_nwc = bool(P["terminal"].get("reinvestment_excludes_nwc", False))   # §6.2: без ключа — с ΔNWC

        def f_ct(c: float) -> float:
            t = terminal(c)
            lhs = sum(t[hh]["CX"] - t[hh]["DA"] + (0.0 if no_nwc else t[hh]["dNWC"]) for hh in (1, 2))
            return lhs - (g / roic) * sum(t[hh]["NOPAT"] for hh in (1, 2))

        if f_ct(0.0) > 0:
            c_t = 0.0
            warn.append("f(0) > 0: c_T = 0")
        elif f_ct(1.0) < 0:
            raise ValueError("f(1) < 0 — отказ клетки (§6.2)")
        else:
            lo, hi = 0.0, 1.0
            while hi - lo > 1e-12:
                mid = 0.5 * (lo + hi)
                if f_ct(mid) > 0:
                    hi = mid
                else:
                    lo = mid
            c_t = 0.5 * (lo + hi)
    else:
        raise ValueError(f"неизвестное правило вложений capex.rule = {rule!r} (§17)")
    TR = terminal(c_t)
    gordon_dip = (rule == "kappa" and P["capex"].get("season") == "h1_dip"
                  and (bool(P["terminal"].get("gordon_dip", False))
                       or P.get("_diagnostic", {}).get("gordon_kappa_season")))
    if gordon_dip:
        # книга 1.4 (ключ `terminal.gordon_dip`, §6.3; до неё — диагностика флага 2 отчёта 402): строки Гордона —
        # установившееся состояние того же правила κ с провалом: FA_0 = κ_C × R12°(2036H2), FA_1 = (κ_C − δ) ×
        # (R°(T1) + R°(2036H2)), FA_2 = κ_C × (R°(T1) + R°(T2)), CX_h − d_h(CX_1, CX_2) = FA_h − FA_{h−1}; d_h — §6.1
        # (steady_da линейна по CX) — система 2 × 2
        dip_t = P["capex"]["h1_dip"]
        fa0_t = k_c * r12_end
        fa1_t = (k_c - dip_t) * (TR[1]["RO"] + RO[(ly, 2)])
        fa2_t = k_c * (TR[1]["RO"] + TR[2]["RO"])
        u1, u2 = steady_da(1.0, 0.0, g, L), steady_da(0.0, 1.0, g, L)
        m11, m12, m21, m22 = 1 - u1[0], -u2[0], -u1[1], 1 - u2[1]
        b1, b2 = fa1_t - fa0_t, fa2_t - fa1_t
        det = m11 * m22 - m12 * m21
        TR = terminal(c_t, ((b1 * m22 - m12 * b2) / det, (m11 * b2 - m21 * b1) / det))

    # ---------- вложения, покупки, амортизация (§4.9): по порядку полугодий, когорта своего полугодия в DA не входит
    LEASE = {p: ell * RG[p] for p in per}
    MA = {p: (P["ma"]["share"] * RG[p] if p[0] <= P["ma"]["until"] else 0.0) for p in per}
    cx_anchor = F.cx_hist(*anchor)
    hist = [F.cx_hist(*shift_half(ay, ah, -j)) for j in range(0, twol + 1)]   # hist[j] = CX(якорь − j)
    s0 = sum(hist[1:twol + 1])
    da_anchor = F.g("da_ex_office", label(*anchor))
    seq = [cx_anchor]
    CX, DA, FA, KAP, FLOOR = {}, {}, {}, {}, []
    fa_prev = fixed_assets(F, P["meta"]["facts_date"])
    # книга 1.3 (Г1, ключ `capex.anchor_rest: cohort`, §4.9): остаток FA(якорь) сверх формы выбывания базы якоря и его
    # когорты — когорта со следующего полугодия, долями 1/(2L) в 2L полугодиях; без ключа остатка нет
    rest = None
    if rule == "kappa" and P["capex"].get("anchor_rest") == "cohort":
        form = sum(sum(hist[1:twol - k + 1]) for k in range(1, twol)) / s0      # Σ_{k=1..2L−1} S(k)/S(0)
        rest = fa_prev - da_anchor * form - cx_anchor
    # книга 1.4 (ключ `capex.anchor_land`, §4.9; только с остатком якоря): право пользования землёй L_0 — из остатка,
    # списание d_L за полугодие сквозным счётом k = 1, 2, … (явный участок, затем когорты терминала), пока не кончится
    land_l0 = None
    da_land = lambda k: 0.0
    if rest is not None and dget(P, "capex.anchor_land", None):
        d_l = P["capex"]["anchor_land"]["da_half"]
        land_l0 = F.b(P["meta"]["facts_date"], "rou_land")
        rest -= land_l0
        da_land = lambda k: min(d_l, max(0.0, land_l0 - d_l * (k - 1)))
    # книга 1.3 (Г2, ключ `capex.season: h1_dip`, §4.9): цель κ на конец I полугодия ниже линии на δ
    delta_h1 = P["capex"]["h1_dip"] if rule == "kappa" and P["capex"].get("season") == "h1_dip" else None
    if rule == "kappa":
        s_first, phi = P["capex"]["first_share"], P["capex"]["floor_da"]
        capex_anchor = val(F.grp["capex"][label(*anchor)])          # капвложения ОС и НМА 2026H1, без аренды
        idx = lambda y, h: 2 * y + h - 1
        n_line = idx(P["capex"]["levels_to"], 2) - idx(*first)      # K: полугодий от p_1 до Y30H2
        kappa1 = None
    else:
        k2030 = P["capex"]["levels"][capex] + ell
        yk = P["capex"]["converge_year"]
        K = lambda y: k2030 if y <= 2030 else (c_t if y >= yk else k2030 + (c_t - k2030) * (y - 2030) / (yk - 2030))
    for k, p in enumerate(per, start=1):
        sk = sum(hist[1:twol - k + 1]) if k < twol else 0.0
        DA[p] = da_anchor * sk / s0 + sum(seq[k - j] for j in range(1, min(k, twol) + 1)) / twol
        if rest is not None and k <= twol:
            DA[p] += rest / twol
        if land_l0 is not None:
            DA[p] += da_land(k)
        if rule == "kappa":
            pm1 = shift_half(*p, -1)
            r12 = RO[p] + RO[pm1]                    # κ = FA / R12 на базе R° (книга 1.2; книга 1.1 — R)
            if k == 1:
                cx = s_first * (RG[anchor] + RG[p]) - capex_anchor + LEASE[p]
            else:
                KAP[p] = kappa1 + (k_c - kappa1) * min(k - 1, n_line) / n_line
                if delta_h1 is not None and p[1] == 1:
                    cx = DA[p] + (KAP[p] - delta_h1) * r12 - fa_prev
                else:
                    cx = DA[p] + KAP[p] * r12 - fa_prev
            if cx < phi * DA[p]:
                FLOOR.append(label(*p))
                cx = phi * DA[p]
            fa_prev = fa_prev + cx - DA[p]
            if k == 1:
                kappa1 = fa_prev / r12
                KAP[p] = kappa1
        else:
            cx = K(p[0]) * RG[p]
            fa_prev = fa_prev + cx - DA[p]
        CX[p] = cx
        FA[p] = fa_prev
        seq.append(cx)
    if FLOOR:
        warn.append("граница φ × DA: " + ", ".join(FLOOR))

    # ---------- налог, поток, проценты, щит (§4.11–§4.13)
    nd0 = net_debt_nonbank(F, P["meta"]["facts_date"])
    sig = P["financing"]["spread"]
    cap_ts = P.get("_diagnostic", {}).get("t_s_cap")
    shield_cap = bool(P["financing"].get("shield_cap", False))    # книга 1.2 (ошибка 9): t_S ≤ τ_g (§4.13)
    tau_g = P["tax"]["general"]
    # E_o для базы налога (книга 1.2, §4.11): 2026H2 — 2 × факт «прочих» 3К26, дальше — e_o × R
    EO = {p: (2 * F.e("F_o", "2026Q3") if p == first else Eo[p]) for p in per} if f_other else {}
    TAX, BASE, EBIT, FCFF, I, TS, SH, NOPAT, PG = {}, {}, {}, {}, {}, {}, {}, {}, {}
    for p in per:
        eb = {b: E[b][p] for b in FLOW}
        if not f_other:
            eb["F"] = EF[p]
        BASE[p] = tax_base(eb, DA[p], EX[p], Eatk[p], e_o=EO.get(p))
        PG[p] = general_pg(P, eb)                     # книга 1.4 (§4.11): банк — по τ_g; без ключа 0
        TAX[p] = tax_with_pg(P, tau_bar(P, p[0]), BASE[p], PG[p])
        EBIT[p] = EG[p] - SBC[p] - DA[p]
        NOPAT[p] = EBIT[p] - TAX[p]
        FCFF[p] = EG[p] - SBC[p] - TAX[p] - CX[p] - MA[p] - dNWC[p] - dKb[p]
        I[p] = nd0 * (traj(W["key_rate"], *p) + sig) / 2
        TS[p] = TAX[p] / EBIT[p] if EBIT[p] > 0 else 0.0
        if shield_cap:
            TS[p] = min(TS[p], tau_g)
        if cap_ts is not None:                 # только диагностика находки (вне книги), по умолчанию нет
            TS[p] = min(TS[p], cap_ts)
        SH[p] = TS[p] * I[p]

    # ---------- дисконт и EV (§5)
    zc = W["zero_curve"]
    df0 = lambda t: (1 + zero_rate(zc, t) + beta_erp) ** (-t) if t > 0 else 1.0
    curve_as_of = dt.date.fromisoformat(str(P["meta"]["curve_as_of"]))
    delta = 0.5 * (pos(v) - pos(curve_as_of)) if v > curve_as_of else 0.0
    df = lambda t: df0(delta + t) / df0(delta)
    closed = 0
    elapsed = None
    for (y, h) in per:
        a, b_ = half_bounds(y, h)
        if b_ <= v:
            closed += 1
        elif a <= v:
            elapsed = (v - a).days / (b_ - a).days
            break
    if elapsed is None:
        raise ValueError("дата оценки вне прогнозных полугодий")
    n_p = len(per)
    pv_flow = pv_shield = 0.0
    times = {}
    for i, p in enumerate(per):
        if i < closed:
            continue
        if i == closed:
            share, t = 1 - elapsed, (1 - elapsed) * 0.25
        else:
            share, t = 1.0, (1 - elapsed) * 0.5 + (i - closed - 1) * 0.5 + 0.25
        times[p] = (share, t)
        pv_flow += FCFF[p] * share * df(t)
        pv_shield += SH[p] * share * df(t)
    t_end = (1 - elapsed) * 0.5 + (n_p - closed - 1) * 0.5
    tv = (TR[1]["FCFF"] * (1 + r) ** 0.75 + TR[2]["FCFF"] * (1 + r) ** 0.25) / (r - g)
    # Книга 1.2 (ошибка 6, ключ `terminal.da_cohort_years: H`, §6.3): первые H лет после last_period — полугодия
    # когортами §4.9 и правилом κ; Гордон — после них, с множителем ((1 + g) / (1 + r))^H.
    H = P["terminal"].get("da_cohort_years") if rule == "kappa" else None
    tail = None
    i_t = nd0 * (traj(W["key_rate"], *per[-1]) + sig) / 2
    ts_t = {hh: (TR[hh]["tax"] / TR[hh]["EBIT"] if TR[hh]["EBIT"] > 0 else 0.0) for hh in (1, 2)}
    if shield_cap:                              # §6.4: книга 1.2 — t_S(T_h) ≤ τ_g
        ts_t = {hh: min(x_, tau_g) for hh, x_ in ts_t.items()}
    s_t = {hh: ts_t[hh] * i_t for hh in (1, 2)}
    tv_s = (s_t[1] * (1 + r) ** 0.75 + s_t[2] * (1 + r) ** 0.25) / r
    # книга 1.3 (ниже порога 1, ключ `terminal.shield_cohort`, §6.4): щит первых H лет — на ставке когорт
    shield_coh = bool(H) and bool(P["terminal"].get("shield_cohort", False))
    if H:
        phi = P["capex"]["floor_da"]
        chrono = [hist[j] for j in range(twol, 0, -1)] + seq          # CX по порядку: …, якорь, 2026H2, …, 2036H2
        fa_k, ro_prev = FA[per[-1]], RO[per[-1]]
        pv_tail = pv_tail_s = 0.0
        tail = {"F": [], "DA": [], "CX": [], "FA": [], "t_S": []}
        n_exp = len(per)
        for k in range(int(round(2 * H))):
            j, hh = divmod(k, 2)
            row, s_k = TR[hh + 1], (1 + g) ** j
            da_k = sum(chrono[-i] for i in range(1, twol + 1)) / twol
            if land_l0 is not None:              # книга 1.4 (§4.9, §6.3): DA_L(n + 1 + k_T), сквозной счёт
                da_k += da_land(n_exp + 1 + k)
            ro_k = row["RO"] * s_k
            # книга 1.3 (Г2, §6.3): в полугодиях I (h = 0) цель κ_C − δ
            k_tgt = k_c - delta_h1 if (delta_h1 is not None and hh == 0) else k_c
            cx_k = max(da_k + k_tgt * (ro_k + ro_prev) - fa_k, phi * da_k)
            fa_k = fa_k + cx_k - da_k
            # книга 1.4 (§4.11): pg_k = pg(T_h) × s_k — по τ_g, остаток базы — по τ̄(LT)
            tax_k = tax_with_pg(P, tau_lt, row["pi"] * s_k - da_k, row["pg"] * s_k)
            f_k = row["E"] * s_k - row["SBC"] * s_k - tax_k - cx_k - row["dNWC"] * s_k - row["dKb"] * s_k
            pv_tail += f_k * (1 + r) ** -(0.25 + 0.5 * k)
            if shield_coh:
                ebit_k = (row["E"] - row["SBC"]) * s_k - da_k
                ts_k = tax_k / ebit_k if ebit_k > 0 else 0.0
                if shield_cap:
                    ts_k = min(ts_k, tau_g)
                pv_tail_s += ts_k * i_t * (1 + r) ** -(0.25 + 0.5 * k)
                tail["t_S"].append(ts_k)
            chrono.append(cx_k)
            ro_prev = ro_k
            for key, x_ in (("F", f_k), ("DA", da_k), ("CX", cx_k), ("FA", fa_k)):
                tail[key].append(x_)
        tail["pv"] = pv_tail
        tail["gordon"] = tv
        tv = pv_tail + tv * ((1 + g) / (1 + r)) ** H
        if shield_coh:
            tail["pv_shield"] = pv_tail_s
            tail["gordon_shield"] = tv_s
            tv_s = pv_tail_s + tv_s * (1 + r) ** -H
    pv_tv, pv_tv_s = tv * df(t_end), tv_s * df(t_end)
    ev = pv_flow + pv_shield + pv_tv + pv_tv_s

    # ---------- требования на дату оценки (§7.1)
    claims = {"net_debt_nonbank": nd0}
    bridge_rows = list(P["bridge"]["include"])
    if P["nwc"].get("income_tax_place") == "bridge":     # §7.1 (40f36e7): налог к уплате — обязательство, авансы — актив
        bridge_rows += [{"fact": INCOME_TAX_PAYABLE, "side": "liability"}, {"fact": INCOME_TAX_PREPAID, "side": "asset"}]
    for item in bridge_rows:
        name = item["fact"] if isinstance(item, dict) else item
        side = item.get("side", "asset") if isinstance(item, dict) else "asset"
        amount = F.b(P["meta"]["facts_date"], name)
        claims[f"bridge.{name}"] = -amount if side == "asset" else amount
    eqd = F.eq[P["meta"]["facts_date"]]
    # опционы делителя (§7.2): исполнимые O_v; книга 1.4 (ключ `valuation.options_recognized` ρ) — O_d = O_v + ρ × (O − O_v)
    o_v = val(eqd["options_exercisable"])
    rho_o = dget(P, "valuation.options_recognized", None)
    o_d = o_v if rho_o is None else o_v + rho_o * (val(eqd["options_outstanding"]) - o_v)
    t_w = dget(P, "bridge.net_exercise_tax", None)
    if t_w is not None:
        # книга 1.3 (Г8, §7.1): NXT = t_w × N_vested × P_рынок / 1000 — обязательство; N_vested — тот же факт, что в
        # делителе §7.2 (книга 1.3 — исполнимые опционы; книга 1.4 — опционы делителя O_d)
        claims["bridge.net_exercise_tax"] = t_w * o_d * P["meta"]["market_price"] / 1000
    roll = 0.0
    for i, p in enumerate(per):
        if i < closed:
            roll += FCFF[p] + SH[p] - I[p]
        elif i == closed:
            roll += elapsed * (FCFF[p] + SH[p] - I[p])
    claims["rollover"] = -roll
    divs = 0.0
    by_paid = bool(P["bridge"].get("dividend_paid", False))     # книга 1.2 (ошибка 8): выплата по ОДДС, если есть
    for d in F.eq["dividends"]:
        rd = d.get("record_date")
        if rd and dt.date.fromisoformat(rd) <= v and _paid_after_anchor(d, P):
            divs += val(d["paid_bn_rub"]) if by_paid and "paid_bn_rub" in d else val(d["total_bn_rub"])
    claims["dividends"] = divs
    claims["total"] = sum(claims.values())

    # ---------- делитель и цена (§7.2)
    n_eff = val(eqd["shares_outstanding"]) + P["valuation"]["net_exercise_k"] * o_d
    price = price_from_equity(ev - claims["total"], P, n_eff)

    lab = [label(*p) for p in per]
    blocks = {b: {"revenue": [R[b][p] for p in per], "ebitda": [E[b][p] for p in per]} for b in FLOW}
    blocks["F"] = {"revenue": [RF[p] for p in per], "ebitda": [EF[p] for p in per],
                   "ebitda_at": [Eat[p] for p in per], "ebitda_other": [Eo[p] for p in per]}
    blocks["X"] = {"revenue": [RX[p] for p in per], "ebitda": [EX[p] for p in per]}
    blocks = {k: blocks[k] for k in ("A", "B", "C", "D", "E", "F", "G", "X")}
    return {
        "world": world, "regime": regime, "capex_level": capex, "asof": v.isoformat(),
        "periods": lab,
        "blocks": blocks,
        "margin": {b: [m[b][p] for p in per] for b in FLOW},
        "real_growth": {b: [real[b][p] for p in per] for b in FLOW},
        "group": {
            "revenue": [RG[p] for p in per], "ebitda": [EG[p] for p in per], "attack": [Eatk[p] for p in per],
            "sbc": [SBC[p] for p in per], "da": [DA[p] for p in per], "ebit": [EBIT[p] for p in per],
            "tax_base": [BASE[p] for p in per], "tax": [TAX[p] for p in per],
            "capex": [CX[p] - LEASE[p] for p in per], "lease_new": [LEASE[p] for p in per],   # интерфейс ядра
            "cx": [CX[p] for p in per], "acq": [MA[p] for p in per],                         # CX = capex + lease_new
            "nwc": [NWC[p] for p in per], "dwc": [dNWC[p] for p in per], "dkb": [dKb[p] for p in per],
            "fcff": [FCFF[p] for p in per], "nopat": [NOPAT[p] for p in per], "fa": [FA[p] for p in per],
            "interest": [I[p] for p in per], "tax_rate_cash": [TS[p] for p in per],
            "shield": [SH[p] for p in per], "revenue_exfs": [RO[p] for p in per],
            "pg": [PG[p] for p in per],                                                      # книга 1.4 (§4.11)
        },
        "terminal": {"g": g, "r": r, "c_T": c_t, "L": L, "rows": {f"T{hh}": {k: TR[hh][k] for k in
                     ("R", "E", "SBC", "CX", "DA", "dNWC", "dKb", "tax", "EBIT", "NOPAT", "FCFF", "RO", "pg")}
                     for hh in (1, 2)}, "interest": i_t, "shield": [s_t[1], s_t[2]], "tax_rate_cash": ts_t,
                     "cohorts": tail, "gordon_dip": bool(gordon_dip)},
        "timing": {"closed": closed, "elapsed": elapsed, "t_end": t_end, "roll_years": delta},
        "ev_parts": {"pv_flow": pv_flow, "pv_shield": pv_shield, "pv_tv": pv_tv, "pv_tv_shield": pv_tv_s},
        "tv": tv, "tv_shield": tv_s, "v0": ev, "terminal_share": (pv_tv + pv_tv_s) / ev if ev else float("nan"),
        "kappa": ({"level": k_c, "kappa1": kappa1, "r12_first": RO[first] + RO[anchor], "fa_first": FA[first],
                   "line": [KAP[p] for p in per], "floor": FLOOR, "r12_end": RO[(ly, 1)] + RO[(ly, 2)],
                   "h1_dip": delta_h1, "anchor_rest": rest, "anchor_land": land_l0,
                   "land_da": ([da_land(k) for k in range(1, len(per) + 1)] if land_l0 is not None else None)}
                  if rule == "kappa" else None),
        "claims": claims, "shares": n_eff, "options_divisor": o_d, "price": price, "warnings": warn,
    }


def steady_da(c1: float, c2: float, g: float, L: float) -> tuple[float, float]:
    """Установившаяся амортизация когорт терминала при росте вложений с темпом g (§6.1)."""
    twol = int(round(2 * L))
    a = math.floor(L)
    e = twol - 2 * a
    sg = (lambda n: n) if abs(g) < 1e-12 else (lambda n: (1 - (1 + g) ** (-n)) / g)
    da1 = ((c1 + c2) * sg(a) + e * c2 * (1 + g) ** (-(a + 1))) / twol
    da2 = ((c1 * (1 + g) + c2) * sg(a) + e * c1 * (1 + g) ** (-a)) / twol
    return da1, da2


def _paid_after_anchor(d: dict, P: dict) -> bool:
    """Дивиденд — требование, если деньги под него на якоре ещё были в компании (выплата после якоря)."""
    paid = d.get("paid")
    if not paid:
        return True
    y, k = int(paid[:4]), paid[4:]
    ay, ah = half(P["meta"]["anchor_period"])
    if k.startswith("Q"):
        qh = 1 if int(k[1]) <= 2 else 2
    else:
        qh = int(k[1])
    return (y, qh) > (ay, ah)


def price_from_equity(equity: float, P: dict, n_eff: float) -> float:
    g_gov = P["valuation"]["governance_discount"]
    return equity * (1 - g_gov) * 1000 / n_eff if equity > 0 else equity * 1000 / n_eff


# ================================================================================================ сетка, слои, точка

def grid(P: dict, F: Facts, asof=DEFAULT_ASOF) -> list[dict]:
    return [cell(P, F, w, rho, c, asof) for w in WORLDS for rho in REGIMES for c in CAPEX_LEVELS]


def capex_prob(P: dict, regime: str, level: str) -> float:
    """Вероятность уровня вложений в режиме: `joint.capex_prob_given_regime` (решение архитектора), иначе — общая."""
    by = P["joint"].get("capex_prob_given_regime")
    return by[regime][level] if by else P["joint"]["capex_prob"][level]


def layer_weights(P: dict) -> dict:
    return {"analytical": P["joint"]["world_prob"], "market_implied": P["joint"]["market_implied_prob"],
            "macro_neutral": {w: (1.0 if w == P["joint"]["neutral_world"] else 0.0) for w in WORLDS}}


def cell_prob(P: dict, layer: str, c: dict) -> float:
    return (layer_weights(P)[layer][c["world"]] * P["joint"]["regime_prob"][c["regime"]]
            * capex_prob(P, c["regime"], c["capex_level"]))


def layers(P: dict, cells: list[dict], floor: str | None = None) -> dict:
    """Слои §8. `floor` — только цена ограниченной ответственности (§13.2, книга 1.3, Г11): `cell` — капитал каждой
    клетки не ниже нуля (капитал слоя = Σ P × max(0, V0 − D)); `run` — капитал слоя не ниже нуля."""
    wp = {"analytical": P["joint"]["world_prob"], "market_implied": P["joint"]["market_implied_prob"],
          "macro_neutral": {w: (1.0 if w == P["joint"]["neutral_world"] else 0.0) for w in WORLDS}}
    out = {}
    n_eff = cells[0]["shares"]
    for name in LAYERS:
        v0 = d = tot = eq_floor = 0.0
        for c in cells:
            pr = wp[name][c["world"]] * P["joint"]["regime_prob"][c["regime"]] * capex_prob(P, c["regime"], c["capex_level"])
            tot += pr
            v0 += pr * c["v0"]
            d += pr * c["claims"]["total"]
            eq_floor += pr * max(0.0, c["v0"] - c["claims"]["total"])
        eq = eq_floor if floor == "cell" else (max(0.0, v0 - d) if floor == "run" else v0 - d)
        out[name] = {"prob_sum": tot, "v0": v0, "claims": d, "equity": eq,
                     "price": price_from_equity(eq, P, n_eff)}
    return out


def point(P: dict, F: Facts, asof=DEFAULT_ASOF, floor: str | None = None) -> dict:
    cells = grid(P, F, asof)
    ly = layers(P, cells, floor)
    lam = P["joint"]["lambda"]
    low, high = ly["macro_neutral"]["price"], ly["analytical"]["price"]
    return {"asof": str(asof), "lambda": lam, "low": low, "high": high, "point": low + lam * (high - low),
            "rates_view": high - low, "layers": ly, "cells": cells}


# ================================================================================================ полоса (§9)

def axis_edges(P: dict, ax: dict) -> tuple[float, float]:
    """Края оси (P10, P90), §9: `range` — числа книги; `offset` (книга 1.2) — сдвиги от значения книги c."""
    if "offset" in ax:
        if "range" in ax:
            raise ValueError(f"у оси {ax['key']} и range, и offset (§9: один из ключей)")
        c = dget(P, ax["paths"][0])
        lo, hi = dget(P, ax["offset"])
        return c + lo, c + hi
    a, b = _norm_range(dget(P, ax["range"]))
    return a, b


def axis_bounds(P: dict, ax: dict) -> list:
    """Границы обрезки оси (§9): `bounds`; книга 1.4 (`bounds_from`, ошибка 10 волны 3) — граница i = значение ключа
    книги прогона по пути `bounds_from[i]`, null — граница `bounds[i]`."""
    lo, hi = list(ax.get("bounds") or [None, None])
    bf = ax.get("bounds_from")
    if bf:
        lo = dget(P, bf[0]) if bf[0] else lo
        hi = dget(P, bf[1]) if bf[1] else hi
    return [lo, hi]


def axis_value(P: dict, ax: dict, u: float) -> float:
    """Значение оси полосы: двукусочное нормальное §0.4 с медианой = значению книги и краями `range` = P10, P90; у
    вида `path` c = 0, a = −1, b = +1; затем обрезка `bounds`."""
    if ax["kind"] == "path":
        c, a, b = 0.0, -1.0, 1.0
    elif ax["kind"] == "value":
        c = dget(P, ax["paths"][0])
        a, b = axis_edges(P, ax)
    else:
        raise ValueError(f"вид оси {ax['kind']!r} второй счёт не считает (в книге его нет)")
    z90 = _N.inv_cdf(0.9)
    sigma = (c - a) / z90 if u <= 0.5 else (b - c) / z90
    x = c + sigma * _N.inv_cdf(u)
    lo, hi = axis_bounds(P, ax)
    if lo is not None:
        x = max(x, lo)
    if hi is not None:
        x = min(x, hi)
    return x


def hypercube(n: int, m: int, seed: int) -> list[list[float]]:
    """Латинский гиперкуб §9: для каждой оси по порядку — перестановка, затем n случайных чисел."""
    rng = random.Random(seed)
    u = [[0.0] * m for _ in range(n)]
    for j in range(m):
        perm = list(range(n))
        rng.shuffle(perm)
        for i in range(n):
            u[i][j] = (perm[i] + rng.random()) / n
    return u


def quantile7(xs: list[float], q: float) -> float:
    s = sorted(xs)
    h = (len(s) - 1) * q
    lo = math.floor(h)
    return s[lo] + (h - lo) * (s[min(lo + 1, len(s) - 1)] - s[lo])


def band(P: dict, F: Facts, draws: int | None = None, asof=DEFAULT_ASOF, fix: dict | None = None,
         floor: str | None = None) -> dict:
    """Малая полоса §9: каждый прогон — вся сетка на изменённой книге; низ и верх прогона округлены до 0,1 ₽.
    `fix` — {ключ оси: значение}: ось закреплена (развилки §13.5), её случайные числа всё равно тратятся. `floor` —
    капитал не ниже нуля (цена ограниченной ответственности, `layers`)."""
    U = P["valuation"]["uncertainty"]
    n = int(draws or U["small_draws"])
    axes = U["axes"]
    cube = hypercube(n, len(axes), int(U["seed"]))
    lam = P["joint"]["lambda"]
    lows, highs, centers = [], [], []
    for i in range(n):
        Q = copy.deepcopy(P)
        for j, ax in enumerate(axes):
            x = fix[ax["key"]] if fix and ax["key"] in fix else axis_value(P, ax, cube[i][j])
            for path in ax["paths"]:
                dset(Q, path, x)
        pt = point(Q, F, asof, floor)
        lo, hi = round(pt["low"], 1), round(pt["high"], 1)
        lows.append(lo)
        highs.append(hi)
        centers.append(lo + lam * (hi - lo))
    out = {f"p{round(q * 100)}": quantile7(centers, q) for q in (0.1, 0.25, 0.5, 0.75, 0.9)}
    out.update({"median": out["p50"], "mean": sum(centers) / n, "draws": n, "lows": lows, "highs": highs})
    mp = P["meta"].get("market_price")
    if mp:
        out["p_below"] = sum(1 for x in centers if x < mp) / n
    return out


# ================================================================================================ проверки §13

def _layer_mean(P: dict, cells: list[dict], layer: str, num, den) -> float:
    a = b = 0.0
    for c in cells:
        pr = cell_prob(P, layer, c)
        a += pr * num(c)
        b += pr * den(c)
    return a / b


def _cid(c: dict) -> str:
    return f"{c['world']}|{c['regime']}|{c['capex_level']}"


def gates(P: dict, F: Facts, pt: dict) -> dict:
    """Гейты §13.2 (коридоры — `checks`): значение, размах по клеткам и масса — вероятность клеток слоя «свой
    взгляд» (с вероятностью уровня при режиме), где условие нарушено. Клетки с вероятностью 0 в массу не входят."""
    ch, cells, lam = P["checks"], pt["cells"], P["joint"]["lambda"]
    pr = [cell_prob(P, "analytical", c) for c in cells]
    # книга 1.2 (В14, ключ `checks.cell_mass: point`): масса гейтов клеток equity_cushion и terminal_share — по
    # точке, P = λ × P_analytical + (1 − λ) × P_macro_neutral (§13.2); без ключа — слой «свой взгляд»
    if ch.get("cell_mass") == "point":
        pr_cells = [lam * cell_prob(P, "analytical", c) + (1 - lam) * cell_prob(P, "macro_neutral", c) for c in cells]
    else:
        pr_cells = pr
    # книга 1.4 (ключ `joint.expectation: headline`, §13.2): ожидания в гейтах — с весами клетки в заголовке
    # P_H = λ × P_analytical + (1 − λ) × P_macro_neutral; масса — прежняя
    headline = dget(P, "joint.expectation", None) == "headline"
    pr_exp = ([lam * cell_prob(P, "analytical", c) + (1 - lam) * cell_prob(P, "macro_neutral", c) for c in cells]
              if headline else pr)
    out = {}

    zero_pass = ch.get("zero_weight_cells") == "pass"      # книга 1.3 (Г11): клетки с весом 0 не срабатывают

    def status(bad_weighted: list, bad_zero: list, name: str) -> str:
        """Исход гейта клеток: сработал (есть клетки с весом) / флаг «масса 0» (книги 1.1, 1.2) / проходит; ключ
        `checks.zero_weight_cells: pass` — только у гейтов `equity_cushion` и `terminal_share` (§13.2)."""
        if bad_weighted:
            return "сработал"
        if bad_zero and not (zero_pass and name in ("equity_cushion", "terminal_share")):
            return "флаг: масса 0"
        return "проходит"

    def per_cell(name, fn, lo, hi, note="", prob=None):
        p_ = pr if prob is None else prob
        vals = [fn(c) for c in cells]
        bad = [i for i, x in enumerate(vals) if not (lo <= x <= hi)]
        w_vals = [x for x, p in zip(vals, p_) if p > 0]
        out[name] = {"min": min(vals), "max": max(vals), "corridor": [lo, hi], "mass": sum(p_[i] for i in bad),
                     "cells": [_cid(cells[i]) for i in bad if p_[i] > 0],
                     "cells_zero_prob": [_cid(cells[i]) for i in bad if p_[i] == 0], "note": note,
                     "min_weighted": min(w_vals), "max_weighted": max(w_vals),
                     # значение гейта клеток — ожидание (книга 1.4 — с весами заголовка; ПРОЧТЕНИЕ 36), печать
                     "value": sum(p * x for p, x in zip(pr_exp, vals)) / sum(pr_exp)}
        out[name]["status"] = status(out[name]["cells"], out[name]["cells_zero_prob"], name)

    e26h1 = F.e("group", P["meta"]["anchor_period"])
    per_cell("ebitda_2026", lambda c: e26h1 + c["group"]["ebitda"][0], *ch["ebitda_2026"])
    tax_num = lambda c: sum(c["group"]["tax"][i] - c["group"]["shield"][i] for i in range(3))
    tax_den = lambda c: sum(c["group"]["ebit"][i] - c["group"]["interest"][i] for i in range(3))
    per_cell("tax_rate_2026_27", lambda c: tax_num(c) / tax_den(c), *ch["tax_2026_27"])
    # ПРОЧТЕНИЕ 36: «налог 2026–27» в ожидании — и отношение ожидаемых сумм (печать)
    out["tax_rate_2026_27"]["value_ratio_of_sums"] = (sum(p * tax_num(c) for p, c in zip(pr_exp, cells))
                                                      / sum(p * tax_den(c) for p, c in zip(pr_exp, cells)))
    e27 = lambda c: c["group"]["ebitda"][1] + c["group"]["ebitda"][2]
    # книга 1.4 (ключ `joint.expectation: headline`, §13.2, 4083f9d): ожидание E(2027) — с весами заголовка E_H(2027)
    e27_exp = sum(p * e27(c) for p, c in zip(pr_exp, cells))
    tol = ch["ebitda_2027_tol"] * ch["ebitda_2027_brokers"]
    out["broker_ebitda_2027"] = {"value": e27_exp, "mass": float(abs(e27_exp - ch["ebitda_2027_brokers"]) > tol)}

    roic = P["terminal"]["roic"]
    fa0 = fixed_assets(F, P["meta"]["facts_date"])
    nopat12 = roic * fa0
    roiic = lambda nop, fa: (nop - nopat12) / (fa - fa0)
    gen_blocks = bool(dget(P, "tax.general_blocks", None))
    if ch.get("roic_tax") == "general" and gen_blocks:
        # книга 1.4 (§13.2): часть блоков общей ставки к τ_g не переводится — NOPAT(2036) = Σ_h [EBIT − ((tax_u −
        # τ_g × pg) × τ_g / τ̄(y) + τ_g × pg)], pg — §4.11 на строках клетки
        tg = P["tax"]["general"]
        last_y = half(P["meta"]["last_period"])[0]
        tb = tau_bar(P, last_y)
        nop36 = lambda c: sum(c["group"]["ebit"][i] - ((c["group"]["tax"][i] - tg * c["group"]["pg"][i]) * tg / tb
                                                       + tg * c["group"]["pg"][i]) for i in (-2, -1))
    elif ch.get("roic_tax") == "general":
        # книга 1.3 (Г4, §13.2): NOPAT(2036) клетки = Σ_h [EBIT − τ_g × max(0, base)]; ROIC окна — той же формой (§17)
        tg = P["tax"]["general"]
        nop36 = lambda c: sum(c["group"]["ebit"][i] - tg * max(0.0, c["group"]["tax_base"][i]) for i in (-2, -1))
    else:
        nop36 = lambda c: c["group"]["nopat"][-2] + c["group"]["nopat"][-1]
    # книга 1.4 (ключ `checks.roic_one_season`, §13.2): IC конца — на 30.06 последнего года (2036H1), как IC якоря
    i_ic = -2 if ch.get("roic_one_season") else -1
    lo, hi = roic * (1 - ch["roic_tol"]), roic * (1 + ch["roic_tol"])
    per_cell("roic_increment_cells", lambda c: roiic(nop36(c), c["group"]["fa"][i_ic]), lo, hi,
             "советующий флаг по клеткам (правило κ)")
    exp = roiic(sum(p * nop36(c) for p, c in zip(pr_exp, cells)),
                sum(p * c["group"]["fa"][i_ic] for p, c in zip(pr_exp, cells)))
    out["roic_increment"] = {"value": exp, "corridor": [lo, hi], "mass": float(not lo <= exp <= hi),
                             "note": ("по ожиданию с весами заголовка" if headline else
                                      "по ожиданию слоя «свой взгляд»")}

    def reinvest_gap(c):
        t = c["terminal"]
        net = sum(t["rows"][k]["CX"] - t["rows"][k]["DA"] for k in ("T1", "T2"))
        return net / (t["g"] * c["group"]["fa"][-1]) - 1
    vals = [reinvest_gap(c) for c in cells]
    out["terminal_reinvest"] = {"min": min(vals), "max": max(vals),
                                "mass": sum(p for p, x in zip(pr, vals) if x < -1e-9)}

    lay = pt["layers"]
    # §8 (81c35fa): EV точки — V0(точка) по §7.3 с D слоя «свой взгляд»; ожидаемая E(2027) — слой «свой взгляд»
    v0_pt = v0_from_price(pt["point"], P, cells[0]["shares"], lay["analytical"]["claims"])
    v0_mix = lay["macro_neutral"]["v0"] + lam * (lay["analytical"]["v0"] - lay["macro_neutral"]["v0"])
    e27_l = {n: _layer_mean(P, cells, n, e27, lambda c: 1.0) for n in ("analytical", "macro_neutral")}
    # книга 1.4 (§8, 4083f9d): знаменатель — E_H(2027) = λ × E_свой + (1 − λ) × E_нейтр, как V0(точка); без ключа — слой
    e27_den = (e27_l["macro_neutral"] + lam * (e27_l["analytical"] - e27_l["macro_neutral"]) if headline
               else e27_l["analytical"])
    x = v0_pt / e27_den
    lo, hi = ch["ev_ebitda_2027"]
    out["ev_ebitda_2027"] = {"value": x, "corridor": [lo, hi], "mass": float(not lo <= x <= hi),
                             "v0_point": v0_pt, "ebitda_2027_analytical": e27_l["analytical"], "ebitda_2027": e27_den,
                             "alt_v0_mix": v0_mix / e27_l["analytical"]}

    per_cell("terminal_share", lambda c: c["terminal_share"], *ch["terminal_share_cell"], prob=pr_cells)
    s_l = {n: _layer_mean(P, cells, n, lambda c: c["ev_parts"]["pv_tv"] + c["ev_parts"]["pv_tv_shield"],
                          lambda c: c["v0"]) for n in ("analytical", "macro_neutral")}
    s_pt = s_l["macro_neutral"] + lam * (s_l["analytical"] - s_l["macro_neutral"])
    lo, hi = ch["terminal_share"]
    out["terminal_share_point"] = {"value": s_pt, "corridor": [lo, hi], "mass": float(not lo <= s_pt <= hi),
                                   "layers": s_l}

    margins = [[e / r for e, r in zip(c["group"]["ebitda"], c["group"]["revenue"])] for c in cells]
    lo, hi = ch["margin_range"]
    bad = [i for i, m in enumerate(margins) if min(m) < lo or max(m) > hi]
    out["margin_range"] = {"min": min(min(m) for m in margins), "max": max(max(m) for m in margins),
                           "corridor": [lo, hi], "mass": sum(pr[i] for i in bad),
                           "cells": [_cid(cells[i]) for i in bad if pr[i] > 0]}

    def capex_years(c):
        per, g = c["periods"], c["group"]
        ys = sorted({int(x[:4]) for x in per if int(x[:4]) > int(per[0][:4])})
        return [sum(g["capex"][i] + g["lease_new"][i] for i, x in enumerate(per) if int(x[:4]) == y)
                / sum(g["revenue"][i] for i, x in enumerate(per) if int(x[:4]) == y) for y in ys]
    cy = [capex_years(c) for c in cells]
    lo, hi = ch["capex_range"]
    bad = [i for i, v in enumerate(cy) if min(v) < lo or max(v) > hi]
    out["capex_range"] = {"min": min(min(v) for v in cy), "max": max(max(v) for v in cy), "corridor": [lo, hi],
                          "mass": sum(pr[i] for i in bad), "cells": [_cid(cells[i]) for i in bad if pr[i] > 0]}

    rr = {w: P["worlds"][w]["zero_curve"]["LT"] + P["valuation"]["beta_u"] * P["valuation"]["erp"]
          - P["worlds"][w]["lt"]["inflation"] for w in WORLDS}
    lo, hi = ch["real_rate"]
    out["real_rate"] = {"by_world": rr, "corridor": [lo, hi],
                        "mass": sum(p for p, c in zip(pr, cells) if not lo <= rr[c["world"]] <= hi)}

    eq = [c["v0"] - c["claims"]["total"] for c in cells]
    v0d = {n: ly["v0"] / ly["claims"] for n, ly in lay.items()}
    m_cells = sum(p for p, x in zip(pr_cells, eq) if x <= 0)
    out["equity_cushion"] = {"min_equity": min(eq), "v0_to_d": v0d, "mass_cells": m_cells,
                             "cells": [f"{_cid(c)}: {x:.1f}" for c, x, p in zip(cells, eq, pr_cells) if x <= 0 and p > 0],
                             "cells_zero_prob": [_cid(c) for c, x, p in zip(cells, eq, pr_cells) if x <= 0 and p == 0],
                             "zero_weight_values": {_cid(c): x for c, x, p in zip(cells, eq, pr_cells) if p == 0},
                             "min_equity_weighted": min(x for x, p in zip(eq, pr_cells) if p > 0),
                             "mass": 1.0 if min(v0d.values()) < ch["min_v0_to_d"] else m_cells}
    ec = out["equity_cushion"]
    ec["status"] = ("сработал" if ec["mass"] > 0 or ec["cells"] else status([], ec["cells_zero_prob"], "equity_cushion"))
    out["flow_9m26"] = flow_9m26(P, F, cells)
    return out


def v0_from_price(price: float, P: dict, n_eff: float, claims: float) -> float:
    """§7.3: V0(цена) = цена × N_eff / 1000 / (1 − g_gov) + D; цена ≤ 0 — без деления на 1 − g_gov."""
    g_gov = P["valuation"]["governance_discount"]
    eq = price * n_eff / 1000
    return (eq / (1 - g_gov) if price > 0 else eq) + claims


# Строки ОФП на 30.09.2026 в составе §4.10 (§2; датабук 3К26) и их доли на 30.06.2026 (§13.2, книга 1.2)
NWC_ASSETS = ("bs_inventories", "bs_trade_receivables", "bs_advances_paid", "bs_vat_receivable",
              "bs_unsettled_receivables", "bs_content_assets")
NWC_LIABS = ("bs_payables", "bs_contract_liabilities", "bs_provisions")
OCA_OUT = ("oca_income_tax_prepaid", "oca_net_lease_investment", "oca_fin_investments", "oca_loans_employees",
           "oca_loans_third", "oca_loans_related")


def nwc_from_lines(F: Facts, date: str, shares_date: str, tax_in_nwc: bool = False,
                   cash_move: float | None = None) -> float:
    """Оборотный капитал без банка по строкам ОФП (§13.2): ПОА × (1 − x) и налоги × y, доли x, y — на `shares_date`.
    Налоги на дату — одной строкой `bs_taxes_payable` или двумя (налог на прибыль + прочие). `tax_in_nwc`
    (`nwc.income_tax_place: nwc`): авансы по налогу на прибыль в x не входят, y = 1. `cash_move` (книга 1.3, ключ
    `checks.flow_9m26_bridge: cash`): вместо ПОА × (1 − x) — ПОА − X, X = Σ строк x на `shares_date` − денежное
    движение активов моста между датами (Fin + Loan III кв.)."""
    s = shares_date
    out_keys = [k for k in OCA_OUT if not (tax_in_nwc and k == INCOME_TAX_PREPAID)]
    y = 1.0 if tax_in_nwc else (F.b(s, "bs_other_taxes_payable")
                                / (F.b(s, "bs_income_tax_payable") + F.b(s, "bs_other_taxes_payable")))
    taxes = (F.b(date, "bs_taxes_payable") if F.has(date, "bs_taxes_payable")
             else F.b(date, "bs_income_tax_payable") + F.b(date, "bs_other_taxes_payable"))
    if cash_move is None:
        x = sum(F.b(s, k) for k in out_keys) / F.b(s, "bs_other_c_assets")
        oca = F.b(date, "bs_other_c_assets") * (1 - x)
    else:
        oca = F.b(date, "bs_other_c_assets") - (sum(F.b(s, k) for k in out_keys) - cash_move)
    return (sum(F.b(date, k) for k in NWC_ASSETS) + oca
            - sum(F.b(date, k) for k in NWC_LIABS) - taxes * y)


def bridge_cash(F: Facts, q: str) -> dict:
    """Денежные потоки активов моста и курсовая разница квартала (ОДДС; §13.2, книга 1.3): Fin — поступления от выбытия
    прочих финансовых активов − вложения в них; Loan — погашение выданных займов − выдача; FX — влияние курсов."""
    c = F.ctrl_cash
    v = lambda k: val(c[k][q])
    return {"fin": v("fin_assets_disposed") - v("fin_assets_invested"), "loan": v("loans_repaid") - v("loans_issued"),
            "fx": v("fx_on_cash")}


def flow_9m26(P: dict, F: Facts, cells: list[dict]) -> dict:
    """Проверка «поток 9М26» (§13.2, книга 1.2: ключ `checks.flow_9m26_capex: season` при `capex.rule: kappa`)."""
    ch = P["checks"]
    if ch.get("flow_9m26_capex") != "season" or P["capex"].get("rule") != "kappa":
        return {"value": None, "mass": 0.0, "note": "не считается (книга 1.1: нет ключа checks.flow_9m26_capex)"}
    F = one_composition(F, P)
    d0, d1, d_sh = "2025-12-31", "2026-09-30", P["meta"]["facts_date"]
    missing = [k for k in NWC_ASSETS + NWC_LIABS + ("bs_other_c_assets",) if not F.has(d1, k)]
    if not (F.has(d1, "bs_taxes_payable") or (F.has(d1, "bs_income_tax_payable") and F.has(d1, "bs_other_taxes_payable"))):
        missing.append("налоги к уплате")
    out: dict = {"missing_facts": missing}
    e9, r9 = F.es("group", NINE_M), F.rs("group", NINE_M)
    da9, sbc9 = F.gs("da_ex_office", NINE_M), F.gs("sbc", NINE_M)
    # книга 1.4 (§4.11, §13.2): блоки общей ставки — по τ_g на фактах 9М26
    pg9 = fact_pg(F, P, NINE_M)
    tax9 = tax_with_pg(P, tau_bar(P, half(P["meta"]["anchor_period"])[0]), fact_tax_base(F, P, NINE_M, da9), pg9)
    ebit9 = e9 - sbc9 - da9
    t_s = tax9 / ebit9 if ebit9 > 0 else 0.0
    if P["financing"].get("shield_cap"):
        t_s = min(t_s, P["tax"]["general"])
    cap = F.grp["capex"]
    sig3 = sum(val(cap[f"{y}Q3"]) for y in FULL_YEARS) / sum(val(cap[f"{y}H2"]) for y in FULL_YEARS)
    wp = world_weights(P)                        # книга 1.4 (§13.2): CAPEX_мод(2026H2) с весами P_H(W)
    s_first = P["capex"]["first_share"]
    capex_h1 = val(cap[P["meta"]["anchor_period"]])
    r_h1 = F.r("group", P["meta"]["anchor_period"])
    r_h2 = {c["world"]: c["group"]["revenue"][0] for c in cells if c["regime"] == "base" and c["capex_level"] == "base"}
    capex_h2 = sum(wp[w] * (s_first * (r_h1 + r_h2[w]) - capex_h1) for w in WORLDS)
    capex9 = capex_h1 + sig3 * capex_h2
    lease_ma = (P["capex"]["lease_share"] + P["ma"]["share"]) * r9
    dkb = P["bank"]["capital_to_revenue"] * (F.rs("G", NINE_M) - F.rs("G", NINE_M_PREV))
    cash = F.grp["cash_2026"]
    div9 = sum(val(cash["dividends_paid"][q]) for q in Q9M)
    disp9 = sum(val(cash["disposals"][q]) for q in Q9M)
    i_net = sum(F.g("debt_interest", q) + F.g("lease_interest", q) - F.g("office_lease_interest", q)
                - F.g("interest_income", q) for q in Q9M)
    f_fact = -(net_debt_nonbank(F, d1) - net_debt_nonbank(F, d0)) + div9 + i_net * (1 - t_s) - disp9
    out.update({"tax_rule_9m": tax9, "t_s": t_s, "sigma_3": sig3, "capex_model_2026h2": capex_h2,
                "capex_9m": capex9, "dividends_9m": div9, "disposals_9m": disp9, "interest_net_9m": i_net,
                "tol": ch["flow_9m26_tol"]})
    move_q3 = None
    if ch.get("flow_9m26_bridge") == "cash":
        # книга 1.3 (Г9, §13.2): из F_факт — потоки активов моста и курсовая разница 9М; оборотный 30.09 видит
        # движение III кв.
        bc = {q: bridge_cash(F, q) for q in Q9M}
        fin9, loan9, fx9 = (sum(bc[q][k] for q in Q9M) for k in ("fin", "loan", "fx"))
        f_fact = f_fact - fin9 - loan9 - fx9
        move_q3 = bc[Q9M[-1]]["fin"] + bc[Q9M[-1]]["loan"]
        out.update({"fin_9m": fin9, "loan_9m": loan9, "fx_9m": fx9, "bridge_move_q3": move_q3})
        claims_key = ch.get("flow_9m26_claims")
        if claims_key:
            # книга 1.4 (§13.2, ошибка 7 волны 3): F_факт += (NP(31.12.2025) − NP(30.09.2026)) − Lease_9М + W;
            # NP(30.09) = налоги к уплате(30.09) × (1 − y) − авансы(30.06), y — как в оценке оборотного
            y_sh = F.b(d_sh, "bs_other_taxes_payable") / (F.b(d_sh, INCOME_TAX_PAYABLE)
                                                          + F.b(d_sh, "bs_other_taxes_payable"))
            np0 = F.b(d0, INCOME_TAX_PAYABLE) - F.b(d0, INCOME_TAX_PREPAID)
            np1 = F.b(d1, "bs_taxes_payable") * (1 - y_sh) - F.b(d_sh, INCOME_TAX_PREPAID)
            lease_inv = lambda d: F.b(d, "oca_net_lease_investment") + F.b(d, "onca_net_lease_investment")
            lease9 = lease_inv(d0) - lease_inv(d_sh)
            w_tax = claims_key["withheld_tax"]
            f_fact = f_fact + (np0 - np1) - lease9 + w_tax
            out.update({"np_2025_12_31": np0, "np_2026_09_30": np1, "y_taxes": y_sh, "lease_9m": lease9,
                        "withheld_tax": w_tax})
    out["f_fact"] = f_fact
    if missing:
        out.update({"value": None, "mass": 0.0,
                    "note": "не считается: нет строк ОФП на 30.09.2026 в фактах: " + ", ".join(missing)})
        return out
    nwc0 = nwc_fact(F, P, d0)
    nwc1 = nwc_from_lines(F, d1, d_sh, P["nwc"].get("income_tax_place") == "nwc", cash_move=move_q3)
    f_mod_ex_nwc = e9 - P["sbc"]["share"] * r9 - tax9 - capex9 - lease_ma - dkb
    f_mod = f_mod_ex_nwc - (nwc1 - nwc0)
    diff = f_mod - f_fact
    tol_nwc = ch.get("flow_9m26_nwc_tol", 0.0)
    out.update({"nwc_2026_09_30": nwc1, "nwc_2025_12_31": nwc0, "f_model": f_mod, "value": diff,
                "value_at_nwc_edges": [diff + tol_nwc, diff - tol_nwc],
                "mass": float(abs(diff) > ch["flow_9m26_tol"])})
    return out


# ------------------------------------------------------------------------------------------------ ловушки, развилки

def _curve_up(Q):
    for w in WORLDS:
        Q["worlds"][w]["zero_curve"] = {k: v + 0.02 for k, v in Q["worlds"][w]["zero_curve"].items()}


def _margins_down(Q):
    for b in FLOW:
        Q["margin"]["targets"][b] = {rho: v - 0.01 for rho, v in Q["margin"]["targets"][b].items()}


def _capex_up(Q):
    if Q["capex"].get("rule") == "kappa":            # §13.3: по правилу κ — κ всех уровней +0,01
        Q["capex"]["kappa_levels"] = {k: v + 0.01 for k, v in Q["capex"]["kappa_levels"].items()}
    else:
        Q["capex"]["levels"] = {k: v + 0.01 for k, v in Q["capex"]["levels"].items()}


def _sbc_up(Q):
    Q["sbc"]["share"] += 0.005


def _tax_up(Q):
    Q["tax"]["general"] += 0.05
    Q["tax"]["it_rate"] = {k: (v + 0.05 if k != "LT_from" else v) for k, v in Q["tax"]["it_rate"].items()}


WORSENINGS = {"curve+2pp": _curve_up, "margins-1pp": _margins_down, "kappa+0.01": _capex_up,
              "sbc+0.5pp": _sbc_up, "tax+5pp": _tax_up}


def traps(P: dict, F: Facts, asof=DEFAULT_ASOF, draws: int | None = None, with_band: bool = True) -> dict:
    """«Ухудшение не повышает цену» (§13.3; решение архитектора): по точке и медиане малой полосы; по клеткам —
    советующий флаг (клетки, цена которых выросла). Ухудшение меняет книгу; полоса разыгрывает оси от неё."""
    base = point(P, F, asof)
    base_m = band(P, F, draws, asof)["median"] if with_band else None
    out = {}
    for name, fn in WORSENINGS.items():
        Q = copy.deepcopy(P)
        fn(Q)
        w = point(Q, F, asof)
        rose = [_cid(a) for a, b in zip(base["cells"], w["cells"]) if b["price"] > a["price"] + 1e-9]
        row = {"point": w["point"] - base["point"], "cells_rose": rose, "ok_point": w["point"] <= base["point"] + 1e-9}
        if with_band:
            m = band(Q, F, draws, asof)["median"]
            row.update({"median": m - base_m, "ok_median": m <= base_m + 1e-9})
        out[name] = row
    return out


def rederive_holding(Q: dict, F: Facts, hold: tuple) -> dict:
    """Перевывод выведенных чисел книги варианта (§17; книга 1.4, §13.5): правила §17 из фактов, затем уровни κ, δ и
    T до неподвижной точки (старт — δ книги варианта); ключам `hold` возвращается значение варианта."""
    held = {k: copy.deepcopy(dget(Q, k)) for k in hold}
    D, _ = derive(F, Q)
    R = deep_merge(Q, D)
    for k, v in held.items():
        dset(R, k, copy.deepcopy(v))
    if R["capex"].get("rule") == "kappa":
        levels, tk = derive_kappa(R, F)
        R["capex"]["kappa_levels"] = levels
        if R["capex"].get("kappa_high") == "mirror":
            R["capex"]["kappa_high_capex"] = tk["kappa.target_high"]
        if R["capex"].get("season") == "h1_dip":
            R["capex"]["h1_share"] = tk["capex.h1_share"]
            R["capex"]["h1_dip"] = tk["capex.h1_dip"]
    for k, v in held.items():
        dset(R, k, copy.deepcopy(v))
    return R


def fork_books(P: dict, F: Facts) -> dict:
    """Варианты развилок §13.5 на полосе: {имя: (книга, закреплённые оси)}."""
    out = {}
    a, b = axis_edges(P, next(ax for ax in P["valuation"]["uncertainty"]["axes"] if ax["key"] == "it_tax"))
    out["it_tax_low"] = (P, {"it_tax": a})
    out["it_tax_high"] = (P, {"it_tax": b})
    gtv_r_c = sum(F.gtv("C", q) for q in Q12) / F.rs("C", Q12)
    # книга 1.4 (ключ `forks.rederive`, §13.5): развилки стресса и e-com — с перевыводом выведенных чисел до
    # неподвижной точки; ключи развилки держатся (после перевывода им возвращается значение варианта)
    redo = bool(dget(P, "forks.rederive", False))
    for name, x in (("ecom_breakeven", 0.0), ("ecom_ozon", P["margin"]["C"]["ozon_trade_gmv"] * gtv_r_c)):
        Q = copy.deepcopy(P)
        d = x - Q["margin"]["center"]["C"]
        Q["margin"]["center"]["C"] = x
        Q["margin"]["targets"]["C"] = {rho: v + d for rho, v in Q["margin"]["targets"]["C"].items()}
        Q["margin"]["range"]["C"] = [v + d for v in _norm_range(Q["margin"]["range"]["C"])]
        if redo:
            Q = rederive_holding(Q, F, ("margin.center.C", "margin.targets.C", "margin.range.C"))
        out[name] = (Q, None)
    Q = copy.deepcopy(P)
    w = P["forks"]["stress_weight"]
    Q["joint"]["regime_prob"] = {rho: (w if rho == "stress" else (1 - w) / (len(REGIMES) - 1)) for rho in REGIMES}
    if redo:
        Q = rederive_holding(Q, F, ("joint.regime_prob",))
    out["stress"] = (Q, None)
    out["at_low"] = (P, {"at_expense": -1.0})
    out["at_high"] = (P, {"at_expense": 1.0})
    return out


def bank_cash_fork(P: dict, F: Facts, asof=DEFAULT_ASOF) -> dict:
    """Развилка «деньги банка» (§13.5): D при Cash_bank = ликвидности формы 101 на якорь — сдвиг моста на точке
    (ПРОЧТЕНИЕ 21: проценты и щит не меняются — «цена — сдвиг моста»)."""
    d = P["meta"]["facts_date"]
    cash_rule = net_debt_nonbank(F, d) - F.b(d, "net_debt_company")
    delta = F.b(d, "f101_liquidity") - cash_rule
    base = point(P, F, asof)
    cells = [dict(c, claims=dict(c["claims"], total=c["claims"]["total"] + delta)) for c in base["cells"]]
    ly = layers(P, cells)
    lam = P["joint"]["lambda"]
    p2 = ly["macro_neutral"]["price"] + lam * (ly["analytical"]["price"] - ly["macro_neutral"]["price"])
    return {"delta_claims": delta, "price": p2 - base["point"]}


def limited_liability(P: dict, F: Facts, asof=DEFAULT_ASOF, draws: int | None = None) -> dict:
    """Цена ограниченной ответственности акционеров (§13.2, книга 1.3, Г11): сдвиг точки и медианы малой полосы, если
    капитал клетки и прогона полосы не ниже нуля — на тех же случайных числах. ПРОЧТЕНИЕ 31: «капитал клетки» — пол
    у каждой клетки сетки точки и каждого прогона (`cell`, основное); «капитал прогона» — пол у капитала слоя (`run`)."""
    base, b0 = point(P, F, asof), band(P, F, draws, asof)
    out = {"point_base": base["point"], "median_base": b0["median"]}
    for fl in ("cell", "run"):
        out[f"point.{fl}"] = point(P, F, asof, fl)["point"] - base["point"]
        out[f"median.{fl}"] = band(P, F, draws, asof, floor=fl)["median"] - b0["median"]
    out["cells_below_zero"] = [f"{_cid(c)}: {c['v0'] - c['claims']['total']:.1f}" for c in base["cells"]
                               if c["v0"] - c["claims"]["total"] < 0]
    return out


def forks(P: dict, F: Facts, asof=DEFAULT_ASOF, draws: int | None = None) -> dict:
    """Цены развилок §13.5 — сдвиг медианы малой полосы против заголовка на тех же случайных числах."""
    head = band(P, F, draws, asof)["median"]
    out = {"headline": head}
    for name, (Q, fix) in fork_books(P, F).items():
        out[name] = band(Q, F, draws, asof, fix=fix)["median"] - head
    out["bank_cash"] = bank_cash_fork(P, F, asof)["price"]
    return out


# ================================================================================================ самопроверки

def selfcheck(P: dict, F: Facts, asof=DEFAULT_ASOF, T: dict | None = None) -> list[tuple[str, bool, str]]:
    res = []
    add = lambda name, ok, note="": res.append((name, bool(ok), note))

    # якорь = факты: A–G + исключения = группа 2026H1 (выручка, EBITDA) до 0,1 млрд
    for met, fn in (("выручка", F.r), ("EBITDA", F.e)):
        p = P["meta"]["anchor_period"]
        diff = sum(fn(b, p) for b in BLOCKS) + fn("X", p) - fn("group", p)
        add(f"якорь {p}: A–G + X = группа, {met}", abs(diff) <= 0.1, f"{diff:+.4f}")
    # среднее режимов = правило центра
    worst = 0.0
    for b in FLOW:
        worst = max(worst, abs(sum(P["margin"]["targets"][b].values()) / 4 - P["margin"]["center"][b]))
        worst = max(worst, abs(sum(P["growth"]["shift"][b].values()) / 4))
    add("среднее режимов = центр (маржа, сдвиг роста)", worst <= 1e-12, f"{worst:.1e}")
    pt = point(P, F, asof)
    worst_g = worst_f = worst_ev = worst_m = worst_s = worst_ct = worst_ts = worst_k = 0.0
    worst_floor = math.inf
    worst_ro = worst_cap = worst_coh = worst_rest = 0.0
    fa_anchor = fixed_assets(F, P["meta"]["facts_date"])
    cap_on = bool(P["financing"].get("shield_cap", False))
    tau_g = P["tax"]["general"]
    n_capped = 0
    for c in pt["cells"]:
        gr = c["group"]
        for i in range(len(c["periods"])):
            rsum = sum(c["blocks"][b]["revenue"][i] for b in BLOCKS) + c["blocks"]["X"]["revenue"][i]
            esum = sum(c["blocks"][b]["ebitda"][i] for b in BLOCKS) + c["blocks"]["X"]["ebitda"][i] + gr["attack"][i]
            worst_g = max(worst_g, abs(rsum - gr["revenue"][i]), abs(esum - gr["ebitda"][i]))
            f = gr["ebitda"][i] - gr["sbc"][i] - gr["tax"][i] - gr["capex"][i] - gr["lease_new"][i] - gr["acq"][i] - gr["dwc"][i] - gr["dkb"][i]
            worst_f = max(worst_f, abs(f - gr["fcff"][i]))
            capped = cap_on and gr["ebit"][i] > 0 and gr["tax"][i] / gr["ebit"][i] > tau_g
            n_capped += capped
            if gr["ebit"][i] > 0 and abs(gr["ebit"][i] - gr["interest"][i]) > 1e-9 and not capped:
                ts = (gr["tax"][i] - gr["shield"][i]) / (gr["ebit"][i] - gr["interest"][i])
                worst_ts = max(worst_ts, abs(ts - gr["tax_rate_cash"][i]))
            if cap_on:                           # §4.13: ставка щита ≤ τ_g
                worst_cap = max(worst_cap, gr["tax_rate_cash"][i] - tau_g)
            # §0: R° = R × Σ A–F / Σ A–G (книга 1.2; книга 1.1 — R° = R)
            a_f = sum(c["blocks"][b]["revenue"][i] for b in BLOCKS if b != "G")
            ro = gr["revenue"][i] * (a_f / (a_f + c["blocks"]["G"]["revenue"][i])
                                     if P["meta"].get("balance_base") == "ex_fs" else 1.0)
            worst_ro = max(worst_ro, abs(ro - gr["revenue_exfs"][i]))
        coh = c["terminal"]["cohorts"]
        if coh:                                  # §6.3, §16: амортизация когорт к году H сходится к установившейся
            s_last = (1 + c["terminal"]["g"]) ** (len(coh["DA"]) // 2 - 1)
            steady = c["terminal"]["rows"]["T1"]["DA"] + c["terminal"]["rows"]["T2"]["DA"]
            worst_coh = max(worst_coh, abs((coh["DA"][-2] + coh["DA"][-1]) / s_last / steady - 1))
        e = c["ev_parts"]
        worst_ev = max(worst_ev, abs(sum(e.values()) - c["v0"]))
        # годовая маржа блока = M_b(y); годовые выплаты = sbc × выручка года
        per = c["periods"]
        for j in range(1, len(per) - 1, 2):
            r_year = gr["revenue"][j] + gr["revenue"][j + 1]
            worst_s = max(worst_s, abs(gr["sbc"][j] + gr["sbc"][j + 1] - P["sbc"]["share"] * r_year))
            y = int(per[j][:4])
            for b in FLOW:
                eb, rb = c["blocks"][b]["ebitda"], c["blocks"][b]["revenue"]
                ann = (eb[j] + eb[j + 1]) / (rb[j] + rb[j + 1])
                tgt = P["margin"]["targets"][b][c["regime"]]
                if y >= P["margin"]["target_year"][b]:
                    worst_m = max(worst_m, abs(ann - tgt))
        t = c["terminal"]
        rows = t["rows"]
        kp = c["kappa"]
        if kp is not None:                       # §6.2, §16: Σ (CX − DA) терминала = g × κ_C × R12(2036H2)
            lhs = sum(rows[k]["CX"] - rows[k]["DA"] for k in rows)
            worst_ct = max(worst_ct, abs(lhs - t["g"] * kp["level"] * kp["r12_end"]))
            # §16: FA / R12 = κ на линии и после 2030 г. (кроме полугодий границы и следующих за ними); CX ≥ φ × DA
            rev = gr["revenue_exfs"]                # κ = FA / R12 на базе R° (книга 1.2)
            dip = kp.get("h1_dip") or 0.0           # книга 1.3 (§4.9): на 30.06 — линия минус δ
            for i in range(1, len(per)):
                if per[i] in kp["floor"] or per[i - 1] in kp["floor"]:
                    continue
                tgt = kp["line"][i] - (dip if per[i][5] == "1" else 0.0)
                worst_k = max(worst_k, abs(gr["fa"][i] / (rev[i] + rev[i - 1]) - tgt))
            if kp.get("anchor_rest") is not None:  # §4.9 (Г1): за 2L полугодий капитал якоря списан весь
                twol = int(round(2 * t["L"]))
                own = sum(gr["da"][i] for i in range(twol)) - sum(
                    gr["cx"][j] / twol for i in range(twol) for j in range(i))   # когорты прогноза p_1… внутри 2L
                # книга 1.4 (§4.9): право пользования землёй — вне остатка; за 2L полугодий списано Σ DA_L(k)
                if kp.get("anchor_land") is not None:
                    own += kp["anchor_land"] - sum(kp["land_da"][:twol])
                worst_rest = max(worst_rest, abs(own - fa_anchor))
            worst_floor = min(worst_floor, min(x - P["capex"]["floor_da"] * d for x, d in zip(gr["cx"], gr["da"])))
        elif t["c_T"] > 0:
            no_nwc = bool(P["terminal"].get("reinvestment_excludes_nwc", False))
            lhs = sum(rows[k]["CX"] - rows[k]["DA"] + (0.0 if no_nwc else rows[k]["dNWC"]) for k in rows)
            rhs = t["g"] / P["terminal"]["roic"] * sum(rows[k]["NOPAT"] for k in rows)
            worst_ct = max(worst_ct, abs(lhs - rhs))
    add("group_identity: R = Σ блоков + R_X, E = Σ + E_X + E_atk (36 клеток)", worst_g <= 1e-9, f"{worst_g:.1e}")
    add("fcff_identity: FCFF = сумма строк (36 клеток)", worst_f <= 1e-9, f"{worst_f:.1e}")
    add("ev_identity: EV = PV потока + щита + терминала + щита терминала", worst_ev <= 1e-9, f"{worst_ev:.1e}")
    add("годовая маржа блока = цель с года цели", worst_m <= 1e-12, f"{worst_m:.1e}")
    add("годовые выплаты акциями = sbc × выручка года", worst_s <= 1e-9, f"{worst_s:.1e}")
    add("(tax − S)/(EBIT − I) = t_S (пока не сработал предел щита)", worst_ts <= 1e-12,
        f"{worst_ts:.1e}; полугодий с пределом {n_capped}")
    add("c_T — корень правила вложений терминала", worst_ct <= 1e-6, f"{worst_ct:.1e}")
    add("R° = R × Σ A–F / Σ A–G (36 клеток)", worst_ro <= 1e-9, f"{worst_ro:.1e}")
    if cap_on:
        add("ставка щита ≤ τ_g (ключ financing.shield_cap)", worst_cap <= 1e-12, f"{worst_cap:.1e}")
    if P["terminal"].get("da_cohort_years") and P["capex"].get("rule") == "kappa":
        # ПРОЧТЕНИЕ 27: «сходится» — приближённо: правило κ в хвосте делит вложения между полугодиями по сезону
        # R°, а не по c_T × R, поэтому годовая амортизация года H отходит от установившейся на доли процента. Книга 1.3
        # (провал κ на 30.06): хвост держит сезон вложений (I полугодие ≈ 34 % года), когорты II полугодия больше и
        # моложе к 31.12, и тот же капитал на линии требует меньше вложений — амортизация года H ниже установившейся
        # без сезона на 1,9–2,4 %; это свойство правила §6.3, не сход (запись в отчёте 402)
        tol_coh = 0.03 if P["capex"].get("season") == "h1_dip" else 0.01
        if P["capex"].get("season") == "h1_dip" and P["terminal"].get("gordon_dip"):
            tol_coh = 1e-06                       # книга 1.4 (§6.3): Гордон с провалом — хвост непрерывен
        add(f"амортизация когорт терминала года H к установившейся — в пределах {tol_coh:.4%} (36 клеток)",
            worst_coh <= tol_coh, f"{worst_coh:.1e}")
    if P["capex"].get("rule") == "kappa":
        add("FA / R12 = κ линии (книга 1.3: на 30.06 — минус δ; правило κ, 36 клеток)", worst_k <= 1e-12,
            f"{worst_k:.1e}")
        if P["capex"].get("anchor_rest") == "cohort":
            add("остаток якоря: за 2L полугодий списан весь FA(якорь) (36 клеток)", worst_rest <= 1e-9,
                f"{worst_rest:.1e}")
        add("вложения не ниже φ × DA", worst_floor >= -1e-12, f"{worst_floor:.3f}")
    if T and "kappa.share_at_base" in T:          # §4.9: корни уровней κ; книга 1.2 — среднее трёх = доле окна
        dev = max(abs(T["kappa.share_at_base"] - T["kappa.target_base"]),
                  abs(T["kappa.share_at_high"] - T["kappa.target_high"]))
        add("уровни κ: D(κ_C) = цели уровня", dev <= 1e-9, f"{dev:.1e}")
        if P["capex"].get("kappa_target") == "all_cells" and "kappa.all_cells_share" in T:
            dev = abs(T["kappa.all_cells_share"] - T["kappa.window_share"])
            add("книга 1.3: средняя доля вложений 2027–2030 по всем клеткам = доле окна", dev <= 1e-9, f"{dev:.1e}")
        elif P["capex"].get("kappa_high") == "mirror":
            w = P["joint"]["capex_prob"]
            mean = (w["low"] * T["kappa.share_at_low"] + w["base"] * T["kappa.share_at_base"]
                    + w["high"] * T["kappa.share_at_high"])
            add("среднее долей трёх уровней κ (25 / 50 / 25) = доле окна", abs(mean - T["kappa.target_base"]) <= 1e-9,
                f"{mean - T['kappa.target_base']:.1e}")
    if T and "capex.h1_dip" in T:                # книга 1.3 (§4.9): H(δ) = T
        dev = abs(T["h1_dip.H"] - T["capex.h1_share"])
        add("книга 1.3: доля I полугодия во вложениях 2027–2030 = T", dev <= 1e-9, f"{dev:.1e}")
    by = P["joint"].get("capex_prob_given_regime")
    if by:                                       # решение архитектора: средние уровней — 25 / 50 / 25 при режимах
        dev = max(max(abs(sum(P["joint"]["regime_prob"][r] * by[r][lv] for r in REGIMES) - P["joint"]["capex_prob"][lv])
                      for lv in CAPEX_LEVELS), max(abs(sum(by[r].values()) - 1) for r in REGIMES))
        add("уровни при режиме: Σ_C = 1, средние = joint.capex_prob", dev <= 1e-12, f"{dev:.1e}")
    for name, ly in pt["layers"].items():
        add(f"вероятности слоя {name} в сумме 1", abs(ly["prob_sum"] - 1) <= 1e-12, f"{ly['prob_sum']:.15f}")
    add("точка конечна", math.isfinite(pt["point"]), f"{pt['point']:.1f} ₽")
    return res


# ================================================================================================ печать

def _fmt_cell(c: dict) -> str:
    out = [f"клетка {c['world']} × {c['regime']} × {c['capex_level']}, дата оценки {c['asof']}"]
    per = c["periods"]
    show = [0, 1, 2, 9, 10, 19, 20]
    out.append("строка        " + "".join(f"{per[i]:>10}" for i in show))
    for b in ("A", "B", "C", "D", "E", "F", "G", "X"):
        out.append(f"R {b:<11} " + "".join(f"{c['blocks'][b]['revenue'][i]:10.1f}" for i in show))
    for b in ("A", "B", "C", "D", "E", "F", "G", "X"):
        out.append(f"E {b:<11} " + "".join(f"{c['blocks'][b]['ebitda'][i]:10.1f}" for i in show))
    for k in ("revenue", "ebitda", "sbc", "da", "tax", "cx", "acq", "dwc", "dkb", "fcff", "interest", "shield"):
        out.append(f"{k:<13} " + "".join(f"{c['group'][k][i]:10.1f}" for i in show))
    t = c["terminal"]
    out.append(f"терминал: g {t['g']:.4%}, r {t['r']:.4%}, c_T {t['c_T']:.4%}, L {t['L']}; TV {c['tv']:.1f}")
    e = c["ev_parts"]
    out.append(f"EV {c['v0']:.1f} = поток {e['pv_flow']:.1f} + щит {e['pv_shield']:.1f} + терминал {e['pv_tv']:.1f}"
               f" + щит терминала {e['pv_tv_shield']:.1f}; доля терминала {e['pv_tv'] / c['v0']:.1%}")
    out.append("требования: " + ", ".join(f"{k} {v:.1f}" for k, v in c["claims"].items()))
    out.append(f"делитель {c['shares']:.3f} млн; цена клетки {c['price']:.1f} ₽")
    return "\n".join(out)


def _fmt_point(pt: dict) -> str:
    out = [f"дата оценки {pt['asof']}; λ = {pt['lambda']}"]
    for k, ly in pt["layers"].items():
        out.append(f"  {k:<15} V0 {ly['v0']:9.1f}  D {ly['claims']:7.1f}  цена {ly['price']:8.1f} ₽")
    out.append(f"низ {pt['low']:.1f} ₽, верх {pt['high']:.1f} ₽, точка {pt['point']:.1f} ₽, вклад взгляда на ставки "
               f"{pt['rates_view']:+.1f} ₽")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    for name in ("cell", "point", "derive", "check", "band", "gates", "forks", "traps", "liability"):
        s = sub.add_parser(name)
        s.add_argument("--draws", type=int, default=None, help="прогонов полосы (по умолчанию small_draws)")
        s.add_argument("--book", default=None, help="книга ядра (YAML): её значения заменяют свои")
        s.add_argument("--version", default="1.4", choices=VERSIONS,
                       help="свои суждения: книга 1.4, 1.3, 1.2 (ключи §17) или 1.1 (прежние правила)")
        s.add_argument("--asof", default=DEFAULT_ASOF)
        s.add_argument("--facts", default=None, help="папка фактов вместо data/facts")
        s.add_argument("--json", action="store_true")
        if name == "cell":
            s.add_argument("--world", required=True, choices=WORLDS)
            s.add_argument("--regime", required=True, choices=REGIMES)
            s.add_argument("--capex", required=True, choices=CAPEX_LEVELS)
    a = ap.parse_args(argv)
    cmd = a.cmd or "point"
    F = Facts(Path(a.facts)) if getattr(a, "facts", None) else Facts()
    book = load_book(a.book) if getattr(a, "book", None) else None
    P, T = build_params(F, book, getattr(a, "version", "1.4"))
    asof = getattr(a, "asof", DEFAULT_ASOF)
    as_json = getattr(a, "json", False)
    if book is not None:
        print(f"книга ядра: взято {len(T['book']['taken'])} ключей, нет в книге {len(T['book']['missing'])}",
              file=sys.stderr)
    if cmd == "cell":
        c = cell(P, F, a.world, a.regime, a.capex, asof)
        print(json.dumps(c, ensure_ascii=False) if as_json else _fmt_cell(c))
    elif cmd == "point":
        pt = point(P, F, asof)
        if as_json:
            pt = {k: v for k, v in pt.items() if k != "cells"}
            print(json.dumps(pt, ensure_ascii=False))
        else:
            print(_fmt_point(pt))
    elif cmd == "derive":
        derived = {k: dget(P, k) for k in _leaf_paths(derive(F, P)[0])}
        if "kappa_levels" in P["capex"]:
            derived.update({f"capex.kappa_levels.{k}": v for k, v in P["capex"]["kappa_levels"].items()})
        if P["capex"].get("kappa_high") == "mirror":
            derived["capex.kappa_high_capex"] = P["capex"]["kappa_high_capex"]
        for k in ("h1_share", "h1_dip"):                  # книга 1.3 (§4.9, §17)
            if k in P["capex"]:
                derived[f"capex.{k}"] = P["capex"][k]
        if as_json:
            print(json.dumps({"keys": derived, "trace": T}, ensure_ascii=False, default=str))
        else:
            for k, v in derived.items():
                print(f"{k:<40} {v}")
            for k, v in T.items():
                if not k.startswith("asset_life_sse") and k != "book":
                    print(f"  · {k:<38} {v}")
    elif cmd in ("band", "gates", "forks", "traps", "liability"):
        draws = getattr(a, "draws", None)
        if cmd == "band":
            res = {k: v for k, v in band(P, F, draws, asof).items() if k not in ("lows", "highs")}
        elif cmd == "gates":
            res = gates(P, F, point(P, F, asof))
        elif cmd == "forks":
            res = forks(P, F, asof, draws)
        elif cmd == "liability":
            res = limited_liability(P, F, asof, draws)
        else:
            res = traps(P, F, asof, draws)
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    elif cmd == "check":
        res = selfcheck(P, F, asof, T)
        for name, ok, note in res:
            print(f"{'ок ' if ok else 'ПРОВАЛ'}  {name}  [{note}]")
        print(_fmt_cell(cell(P, F, "N", "base", "base", asof)))
        print(_fmt_point(point(P, F, asof)))
        return 0 if all(ok for _, ok, _ in res) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
