# -*- coding: utf-8 -*-
"""Переходник модели к общему слою семейства (`common/`): всё, что слою нужно от модели, — только здесь.

Устройство — `common/README.md`, раздел «Переходник модели». Команды `python -m common.cli <команда>`: `state`,
`worlds`, `adopt-common` делает слой; остальные — словарь COMMANDS ниже (функция `f(argv) -> код`, список аргументов
команды модели с `{python}` или строка «почему не подключено»).
"""
from __future__ import annotations

from typing import Any

SLUG = "yandex"
TICKERS = ("YDEX",)
NAME = "Яндекс"
RELEASE_PATH = "var/release/latest.json"        # последний собранный выпуск (ops/build_release.py → DEFAULT_OUT)

COMMANDS: dict[str, Any] = {
    "rederive": ["{python}", "-B", "-m", "model.rederive"],      # перевывод книги (§17); [--dry-run]
    "quick": ["{python}", "-B", "-m", "model.quick"],            # сетка + малая полоса; [--draws N] [--axes]
    "band": ["{python}", "-B", "-m", "model.book_results"],     # полная полоса и таблицы книги; [--draws N]
    "server-tests": ["{python}", "-B", "-m", "ops.server_tests"],  # тесты такта параллельно; [--groups K]
    "release": lambda argv: _release(argv),                     # сборка выпуска yandex-v1; [--fast] [--live-file F]
}   # report (приём отчёта) делает слой — через reanchor и expected_path ниже

_SOURCES = ("--live", "--live-file", "--book", "--check")


def _release(argv: list[str]) -> int:
    """`ops/build_release.py` с аргументами команды; без источника входов (`--live`, `--live-file`, `--book`,
    `--check`) — на входах книги (`--book`)."""
    import subprocess
    import sys
    from pathlib import Path

    argv = list(argv)
    if not any(a == s or a.startswith(s + "=") for a in argv for s in _SOURCES):
        argv = ["--book", *argv]
    root = Path(__file__).resolve().parents[1]
    return subprocess.run([sys.executable, "-B", str(root / "ops" / "build_release.py"), *argv], cwd=root).returncode


# ------------------------------------------------------------------------------------------------------------------
# Лист фактов → файлы data/facts/*.json (задача плана 3). Лист: data/facts/sheets/2026H1.csv + 2026Q3.csv (сборка —
# FACTS_SHEETS, списком). В листе только факты; здесь — только суммы фактов (полугодие = сумма кварталов, блок = сумма
# сегментов, строки баланса) с текстом `calc`. Числа книги здесь не выводятся (их выводит перевывод, задача 5а).
# Узел факта: {"v": число, "src": {doc_id, page}}; узел суммы: {"v": число, "calc": "как сложено"}.

FACTS_SHEETS = ("2026H1", "2026Q3")

_Q_SEG = [f"{y}Q{q}" for y in (2024, 2025) for q in (1, 2, 3, 4)] + ["2026Q1", "2026Q2", "2026Q3"]
_Q_GRP = [f"2023Q{q}" for q in (1, 2, 3, 4)] + _Q_SEG
_H_SEG = ["2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
_H_GRP = ["2023H1", "2023H2"] + _H_SEG

# Блоки устава (раздел 1) из сегментов листа. X — исключения устава: межсегментные (X_inter, строка датабука между
# бизнес-группами) и внутрисегментные (X_intra, Городские и Персональные сервисы) расчёты; 12 мес. −234,2 млрд.
FACTS_BLOCKS = {
    "A": ["search"], "B": ["ridetech"], "C": ["ecom", "delivery"], "D": ["plus", "svoi"], "E": ["b2b"],
    "F": ["autotech", "other"], "F_at": ["autotech"], "F_o": ["other"], "G": ["fintech"],
    "X": ["groups_elim", "city_elim", "personal_elim"], "X_inter": ["groups_elim"],
    "X_intra": ["city_elim", "personal_elim"], "group": [""],
}
_SEGS = ["search", "ridetech", "ecom", "delivery", "city_elim", "plus", "fintech", "svoi", "personal_elim", "b2b",
         "autotech", "other", "groups_elim", ""]


def _parts(period: str) -> list[str]:
    """Кварталы полугодия."""
    y, h = period[:4], period[4:]
    return [f"{y}Q1", f"{y}Q2"] if h == "H1" else [f"{y}Q3", f"{y}Q4"]


def _sum(terms):
    """Функция сборки: Σ знак × число листа по (знак, метрика, сегмент, период)."""
    def f(get):
        return sum(sign * get(m, s, p) for sign, m, s, p in terms)
    return f


def _leaf(metric, segment, period):
    from common.facts_sheet import Fact, Ref
    return {"v": Fact(metric, segment, period), "src": Ref(metric, segment, period)}


def _calc(terms, text):
    return {"v": _sum(terms), "calc": text}


def _seg_value(metric, segs, period):
    """Термы блока за период: квартал и год — строки листа, полугодие — сумма кварталов."""
    pers = _parts(period) if "H" in period else [period]
    return [(1, metric, s, p) for s in segs for p in pers]


def _segments_json() -> dict:
    blocks = {}
    for b, segs in FACTS_BLOCKS.items():
        blocks[b] = {m: {p: _sum(_seg_value(m, segs, p)) for p in _Q_SEG + _H_SEG + ["2024", "2025"]}
                     for m in ("revenue", "adj_ebitda")}
    gtv_b = {p: _sum([(1, "gtv", "ridetech", p)]) for p in _Q_SEG + ["2024", "2025"]}
    gtv_b.update({h: _sum([(1, "gtv", "ridetech", q) for q in _parts(h)]) for h in _H_SEG})
    q_c = [p for p in _Q_SEG if p not in ("2024Q1", "2024Q2")]
    gtv_c = {p: _sum([(1, "gtv", s, p) for s in ("ecom", "delivery")]) for p in q_c + ["2024", "2025"]}
    gtv_c.update({h: _sum([(1, "gtv", s, q) for s in ("ecom", "delivery") for q in _parts(h)]) for h in _H_SEG[1:]})
    gtv_c["2024H1"] = _sum([(1, "gtv", s, "2024") for s in ("ecom", "delivery")]
                           + [(-1, "gtv", s, q) for s in ("ecom", "delivery") for q in ("2024Q3", "2024Q4")])
    return {
        "unit": "bn_rub",
        "source": "кварталы и годы — датабук 30.09.2026, лист «Показатели сегментов»; полугодие = сумма кварталов; "
                  "МСФО 6М — сверка (ifrs_6m)",
        "composition": FACTS_BLOCKS,
        "blocks": blocks,
        "gtv": {"B": gtv_b, "C": gtv_c,
                "calc": "B — GTV Райдтеха; C — GTV электронной коммерции + доставки; 2024Q1–Q2 у C не раскрыты в "
                        "нынешнем определении, 2024H1 у C = год − 3К − 4К"},
        "segments": {s or "group": {m: {p: _leaf(m, s, p) for p in _Q_SEG + ["2024", "2025"]}
                                    for m in ("revenue", "adj_ebitda")} for s in _SEGS},
        "gtv_segments": {s: {p: _leaf("gtv", s, p) for p in (_Q_SEG if s == "ridetech" else q_c) + ["2024", "2025"]}
                         for s in ("ridetech", "ecom", "delivery")},
        "ifrs_6m": {s or "group": {m: {p: _leaf(m + "_ifrs", s, p) for p in ("2025H1", "2026H1")}
                                   for m in ("revenue", "adj_ebitda")}
                    for s in ["search", "ridetech", "ecom", "delivery", "plus", "fintech", "svoi", "b2b", "autotech",
                              "other", "ifrs_elim", ""]},
        "classifieds_revenue": {
            p: _leaf("revenue_classifieds", "", p) for p in ("2023", "2024", "2024H1", "2025H1")},
    }


def _group_json() -> dict:
    def series(metric):
        out = {p: _leaf(metric, "", p) for p in _Q_GRP + ["2023", "2024", "2025"]}
        out.update({h: _calc([(1, metric, "", q) for q in _parts(h)], "сумма кварталов") for h in _H_GRP})
        return out

    da_ex = {p: _calc([(1, "da_total", "", q) for q in (_parts(p) if "H" in p else [p])]
                      + [(-1, "da_office_rou", "", q) for q in (_parts(p) if "H" in p else [p])],
                      "da_total − da_office_rou") for p in _Q_GRP + _H_GRP + ["2023", "2024", "2025"]}
    rou_add = {}
    for cls in ("premises", "cars", "land", "other", "total"):
        node = {p: _leaf("rou_additions", cls, p) for p in ("2024", "2024H1", "2025", "2025H1", "2026H1")}
        for y in ("2024", "2025"):
            node[f"{y}H2"] = _calc([(1, "rou_additions", cls, y), (-1, "rou_additions", cls, f"{y}H1")], "год − 1П")
        rou_add[cls] = node
    tax = {p: _leaf("tax_rate_diff", "", p) for p in ("2024", "2025", "2025H1", "2026H1")}
    tax["2025H2"] = _calc([(1, "tax_rate_diff", "", "2025"), (-1, "tax_rate_diff", "", "2025H1")], "год − 1П")
    cash_2026 = {m: {p: _leaf(m, "", p) for p in ("2026Q1", "2026Q2", "2026Q3")}
                 for m in ("dividends_paid", "own_shares_purchased", "acquisitions", "disposals", "lease_principal",
                           # книга 1.3 (§13.2, решение Г9): потоки активов моста и курсовая разница — знак ОДДС
                           "fin_assets_purchased", "fin_assets_disposed", "loans_issued", "loans_repaid", "fx_cash")}
    # Аренда машин, земли и прочего (не помещения) — валовые поступления права пользования; 12 мес. = 2П25 + 1П26
    nonprem = {h: _calc([(1, "rou_additions", c, y) for c in ("cars", "land", "other") for y in
                         ([h] if h.endswith("H1") else [h[:4]])]
                        + ([] if h.endswith("H1") else [(-1, "rou_additions", c, f"{h[:4]}H1")
                                                        for c in ("cars", "land", "other")]),
                        "автомобили + земля + прочее" + ("" if h.endswith("H1") else " (год − 1П)"))
               for h in _H_SEG}
    nonprem["ltm_2026H1"] = _calc([(1, "rou_additions", c, "2025") for c in ("cars", "land", "other")]
                                  + [(-1, "rou_additions", c, "2025H1") for c in ("cars", "land", "other")]
                                  + [(1, "rou_additions", c, "2026H1") for c in ("cars", "land", "other")],
                                  "12 мес. к 30.06.2026: 2П25 + 1П26 (поступления по классам раскрыты по полугодиям)")
    debt_int = {p: _calc([(1, "interest_expense", "", q) for q in (_parts(p) if "H" in p else [p])]
                         + [(-1, "lease_interest", "", q) for q in (_parts(p) if "H" in p else [p])],
                         "interest_expense − lease_interest: проценты по долгу")
                for p in _Q_GRP + _H_GRP + ["2023", "2024", "2025"]}

    def half_from_year(metric):
        node = {p: _leaf(metric, "", p) for p in ("2024", "2024H1", "2025", "2025H1", "2026H1")}
        for y in ("2024", "2025"):
            node[f"{y}H2"] = _calc([(1, metric, "", y), (-1, metric, "", f"{y}H1")], "год − 1П")
        return node
    return {
        "unit": "bn_rub",
        "source": "кварталы и годы — датабук (сверка скорр. EBITDA, ОДДС); налог и право пользования — МСФО",
        "da_total": series("da_total"),
        "da_office_rou": series("da_office_rou"),
        "da_ex_office": da_ex,
        "sbc": series("sbc"),
        "capex": series("capex"),
        "income_tax": series("income_tax"),
        "office_lease_interest": series("office_lease_interest"),
        "tax_rate_diff": tax,
        "rou_additions": rou_add,
        "rou_additions_nonpremises": nonprem,
        "interest_expense": series("interest_expense"),
        "lease_interest": series("lease_interest"),
        "interest_income": series("interest_income"),
        "debt_interest": debt_int,
        "content_additions": half_from_year("content_additions"),
        "content_amortization": half_from_year("content_amortization"),
        "cash_2026": cash_2026,
    }


_BAL_DATES = {"2025-12-31": "2025H2", "2026-06-30": "2026H1", "2026-09-30": "2026Q3"}
_BAL_FULL = ["bs_ppe", "bs_goodwill", "bs_rou", "bs_content_assets", "bs_intangibles_other", "bs_dta",
             "bs_loans_customers_nc", "bs_debt_securities_nc", "bs_other_nc_assets", "bs_inventories",
             "bs_trade_receivables", "bs_advances_paid", "bs_vat_receivable", "bs_loans_customers_c",
             "bs_debt_securities_c", "bs_unsettled_receivables", "bs_cash", "bs_other_c_assets", "bs_total_assets",
             "bs_equity", "bs_debt_nc", "bs_lease_nc", "bs_dtl", "bs_fs_liabilities_nc", "bs_other_nc_liabilities",
             "bs_payables", "bs_debt_c", "bs_fs_liabilities_c", "bs_income_tax_payable", "bs_other_taxes_payable",
             "bs_contract_liabilities", "bs_provisions", "bs_lease_c",
             "oca_other_receivables", "oca_fin_investments", "oca_income_tax_prepaid", "oca_net_lease_investment",
             "oca_loans_employees", "oca_loans_third", "oca_contract_assets", "oca_loans_related", "oca_other",
             "pay_trade", "pay_supply_financing", "pay_payroll", "pay_content", "pay_other",
             "debt_loans", "debt_bonds", "debt_supply_financing",
             "onca_loans_employees", "onca_loans_related", "onca_loans_third",
             "onca_net_lease_investment"]          # книга 1.3 (решение Г8): долгосрочные чистые инвестиции в аренду
_BAL_Q3 = ["bs_cash", "bs_debt_nc", "bs_debt_c", "bs_lease_nc", "bs_lease_c", "bs_fs_liabilities_nc",
           "bs_fs_liabilities_c", "bs_loans_customers_nc", "bs_loans_customers_c", "bs_debt_securities_nc",
           "bs_debt_securities_c", "bs_content_assets",
           # строки оборотного капитала на 30.09.2026 (датабук, ОФП) — проверка потока 9М26 (§13.2, книга 1.2)
           "bs_inventories", "bs_trade_receivables", "bs_advances_paid", "bs_vat_receivable",
           "bs_unsettled_receivables", "bs_other_c_assets", "bs_payables", "bs_taxes_payable",
           "bs_contract_liabilities", "bs_provisions"]
_BAL_ALL =["lease_office_c", "lease_office_nc", "bank_capital_f123", "f101_301", "f101_302", "f101_319", "f101_321",
            "f101_reserve_321", "f101_322", "f101_501", "andebt_debt", "andebt_lease", "andebt_cash", "adj_net_debt"]
_NWC_PLUS = ["bs_inventories", "bs_trade_receivables", "bs_advances_paid", "bs_vat_receivable",
             "bs_unsettled_receivables", "bs_other_c_assets", "bs_content_assets"]
_NWC_MINUS = ["oca_income_tax_prepaid", "oca_net_lease_investment", "oca_fin_investments", "oca_loans_employees",
              "oca_loans_third", "oca_loans_related", "bs_payables", "bs_other_taxes_payable",
              "bs_contract_liabilities", "bs_provisions"]
# Валовой долг на всех датах баланса окна (для спреда якоря): дата → период листа «на конец»
_DEBT_DATES = {"2023-12-31": "2023H2", "2024-06-30": "2024H1", "2024-09-30": "2024Q3", "2024-12-31": "2024H2",
               "2025-03-31": "2025Q1", "2025-06-30": "2025H1", "2025-09-30": "2025Q3", "2025-12-31": "2025H2",
               "2026-03-31": "2026Q1", "2026-06-30": "2026H1", "2026-09-30": "2026Q3"}


def _balance_json() -> dict:
    out = {"unit": "bn_rub",
           "source": "30.06.2026 и 31.12.2025 — МСФО 6М2026, стр. 4, 14, 17, 18; 30.09.2026 — датабук, лист ОФП; "
                     "офисная аренда — сноска ОФП; банк — ф.101, ф.123; скорр. чистый долг — пресс-релизы"}
    for date, p in _BAL_DATES.items():
        lines = _BAL_FULL if p != "2026Q3" else _BAL_Q3
        node = {m: _leaf(m, "", p) for m in lines + _BAL_ALL}
        if p != "2026Q3":
            node.update({f"rou_{c}": _leaf("rou_balance", c, p) for c in ("premises", "cars", "land", "other", "total")})

        def t(*ms, sign=1):
            return [(sign, m, "", p) for m in ms]
        node["debt"] = _calc(t("bs_debt_nc", "bs_debt_c"), "bs_debt_nc + bs_debt_c")
        node["lease_total"] = _calc(t("bs_lease_nc", "bs_lease_c"), "bs_lease_nc + bs_lease_c")
        node["lease_nonoffice"] = _calc(t("bs_lease_nc", "bs_lease_c") + t("lease_office_c", "lease_office_nc", sign=-1),
                                        "аренда всего − операционная (офисы): машины с выкупом и склады")
        node["fs_liabilities"] = _calc(t("bs_fs_liabilities_nc", "bs_fs_liabilities_c"), "долгоср. + краткоср.")
        node["loans_customers"] = _calc(t("bs_loans_customers_nc", "bs_loans_customers_c"), "внеоборотные + оборотные")
        node["debt_securities"] = _calc(t("bs_debt_securities_nc", "bs_debt_securities_c"), "внеоборотные + оборотные")
        bank = (t("bs_fs_liabilities_nc", "bs_fs_liabilities_c", "bank_capital_f123")
                + t("bs_loans_customers_nc", "bs_loans_customers_c", "bs_debt_securities_nc", "bs_debt_securities_c",
                    sign=-1))
        nd = (t("bs_debt_nc", "bs_debt_c", "bs_lease_nc", "bs_lease_c")
              + t("lease_office_c", "lease_office_nc", "bs_cash", sign=-1))
        node["bank_money"] = _calc(bank, "устав, раздел 4: обязательства ФС + капитал банка (ф.123) − кредиты клиентам − "
                                         "долговые бумаги")
        node["f101_liquidity"] = _calc(t("f101_301", "f101_302", "f101_319", "f101_321", "f101_322")
                                       + t("f101_reserve_321", sign=-1),
                                       "ф.101: 301 + 302 + 319 + 32.1 − резерв 32.1 + 32.2 (сверка денег банка)")
        node["net_debt_company"] = _calc(nd, "долг + аренда машин и складов − все деньги группы (скорр. чистый долг "
                                             "компании из строк баланса)")
        node["net_debt_nonbank"] = _calc(nd + bank, "net_debt_company + bank_money")
        if p != "2026Q3":
            node["nwc_nonbank"] = _calc(t(*_NWC_PLUS) + t(*_NWC_MINUS, sign=-1),
                                        "методика §4.10 и раздел 4 рабочего файла: запасы + торговая ДЗ + авансы "
                                        "выданные + НДС к возмещению + незавершённые расчёты + контентные активы + "
                                        "прочие оборотные активы без авансов по налогу на прибыль, чистых инвестиций в "
                                        "аренду, финансовых вложений и выданных займов − кредиторская задолженность (с "
                                        "финансированием поставок) − прочие налоги − обязательства по договорам − "
                                        "оценочные обязательства")
            node["bridge_fin_investments"] = _calc(t("oca_fin_investments"), "вложения в прочие финансовые активы "
                                                                             "(актив моста)")
            node["bridge_loans_issued"] = _calc(t("oca_loans_employees", "oca_loans_third", "oca_loans_related"),
                                                "займы выданные, оборотные: сотрудникам + третьим лицам + связанным "
                                                "(актив моста)")
            node["loans_issued_noncurrent"] = _calc(t("onca_loans_employees", "onca_loans_third", "onca_loans_related"),
                                                    "займы выданные, внеоборотные (в прочих внеоборотных активах; "
                                                    "решением раздела 4 не названы)")
        if p == "2026H1":
            node["bridge_autoru_receivable"] = _calc(t("autoru_consideration") + t("autoru_cash_received", sign=-1),
                                                     "остаток оплаты за Авто.ру к получению: вознаграждение − "
                                                     "получено (актив моста)")
        out[date] = node
    out["debt_history"] = {d: _calc([(1, "bs_debt_nc", "", p), (1, "bs_debt_c", "", p)], "валовой долг на дату")
                           for d, p in _DEBT_DATES.items()}
    # Классы ОС (30.06.2026) и прочих НМА (31.12.2025: на 30.06.2026 по классам не раскрыты) — первоначальная стоимость
    # и сроки полезного использования из учётной политики МСФО 2025 (срок амортизации, методика §4.9, §17)
    def classes(gross, lmin, lmax, period, names):
        return {c: {"gross": _leaf(gross, c, period),
                    **({"life_min": _leaf(lmin, c, "2025"), "life_max": _leaf(lmax, c, "2025")} if c != "cip" else {})}
                for c in names}
    out["asset_classes"] = {
        "ppe_2026-06-30": classes("ppe_gross", "ppe_life_min", "ppe_life_max", "2026H1",
                                  ("infra", "office", "leasehold", "land_buildings", "other", "cip")),
        "intangibles_2025-12-31": classes("ia_gross", "ia_life_min", "ia_life_max", "2025H2",
                                          ("trademarks", "customers", "technology", "other", "cip")),
    }
    # Флоатеры Яндекса: купон = ключевая + спред (спред долга, методика §4.13, §17)
    out["floater_spread"] = {s: _leaf("floater_spread", s, "2026Q4") for s in ("1R3", "1R4")}
    return out


def _macro_json() -> dict:
    q = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]
    out = {
        "unit": "pct",
        "cpi_yoy": {p: _leaf("cpi_yoy", "", p) for p in q + ["2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]},
    }
    out["cpi_yoy"]["12m_2026Q3"] = _leaf("cpi_yoy", "ltm", "2026Q3")      # ИПЦ «12 мес.» (§0.3, книга 1.2)
    out.update({
        "cpi_yoy_rosstat": {p: _leaf("cpi_yoy_rosstat", "", p) for p in q},
        "key_rate_avg": {p: _leaf("key_rate_avg", "", p) for p in ("2025H1", "2025H2", "2026H1", "2026Q3")},
        "ad_market_internet_yoy": {"akar_2026Q1": _leaf("ad_market_internet_yoy", "akar", "2026Q1"),
                                   "arir_2026H1": _leaf("ad_market_internet_yoy", "arir", "2026H1"),
                                   "note": "чужие оценки в цитате эмитента, не раскрытие"},
    })
    return out


def _equity_json() -> dict:
    def t(m, p):
        return _leaf(m, "", p)
    return {
        "unit": "mln_shares",
        "2026-06-30": {m: t(m, "2026H1") for m in ("shares_issued", "shares_admin", "shares_outstanding",
                                                   "options_outstanding", "options_exercisable")},
        "2025-12-31": {m: t(m, "2025H2") for m in ("shares_issued", "shares_admin", "shares_outstanding",
                                                   "options_outstanding", "options_exercisable")},
        "2024-12-31": {"shares_outstanding": t("shares_outstanding", "2024H2")},
        "2023-12-31": {"shares_outstanding": t("shares_outstanding", "2023H2")},
        "dividends": [
            {"for": "2025", "per_share_rub": t("dividend_per_share", "2025"), "total_bn_rub": t("dividend_total", "2025"),
             "paid": "2026Q2", "note": "выплачен до якоря (МСФО 6М2026, стр. 30)"},
            {"for": "2026H1", "per_share_rub": t("dividend_per_share", "2026H1"),
             "total_bn_rub": t("dividend_total", "2026H1"), "record_date": "2026-09-21", "paid": "2026Q3",
             "paid_bn_rub": t("dividends_paid", "2026Q3"),
             "note": "требование с отсечки 21.09.2026 (устав, раздел 4); дата — рекомендация СД (src в per_share_rub)"},
        ],
    }


def _facts_mapping() -> dict[str, Any]:
    return {"segments.json": _segments_json(), "group.json": _group_json(), "balance.json": _balance_json(),
            "equity.json": _equity_json(), "macro.json": _macro_json()}


facts_mapping: dict[str, Any] = _facts_mapping()


# ------------------------------------------------------------------------------------------------------------------
# Шапка выпуска (common.header, family-header-v1): поля — из выпуска yandex-v1; уровни флагов §13.6 — здесь.

FLAG_LEVELS = {"report_fact": "warn", "dividend_register": "warn", "price_fallback": "warn", "book_update": "info",
               "stale_release": "alert"}
# Живые входы, отказ которых желтит шапку (наблюдаемые источники сборщика, indicators.collect.WATCHED): иначе отказ
# кривой или ключевой ставки виден только в `live.errors`, а хаб с шапкой это поле не читает (аудит 422).
LIVE_WATCHED = ("quotes", "price_history", "curve", "key_rate")
LIVE_NAMES = {"quotes": "котировки ISS", "price_history": "история цены ISS", "curve": "кривая ОФЗ",
              "key_rate": "ключевая ставка ЦБ"}
GATE_ACTION = ("продлить объяснение в data/assumptions/gate_explanations.yaml с числами действующей книги или убрать "
               "причину срабатывания")


def _periods_closed(first: str, n: int) -> list[str]:
    from common.calendar import next_period
    out, p = [], first
    for _ in range(int(n or 0)):
        out.append(p)
        p = next_period(p)
    return out


def _book_deadlines() -> list[dict]:
    """Записки выпуска (data/assumptions/release_notes.yaml): после срока скачок заголовка без новой книги роняет
    сборку (§13.6)."""
    import yaml
    from model.payload import RELEASE_NOTES
    if not RELEASE_NOTES.exists():
        return []
    notes = (yaml.safe_load(RELEASE_NOTES.read_text(encoding="utf-8")) or {}).get("notes") or []
    return [{"date": str(n["valid_until"]), "kind": "book",
             "what": f"записка выпуска от {n.get('date', '—')} (ожидаемая медиана {n.get('expected_median')} ₽) "
                     "действует до этой даты: после неё скачок заголовка без новой книги или фактов уронит сборку",
             "action": "новая книга или новая записка data/assumptions/release_notes.yaml"}
            for n in notes if isinstance(n, dict) and n.get("valid_until")]


def header_inputs(payload: dict, today: str | None = None) -> dict:
    """Входы шапки выпуска (`common.header.build`) из выпуска yandex-v1; `today` — день состояния (нет — сегодня по
    Москве, `FAKE_TODAY`)."""
    from pathlib import Path

    from common.cli import WORLDS_ENGINE_REL
    from common.layer import sha256_bytes
    from model.book import today as _today
    root = Path(__file__).resolve().parents[1]
    meta, head, fv = payload["meta"], payload["headline"], payload["fair_value"]
    mk, book, ck = payload["market"], payload["book"], payload["checks"]
    src = payload["worlds"]["source"]
    eng = root / WORLDS_ENGINE_REL                       # движок приёма миров этого дерева (его sha — в шапку)
    engine = sha256_bytes(eng.read_bytes()) if eng.exists() else None
    closed = _periods_closed(meta["first_period"], meta.get("closed_periods") or 0)
    gates = ck.get("gates") or []
    deadlines = [{"date": g["valid_until"], "kind": "explanation",
                  "what": f"объяснение гейта «{g['title']}» ({g['name']}) действует до этой даты; гейт "
                          + ("сработал: после срока сборка откажет" if g.get("fired") else
                             "не сработал: если сработает после срока, сборка откажет"),
                  "action": GATE_ACTION} for g in gates if g.get("valid_until")]
    cal = root / "data" / "calendar.json"
    flags = [{"name": f["name"], "level": FLAG_LEVELS.get(f["name"], "warn"), "title": f["title"]}
             for f in ck.get("flags") or [] if f.get("raised")]
    failed = [n for n in LIVE_WATCHED if n in ((payload.get("live") or {}).get("errors") or {})]
    if failed:
        flags.append({"name": "live_inputs", "level": "warn",
                      "title": "Часть живых входов не собрана: " + ", ".join(LIVE_NAMES[n] for n in failed)})
    return {
        "model": {"slug": SLUG, "tickers": list(TICKERS), "name": NAME},
        "headline": {"printed": head["printed_central"], "median": head["central"], "point": fv["central"],
                     "band80": list(head["band"]), "band50": list(head["inner"]), "print_step": head["print_step"],
                     "p_below_market": head["p_central_below_market"]},
        "market": {"ticker": TICKERS[0], "price": mk["price"], "price_date": mk["price_date"]},
        "book": {"version": book["version"], "date": book["date"], "tag": book["tag"]},
        "facts": {"anchor_period": meta["anchor_period"], "facts_date": meta["facts_date"],
                  "first_period": meta["first_period"], "open_period": meta.get("open_period"),
                  "periods_closed": closed, "first_period_closed": meta["first_period"] in closed},
        "worlds": {"version": str(src["version"]), "record_asof": src["record_asof"], "record_sha256": src["sha256"],
                   "engine_sha256": engine},
        "release": {"generated_at": meta["generated_at"], "valuation_date": meta["valuation_date"],
                    "engine_commit": meta["engine_commit"], "fast": bool(meta.get("fast"))},
        "flags": flags,
        "gates": [{"key": g["name"], "valid_until": g.get("valid_until"), "mass": g.get("mass"),
                   "explained": bool(g.get("explanation")) and g.get("status") not in ("unexplained", "expired")}
                  for g in gates if g.get("fired")],
        "deadlines": deadlines + _book_deadlines(),
        "invariants": [f"{i.get('name')}: {i.get('detail') or 'нарушен'}" for i in ck.get("invariants") or []
                       if not i.get("ok")],
        "calendar": str(cal) if cal.exists() else None,
        "today": today or _today().isoformat(),
    }


# ------------------------------------------------------------------------------------------------------------------
# Приём отчёта (python -m common.cli report): перенос книги на якорь периода. Модель держит якорь книги — 2026H1:
# факты и перевывод стоят на нём (окна «12 мес. к 30.06.2026», ряды ltm_2026H1, akar_2026Q1, arir_2026H1 в
# model/facts.py), а ожидаемый путь ядра (model.grid.expected_path) даёт только выручку и EBITDA блоков полугодия —
# не баланс, налог, вложения и акции на конец периода. Поэтому перенос подключён для периода якоря (повторный приём
# того же отчёта: факты листа → перевывод книги → число), а на следующий период — «не подключено» с причиной.

REANCHOR_DRAWS: int | None = None      # прогонов полосы переноса; None — малая полоса книги (тест такта ставит меньше)
REANCHOR_TOLERANCE = 0.5               # ₽: механика ≈ 0 (1 % шага печати)


def facts_sheets(period: str) -> list[str]:
    """Листы фактов периода: якорь 2026H1 собирается из листов 2026H1 и 2026Q3 (FACTS_SHEETS)."""
    return list(FACTS_SHEETS) if period == FACTS_SHEETS[0] else [period]


def _anchor() -> str:
    from model.book import load_book
    return load_book()["meta"]["anchor_period"]


def _not_connected(period: str, anchor: str) -> NotImplementedError:
    return NotImplementedError(
        f"перенос книги на якорь {period} в модели не подключён: факты и перевывод стоят на якоре {anchor} (окна "
        "«12 мес. к 30.06.2026», ряды ltm_2026H1, akar_2026Q1, arir_2026H1 в model/facts.py), а ожидаемый путь ядра "
        "даёт только выручку и EBITDA блоков полугодия — без баланса, налога, вложений и акций на конец периода; "
        "перенос якоря в ядре — отдельная задача")


def expected_path(period: str) -> dict:
    """Ожидаемый путь книги на период — факты для теста «на ожидаемом пути = перекат». На якоре книги ожидание = её
    факты (data/facts/*.json); дальше якоря — не подключено (см. выше)."""
    from model.facts import default_facts_dir
    anchor = _anchor()
    if period != anchor:
        raise _not_connected(period, anchor)
    d = default_facts_dir()
    return {name: (d / name).read_text(encoding="utf-8") for name in facts_mapping}


def _value(book_path, facts_dir) -> dict:
    """Медиана малой полосы и точка книги на фактах — на случайных числах книги (как `python -m common.cli quick`)."""
    from model import quick, uncertainty as U
    from model.book import load_book
    from model.checks import round_to_step
    from model.facts import core_facts, load_facts

    A = load_book(book_path)
    try:
        res = quick.run(A, core_facts(load_facts(facts_dir), A["meta"]["anchor_period"]), draws=REANCHOR_DRAWS,
                        n_workers=U.SMALL_WORKERS)
    finally:
        U.close_pool()
    med = res["stats"]["median"]
    return {"median": med, "point": res["point"]["central"], "printed": round_to_step(med, res["print_step"]),
            "draws": res["draws"], "seed": res["seed"], "asof": res["asof"], "book_version": str(res["book_version"])}


def reanchor(facts: dict, period: str, out_dir: str) -> dict:
    """Перенос книги на якорь `period` по фактам `{файл: текст JSON}`; кандидат (книга и факты) — в `out_dir`.

    На якоре книги: факты → `out_dir/facts`, книга (data/assumptions) → `out_dir/book`, перевывод выведенных чисел на
    копии (`python -m model.rederive --book`), число до и после — медиана малой полосы и точка на тех же случайных
    числах. Перекат = 0: якорь и дата оценки те же. `model.rederive` читает только data/facts, поэтому кандидат на
    фактах, отличных от data/facts, — отказ с причиной."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from common.facts_sheet import write_facts
    from model.book import BOOK_DIR, default_book_path
    from model.facts import default_facts_dir

    anchor = _anchor()
    if period != anchor:
        raise _not_connected(period, anchor)
    root = Path(__file__).resolve().parents[1]
    out = Path(out_dir)
    fdir, bdir = out / "facts", out / "book"
    for d in (fdir, bdir):
        if d.exists():
            shutil.rmtree(d)
    write_facts(facts, fdir)
    data_facts = default_facts_dir()
    differ = sorted(n for n in facts if not (data_facts / n).exists()
                    or (data_facts / n).read_bytes() != (fdir / n).read_bytes())
    if differ:
        raise RuntimeError(f"факты кандидата отличаются от data/facts ({', '.join(differ)}): перевывод "
                           "(python -m model.rederive) читает только data/facts — кандидат на других фактах того же "
                           "якоря этой командой не собирается")
    shutil.copytree(BOOK_DIR, bdir)
    book = bdir / default_book_path().name
    before_bytes = book.read_bytes()
    r = subprocess.run([sys.executable, "-B", "-m", "model.rederive", "--book", str(book)], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    (out / "rederive.txt").write_text(r.stdout + r.stderr, encoding="utf-8", newline="\n")
    if r.returncode != 0:
        raise RuntimeError(f"перевывод на копии книги упал (код {r.returncode}): {(r.stdout + r.stderr)[-400:]}")
    total = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("ИТОГ")), "ИТОГ не напечатан")
    before = _value(default_book_path(), data_facts)
    after = _value(book, fdir)
    return {"before": before, "rolled": dict(before), "after": after, "anchor_before": anchor,
            "valuation_date": before["asof"],
            "candidate": {"книга": book.as_posix(), "факты": fdir.as_posix(),
                          "перевывод": (out / "rederive.txt").as_posix()},
            "notes": [f"перевывод на копии книги: {total}",
                      "книга кандидата " + ("байт в байт = data/assumptions" if book.read_bytes() == before_bytes
                                           else "ИЗМЕНЕНА перевыводом"),
                      "факты кандидата байт в байт = data/facts",
                      f"перекат 0: якорь {anchor} и дата оценки {before['asof']} те же; прогонов {after['draws']}, "
                      f"семя {after['seed']}"]}
