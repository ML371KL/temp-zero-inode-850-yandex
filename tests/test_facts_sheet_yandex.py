# -*- coding: utf-8 -*-
"""Лист фактов Яндекса (задача плана 3): лист проходит проверку слоя, факты = сборка из листа (байт в байт),
приёмка устава «A–G + исключения = группа 1П26 до 0,1 млрд», сверки листа с МСФО и релизами."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.facts_sheet import check_built, check_dir, load_sheet
from ops import common_adapter

ROOT = Path(__file__).resolve().parents[1]
FACTS_DIR = ROOT / "data" / "facts"
SHEETS_DIR = FACTS_DIR / "sheets"
BLOCKS = "ABCDEFG"
LTM = ("2025Q4", "2026Q1", "2026Q2", "2026Q3")      # «12 мес.» устава


def _sheets():
    return [load_sheet(SHEETS_DIR, p) for p in common_adapter.FACTS_SHEETS]


def _json(name):
    return json.loads((FACTS_DIR / name).read_text(encoding="utf-8"))


def test_sheets_pass_layer_check():
    assert check_dir(SHEETS_DIR) == []


def test_facts_equal_build_from_sheet():
    assert check_built(_sheets(), common_adapter.facts_mapping, FACTS_DIR) == []


@pytest.mark.parametrize("metric", ["revenue", "adj_ebitda"])
def test_blocks_and_exclusions_equal_group_2026h1(metric):
    """Устав, раздел 5: A–G + исключения = группа 1П26 до 0,1 млрд; группа — МСФО 6М2026, стр. 12.
    Исключения X — межсегментные и внутрисегментные расчёты (раздел 4 рабочего файла, устав раздел 1)."""
    seg = _json("segments.json")
    b = seg["blocks"]
    for p in ("2026H1", "2026Q3", "2025"):
        assert abs(b["X"][metric][p] - b["X_inter"][metric][p] - b["X_intra"][metric][p]) < 1e-9
    total = sum(b[k][metric]["2026H1"] for k in BLOCKS) + b["X"][metric]["2026H1"]
    group_ifrs = seg["ifrs_6m"]["group"][metric]["2026H1"]["v"]
    assert abs(total - group_ifrs) <= 0.1


def test_databook_quarters_match_ifrs_half_years():
    """Сумма кварталов датабука = сегменты МСФО 6М (2025H1, 2026H1) до 2 млн руб. (округление кварталов)."""
    seg = _json("segments.json")
    ifrs, s = seg["ifrs_6m"], seg["segments"]
    pairs = {k: [k] for k in ifrs if k != "ifrs_elim"}
    pairs["ifrs_elim"] = ["groups_elim", "city_elim", "personal_elim"]
    for code, parts in pairs.items():
        for metric in ("revenue", "adj_ebitda"):
            for h, qs in (("2025H1", ("2025Q1", "2025Q2")), ("2026H1", ("2026Q1", "2026Q2"))):
                db = sum(s[p][metric][q]["v"] for p in parts for q in qs)
                assert abs(db - ifrs[code][metric][h]["v"]) <= 0.002, (code, metric, h)


@pytest.mark.parametrize("block,revenue,ebitda", [
    ("A", 567.7, 253.2), ("B", 313.1, 135.2), ("C", 611.0, -27.5), ("D", 154.3, 4.5), ("E", 59.2, 14.5),
    ("F", 13.8, -46.8), ("G", 120.7, 3.3), ("X", -234.2, 1.5),
])
def test_ltm_levels_match_charter(block, revenue, ebitda):
    """Уровни «12 мес.» устава (раздел 1) — из листа; X — меж- и внутрисегментные расчёты."""
    b = _json("segments.json")["blocks"][block]
    assert abs(sum(b["revenue"][q] for q in LTM) - revenue) <= 0.05
    assert abs(sum(b["adj_ebitda"][q] for q in LTM) - ebitda) <= 0.05


def test_net_debt_from_balance_matches_company_reconciliation():
    """Долг + аренда машин и складов − деньги из строк баланса = скорр. чистый долг релиза (до 0,05 млрд)."""
    bal = _json("balance.json")
    for date in ("2025-12-31", "2026-06-30", "2026-09-30"):
        n = bal[date]
        assert abs(n["net_debt_company"]["v"] - n["adj_net_debt"]["v"]) <= 0.05, date
        assert abs(n["lease_nonoffice"]["v"] - n["andebt_lease"]["v"]) <= 0.05, date


def test_bank_money_rule_on_anchor():
    """Устав, раздел 4: чистый долг без банка на 30.06.2026 ≈ 200–210 млрд; деньги банка по правилу — против
    ликвидности ф.101 на 01.07.2026 (сверка, расхождение печатается в out/104-facts.md)."""
    n = _json("balance.json")["2026-06-30"]
    assert 200 <= n["net_debt_nonbank"]["v"] <= 210
    assert abs(n["bank_money"]["v"] - n["f101_liquidity"]["v"]) <= 10


def test_cpi_calculation_matches_rosstat_quarterly_series():
    """ИПЦ г/г (средний индекс периода к тому же периоду годом раньше, из месячных индексов) против квартального
    ряда Росстата «квартал к соответствующему кварталу предыдущего года»: до 0,025 п.п. (округление индексов)."""
    m = _json("macro.json")
    for p, node in m["cpi_yoy_rosstat"].items():
        assert abs(m["cpi_yoy"][p]["v"] - node["v"]) <= 0.025, p


def test_bridge_items_are_outside_working_capital():
    """Раздел 4 рабочего файла: финансовые вложения и выданные займы — в мосту, не в оборотном капитале;
    оборотный капитал без них + они = сумма строк до исключения (тождество сборки)."""
    n = _json("balance.json")["2026-06-30"]
    assert abs(n["bridge_fin_investments"]["v"] - 10.005) < 1e-9
    assert abs(n["bridge_loans_issued"]["v"] - 3.353) < 1e-9
    assert abs(n["bridge_autoru_receivable"]["v"] - 3.5) < 1e-9
    lines = ["bs_inventories", "bs_trade_receivables", "bs_advances_paid", "bs_vat_receivable", "bs_unsettled_receivables",
             "bs_other_c_assets", "bs_content_assets"]
    minus = ["oca_income_tax_prepaid", "oca_net_lease_investment", "bs_payables", "bs_other_taxes_payable",
             "bs_contract_liabilities", "bs_provisions"]
    gross = sum(n[k]["v"] for k in lines) - sum(n[k]["v"] for k in minus)
    assert abs(n["nwc_nonbank"]["v"] + n["bridge_fin_investments"]["v"] + n["bridge_loans_issued"]["v"] - gross) < 1e-9
