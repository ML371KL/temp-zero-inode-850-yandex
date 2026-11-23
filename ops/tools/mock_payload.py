"""Мок выпуска yandex-v1 для проверки витрины без ядра.

Пишет tests/fixtures/sample_payload.json строго по docs/PAYLOAD.md: все блоки
REQUIRED_TOP_LEVEL и поля в той форме, в какой их кладёт сборка выпуска
(семь блоков сервисов A–G и исключения X, банк вне долга, развилки). Числа мока
НИЧЕГО не значат: сетка, слои, прогоны, история и путь по годам — правдоподобная
иллюстрация, чтобы проверить вёрстку. Самосогласованы только правила витрины:
заголовок, полосы, P(ниже рынка) и таблица by_lambda посчитаны из тех же
прогонов, что лежат в выпуске (квантиль тип 7, печать к шагу, половина вверх), —
это проверка ползунка λ.

Сверх того, что сейчас кладёт выпуск ядра, мок заполняет блоки, которые
контракт описывает, а первая версия оставляет пустыми (календарь, история цены,
живая кривая и плитки, прошлый выпуск): так витрина проверяется и на них.

    python ops/tools/mock_payload.py [выход.json]
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "sample_payload.json"

REQUIRED_TOP_LEVEL = ["schema", "meta", "market", "headline", "fair_value", "layers", "grid", "worlds",
                      "regimes", "capex_levels", "paths", "debt", "dividends", "history", "reverse_dcf",
                      "judgements", "uncertainty", "next_report", "journal", "calendar", "checks", "inputs",
                      "live", "changes", "book", "indicators"]

SHARES = 390.2          # млн акций, делитель (иллюстрация)
SHARES_OUT = 381.0      # млн акций в обращении (иллюстрация)
GOV = 0.03
PRICE = 3200.0
CLAIMS = 180.0          # млрд ₽, требования на дату оценки (иллюстрация)
STEP = 50
DRAWS = 1000
LAMBDA = 0.5
VALUATION = "2026-11-20"

BLOCKS = ["A", "B", "C", "D", "E", "F", "G", "X"]
TITLES = {"A": "Поиск и ИИ", "B": "Райдтех", "C": "Электронная коммерция и доставка",
          "D": "Плюс, развлечения, Свои Плюсы", "E": "Б2Б Тех", "F": "Автономные технологии и прочие",
          "G": "Финансовые сервисы", "X": "Исключения"}
# Иллюстрация: выручка блока за 1П 2024, рост за полугодие, маржа EBITDA (у F — расход).
BASE = {"A": (215.0, 0.040, 0.44), "B": (100.0, 0.070, 0.40), "C": (170.0, 0.090, -0.08),
        "D": (45.0, 0.080, 0.02), "E": (14.0, 0.090, 0.20), "F": (5.0, 0.050, -3.0), "G": (12.0, 0.180, 0.02)}
X_SHARE = 0.16          # исключения — доля суммы блоков (иллюстрация)


def r(x, k=4):
    return None if x is None else round(float(x), k)


def half_up(x):
    return math.floor(x + 0.5)


def printed(x):
    return half_up(x / STEP) * STEP


def q7(sorted_vals, q):
    n = len(sorted_vals)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def price_of(equity):
    return equity * (1 - GOV) * 1000 / SHARES if equity > 0 else equity * 1000 / SHARES


def halves(first, last):
    out = []
    y, h = first
    while (y, h) <= last:
        out.append(f"{y}H{h}")
        y, h = (y, 2) if h == 1 else (y + 1, 1)
    return out


def block_rows(i, margin_shift=0.0):
    """Блоки полугодия номер i от 1П 2024 (иллюстрация), с исключениями X."""
    out = {}
    for k, (rev0, g, m) in BASE.items():
        rev = rev0 * (1 + g) ** i * (1.08 if i % 2 else 1.0)
        out[k] = {"revenue": r(rev, 3), "ebitda": r(rev * (m + (margin_shift if k != "F" else 0.0)), 3)}
    s = sum(v["revenue"] for v in out.values())
    out["X"] = {"revenue": r(-s * X_SHARE, 3), "ebitda": r(0.3, 3)}
    return out


def group(blocks):
    rev = sum(v["revenue"] for v in blocks.values())
    ebitda = sum(v["ebitda"] for v in blocks.values())
    return r(rev, 3), r(ebitda, 3), r(ebitda / rev, 6)


def main(out: Path) -> None:
    rng = random.Random(20261120)
    W = ["N", "H", "M"]
    WNAMES = {"N": "Нормализация", "H": "Высокие ставки надолго", "M": "Рыночный как есть"}
    REG = ["stress", "slow", "base", "strong"]
    RTITLES = {"stress": "Стресс", "slow": "Медленный", "base": "Базовый", "strong": "Сильный"}
    CAP = ["low", "base", "high"]
    CTITLES = {"low": "Низкий", "base": "Базовый", "high": "Высокий"}
    p_reg = {k: 0.25 for k in REG}
    p_cap = {"low": 0.25, "base": 0.5, "high": 0.25}
    # Уровень капиталоёмкости зависит от режима: в стрессе — не выше базового, в сильном — не ниже.
    p_cap_reg = {"stress": {"low": 0.5, "base": 0.5, "high": 0.0}, "slow": dict(p_cap), "base": dict(p_cap),
                 "strong": {"low": 0.0, "base": 0.5, "high": 0.5}}
    layer_w = {"analytical": {"N": 0.35, "H": 0.45, "M": 0.20},
               "market_implied": {"N": 0.10, "H": 0.25, "M": 0.65},
               "macro_neutral": {"N": 0.0, "H": 0.0, "M": 1.0}}
    layer_titles = {"analytical": "свой макро-взгляд", "market_implied": "веса, вменённые рынком",
                    "macro_neutral": "рыночные ставки как есть"}

    # ── сетка (иллюстрация) ──
    fW = {"N": 1.18, "H": 0.92, "M": 0.95}
    fR = {"stress": 0.70, "slow": 0.92, "base": 1.10, "strong": 1.28}
    fC = {"low": 1.06, "base": 1.00, "high": 0.94}
    ebitda_2027 = 400.0
    cells = []
    for w in W:
        for g in REG:
            for c in CAP:
                ev = 1500.0 * fW[w] * fR[g] * fC[c]
                eq = ev - CLAIMS
                pr = p_reg[g] * p_cap_reg[g][c]
                cells.append({
                    "world": w, "regime": g, "capex": c,
                    "p_analytical": r(layer_w["analytical"][w] * pr, 6),
                    "p_market_implied": r(layer_w["market_implied"][w] * pr, 6),
                    "p_neutral": r(layer_w["macro_neutral"][w] * pr, 6),
                    "ev": r(ev, 2), "d": CLAIMS, "equity": r(eq, 2), "price": r(price_of(eq), 1),
                    "c_T": r(0.05 + 0.005 * (fW[w] - 1), 4),
                    "terminal_share": r(0.52 + 0.05 * (fW[w] - 1) + 0.04 * (fR[g] - 1), 3),
                    "ebitda_2027": r(ebitda_2027 * (0.9 + 0.1 * fR[g]), 2),
                    "ev_ebitda_2027": r(ev / (ebitda_2027 * (0.9 + 0.1 * fR[g])), 3),
                    "margin_2030": r(0.20 + 0.06 * (fR[g] - 1), 4),
                })

    def layer(name):
        wts = layer_w[name]
        v0 = sum(cell["ev"] * wts[cell["world"]] * p_reg[cell["regime"]] * p_cap_reg[cell["regime"]][cell["capex"]] for cell in cells)
        eq = v0 - CLAIMS
        pv_tv, pv_shield, pv_tv_shield = v0 * 0.50, v0 * 0.022, v0 * 0.005
        return {"title": layer_titles[name], "world_weights": wts, "v0": r(v0, 2), "d": CLAIMS, "equity": r(eq, 2),
                "price": r(price_of(eq), 1), "pv_explicit": r(v0 - pv_tv - pv_shield - pv_tv_shield, 2),
                "pv_shield": r(pv_shield, 2), "pv_tv": r(pv_tv, 2), "pv_tv_shield": r(pv_tv_shield, 2),
                "terminal_share": r((pv_tv + pv_tv_shield) / v0, 4), "ebitda_2027": ebitda_2027,
                "ev_ebitda_2027": r(v0 / ebitda_2027, 3), "v0_to_d": r(v0 / CLAIMS, 3)}

    layers = {k: layer(k) for k in ("analytical", "market_implied", "macro_neutral")}
    low, high = layers["macro_neutral"]["price"], layers["analytical"]["price"]
    central = low + LAMBDA * (high - low)

    # ── прогоны по суждениям ──
    draws_low, draws_high = [], []
    for _ in range(DRAWS):
        shock = math.exp(rng.gauss(-0.03, 0.18))
        draws_low.append(round(max(low * shock * (1 + 0.02 * rng.gauss(0, 1)), 1.0), 1))
        draws_high.append(round(max(high * shock * (1 + 0.02 * rng.gauss(0, 1)), 1.0), 1))

    def stats(lam):
        c = sorted(a + lam * (b - a) for a, b in zip(draws_low, draws_high))
        return {"lambda": r(lam, 2), "point": r(low + lam * (high - low), 1), "median": r(q7(c, 0.5), 1),
                "p10": r(q7(c, 0.1), 1), "p25": r(q7(c, 0.25), 1), "p75": r(q7(c, 0.75), 1), "p90": r(q7(c, 0.9), 1),
                "mean": r(sum(c) / len(c), 2), "p_below": r(sum(1 for v in c if v < PRICE) / len(c), 4)}

    by_lambda = [stats(i / 20) for i in range(21)]
    s = stats(LAMBDA)
    median = s["median"]
    headline = {
        "central": median, "printed_central": printed(median),
        "band": [s["p10"], s["p90"]], "printed_band": [printed(s["p10"]), printed(s["p90"])],
        "inner": [s["p25"], s["p75"]], "printed_inner": [printed(s["p25"]), printed(s["p75"])],
        "mean": s["mean"], "p_central_below_market": s["p_below"], "market_price": PRICE,
        "print_step": STEP, "draws": DRAWS, "lambda": LAMBDA,
    }
    v_star = PRICE * SHARES / 1000 / (1 - GOV) + CLAIMS
    v0_med = median * SHARES / 1000 / (1 - GOV) + CLAIMS
    v0_pt = central * SHARES / 1000 / (1 - GOV) + CLAIMS
    fair_value = {
        "low": r(low, 2), "central": r(central, 2), "high": r(high, 2),
        "printed": {"low": printed(low), "central": printed(central), "high": printed(high)},
        "lambda": LAMBDA, "lambda_step": 0.05, "rates_view": {"rub": r(high - low, 2)},
        "by_lambda": by_lambda, "draws_low": draws_low, "draws_high": draws_high,
        "by_world": {w: r(price_of(sum(c["ev"] * p_reg[c["regime"]] * p_cap_reg[c["regime"]][c["capex"]] for c in cells if c["world"] == w) - CLAIMS), 2) for w in W},
        "center_ev": {"v0_median": r(v0_med, 2), "v0_point": r(v0_pt, 2), "v_star": r(v_star, 2),
                      "gap_median": r(v0_med / v_star - 1, 4), "gap_point": r(v0_pt / v_star - 1, 4),
                      "rub_per_1pct_ev_median": r(0.01 * v0_med * (1 - GOV) * 1000 / SHARES, 1),
                      "rub_per_1pct_ev_point": r(0.01 * v0_pt * (1 - GOV) * 1000 / SHARES, 1),
                      "ebitda_2027": ebitda_2027, "ev_ebitda_2027_median": r(v0_med / ebitda_2027, 3),
                      "ev_ebitda_2027_point": r(v0_pt / ebitda_2027, 3), "ev_ebitda_2027_market": r(v_star / ebitda_2027, 3)},
        "equity_share_of_ev": r((v0_pt - CLAIMS) / v0_pt, 4),
    }

    # ── миры ставок (иллюстрация) ──
    path_periods = halves((2026, 2), (2036, 2))
    worlds = {"order": W, "source": {"version": "1.7", "book": "magnit-850oa book-1.7", "sha256": "0" * 64,
                                     "record_asof": "2026-10-28", "curve_date": "2026-10-28"}}
    curve = {"N": (0.133, 0.120, 0.110, 0.100, 0.089), "H": (0.145, 0.152, 0.147, 0.138, 0.126),
             "M": (0.140, 0.149, 0.153, 0.155, 0.156)}
    for w in W:
        k0, k_lt = {"N": (0.14, 0.08), "H": (0.15, 0.10), "M": (0.15, 0.12)}[w]
        cpi0, cpi_lt = {"N": (0.066, 0.04), "H": (0.075, 0.055), "M": (0.08, 0.06)}[w]
        n = len(path_periods)
        v0_w = sum(c["ev"] * p_reg[c["regime"]] * p_cap_reg[c["regime"]][c["capex"]] for c in cells if c["world"] == w)
        worlds[w] = {
            "name": WNAMES[w], "weights": {k: layer_w[k][w] for k in layer_w},
            "key_rate": [{"period": p, "value": r(k_lt + (k0 - k_lt) * max(0.0, 1 - i / 8), 4)} for i, p in enumerate(path_periods)],
            "cpi": [{"period": p, "value": r(cpi_lt + (cpi0 - cpi_lt) * max(0.0, 1 - i / 6), 4)} for i, p in enumerate(path_periods)],
            "zero_curve": dict(zip(["1", "3", "5", "10", "LT"], curve[w])),
            "lt_inflation": cpi_lt, "r_terminal": r(curve[w][4] + 0.054, 4), "g_terminal": r((1.015) * (1 + cpi_lt) - 1, 4),
            "price": fair_value["by_world"][w], "v0": r(v0_w, 2)}
        assert n == 21

    # ── режимы: цели маржи блоков ──
    centers = {"A": (0.446, (0.36, 0.48), 2031, (-0.03, 0.02)), "B": (0.432, (0.31, 0.47), 2031, (-0.04, 0.02)),
               "C": (0.027, (-0.01, 0.06), 2033, (-0.04, 0.03)), "D": (0.08, (0.02, 0.15), 2033, None),
               "E": (0.25, (0.15, 0.30), 2033, None), "G": (0.08, (0.0, 0.15), 2033, None)}
    spread = {"stress": -1.0, "slow": -0.25, "base": 0.35, "strong": 0.9}
    regimes = {"order": REG, "titles": RTITLES, "prob": p_reg, "shift_to": 2030, "blocks": {},
               "note": "цели маржи режимов — середины квартилей распределения каждого суждения; вероятности режимов — из книги"}
    for k, (mc, rng_m, conv, shift) in centers.items():
        half = (rng_m[1] - rng_m[0]) / 2
        regimes["blocks"][k] = {
            "title": TITLES[k], "margin_center": mc, "margin_range": list(rng_m),
            "margin_targets": {g: r(mc + spread[g] * half * 0.5, 6) for g in REG}, "target_year": 2030,
            "growth_shift": ({g: r(spread[g] * 0.02, 6) for g in REG} if shift else {g: 0.0 for g in REG}),
            "shift_range": list(shift) if shift else None, "converge_year": conv}

    # Правило капиталоёмкости: уровни — капитал к выручке на конец 2030 г. (иллюстрация).
    capex_levels = {"order": CAP, "titles": CTITLES, "levels": None, "prob": p_cap, "levels_to": 2030, "converge_year": None,
                    "lease_share": 0.0016, "asset_life_years": 6.5, "rule": "kappa", "prob_given_regime": p_cap_reg,
                    "kappa_levels": {"low": 0.30, "base": 0.32, "high": 0.35}, "kappa_base_capex": 0.0995,
                    "kappa_high_capex": 0.11, "first_share": 0.11, "floor_da": 0.5}

    # ── история окна и путь по годам (иллюстрация) ──
    hist_periods = halves((2024, 1), (2026, 1))
    hist_halves = []
    for i, p in enumerate(hist_periods):
        b = block_rows(i)
        rev, ebitda, margin = group(b)
        hist_halves.append({"period": p, "revenue": rev, "ebitda": ebitda, "margin": margin, "blocks": b})
    hist_annual = []
    for y in (2024, 2025):
        hs = [h for h in hist_halves if h["period"].startswith(str(y))]
        b = {k: {"revenue": r(sum(h["blocks"][k]["revenue"] for h in hs), 3),
                 "ebitda": r(sum(h["blocks"][k]["ebitda"] for h in hs), 3)} for k in BLOCKS}
        rev, ebitda, margin = group(b)
        hist_annual.append({"period": str(y), "revenue": rev, "ebitda": ebitda, "margin": margin, "blocks": b, "year": y})

    path_halves = []
    for j, p in enumerate(path_periods):
        b = block_rows(len(hist_periods) + j, margin_shift=min(0.03, 0.003 * j))
        rev, ebitda, margin = group(b)
        path_halves.append({"period": p, "revenue": rev, "ebitda": ebitda, "margin": margin,
                            "fcff": r(rev * (0.05 + 0.004 * j), 3), "capex": r(rev * 0.11, 3), "blocks": b})
    annual = []
    prev_rev = hist_annual[-1]["revenue"]
    for y in range(2026, 2037):
        hs = [h for h in path_halves if h["period"].startswith(str(y))]
        first = [h for h in hist_halves if h["period"] == f"{y}H1"]
        rows = first + hs
        b = {k: {"revenue": r(sum(h["blocks"][k]["revenue"] for h in rows), 3),
                 "ebitda": r(sum(h["blocks"][k]["ebitda"] for h in rows), 3)} for k in BLOCKS}
        rev, ebitda, margin = group(b)
        capex = sum(h["capex"] for h in hs)
        fcff = sum(h["fcff"] for h in hs)
        da = rev * 0.035
        sbc = rev * 0.023
        annual.append({
            "year": y, "revenue": rev, "ebitda": ebitda, "attack": -10.0 if y == 2026 else 0.0, "sbc": r(sbc, 3),
            "da": r(da, 3), "ebit": r(ebitda - da - sbc, 3), "tax": r((ebitda - da) * 0.18, 3), "capex": r(capex, 3),
            "lease_new": r(rev * 0.0016, 3), "acq": r(rev * 0.005, 3), "dwc": r(-rev * 0.01, 3),
            "dkb": r(rev * 0.006, 3), "fcff": r(fcff, 3), "nopat": r((ebitda - da) * 0.82, 3), "interest": 26.0,
            "shield": r(26.0 * 0.22, 3), "nwc": r(-rev * 0.06, 3), "fa": r(rev * 0.28, 3), "margin": margin,
            "capex_pct": r((capex + rev * 0.0016) / rev, 6), "revenue_growth": None if y == 2026 else r(rev / prev_rev - 1, 6),
            "blocks": b, "fact": ["2026H1: revenue", "2026H1: ebitda"] if y == 2026 else [],
            "forecast_periods": [h["period"] for h in hs]})
        prev_rev = rev
    paths = {"layer": "analytical", "block_order": BLOCKS, "block_titles": TITLES,
             "note": "ожидаемый путь слоя «свой взгляд» по вероятностям клеток; год якоря — факт 1П и прогноз 2П",
             "annual": annual, "halves": [{k: v for k, v in h.items()} for h in path_halves]}
    history = {"halves": hist_halves, "annual": hist_annual, "block_order": BLOCKS}

    # ── долг и мост: банк вне долга ──
    anchor = {"as_of": "2026-06-30", "net_debt_nonbank": 210.0, "net_debt_company": 7.0, "bank_money": 203.0}
    an = layers["analytical"]
    ev_rows = [{"key": "pv_explicit", "label": "Свободный поток прогноза (PV)", "amount": an["pv_explicit"]},
               {"key": "pv_shield", "label": "Щит прогноза (PV)", "amount": an["pv_shield"]},
               {"key": "pv_tv", "label": "Терминальная стоимость (PV)", "amount": an["pv_tv"]},
               {"key": "pv_tv_shield", "label": "Щит терминала (PV)", "amount": an["pv_tv_shield"]}]
    rows_val = [{"key": "net_debt_nonbank", "label": "Чистый долг без банка (якорь)", "amount": 210.0},
                {"key": "fin_investments", "label": "финансовые вложения", "amount": -10.0},
                {"key": "loans_issued", "label": "выданные займы", "amount": -19.0},
                {"key": "dividends_declared", "label": "Объявленные дивиденды к выплате", "amount": 42.0},
                {"key": "roll", "label": "Перекат на дату оценки", "amount": -43.0}]
    debt = {"anchor": anchor,
            "bridge": {"ev_rows": ev_rows, "v0": an["v0"], "rows_at_valuation": rows_val, "total": CLAIMS,
                       "equity": r(an["v0"] - CLAIMS, 2), "equity_rows": [], "treasury_mln": 0, "shares_mln": SHARES},
            "bonds": [], "bank_loans": None, "wall": [], "wall_note": None}
    dividends = {"policy": None,
                 "register": [{"label": "2026H1", "amount": 42.0, "record_date": "2026-09-21", "paid": "2026Q3", "in_claims": True}],
                 "history": [], "model": [], "next_expected": None, "yield_ltm": None,
                 "note": "путь дивидендов модель не считает: поток — до финансирования"}

    # ── суждения, обратный DCF, полоса ──
    axes = [("sbc", "Выплаты акциями (суждение 8)", "доля выручки", 0.023, 0.015, 0.035, -0.68),
            ("at_expense", "Расход автономных технологий (суждение 6)", "положение пути", 0.0, -1.0, 1.0, 0.52),
            ("it_tax", "Налог IT-корзины после 2030 г. (суждение 9)", "ставка", 0.15, 0.05, 0.25, -0.35),
            ("terminal_growth", "Реальный рост терминала (суждение 10)", "доля", 0.015, 0.005, 0.025, 0.09),
            ("ma", "Покупки компаний", "доля выручки", 0.005, 0.0, 0.01, -0.16),
            ("governance", "Дисконт за управление", "доля", 0.03, 0.01, 0.06, -0.08),
            ("attack", "Ущерб атак 4К26", "млрд ₽ EBITDA", -10.0, -25.0, 0.0, 0.13),
            ("net_exercise", "Доля нетто-исполнения опционов", "доля", 0.7, 0.64, 0.74, -0.02)]
    sq = sum(a[6] ** 2 for a in axes)
    contributions = [{"axis": a[1], "key": a[0], "share": r(a[6] ** 2 / sq, 6), "rank_corr": a[6]} for a in axes]
    judgements = []
    for key, name, unit, book_v, lo_v, hi_v, rho in axes:
        swing = abs(rho) * 1200
        p_lo, p_hi = (central + swing / 2, central - swing / 2) if rho < 0 else (central - swing / 2, central + swing / 2)
        judgements.append({"id": key, "name": name, "unit": unit, "book": book_v, "low": lo_v, "high": hi_v,
                           "price_low": r(p_lo, 2), "price_high": r(p_hi, 2), "swing": r(swing, 2),
                           "share": r(rho ** 2 / sq, 6), "kind": "path" if key == "at_expense" else "value",
                           "paths": [key], "in_core": True})
    judgements.sort(key=lambda j: -j["swing"])
    reverse_rows = []
    for i, (key, name, unit, book_v, lo_v, hi_v, rho) in enumerate(axes):
        # Первая ось решается внутри диапазона, вторая — вне его, третья — недостижима, остальные — не считались.
        status = ["solved", "solved", "unreachable"][i] if i < 3 else "skipped"
        solved = None
        if status == "solved":
            solved = r(book_v + (0.3 if i == 0 else 1.4) * (hi_v - book_v) * (-1 if rho < 0 else 1) * (1 if i == 1 else -0.5), 6)
        in_range = None if solved is None else (min(lo_v, hi_v) <= solved <= max(lo_v, hi_v))
        reverse_rows.append({"key": key, "name": name, "unit": unit, "kind": "value", "paths": [key], "book": book_v,
                             "solved": solved, "delta": None if solved is None else r(solved - book_v, 6),
                             "in_range": in_range, "range": [lo_v, hi_v], "search": [lo_v - (hi_v - lo_v), hi_v + (hi_v - lo_v)],
                             "status": status, "search_value": solved, "gap_full": None if solved is None else 2.1,
                             "point_solved": solved, "point_status": status, "provisional": False, "fixed_axes": [key],
                             "evaluations": 0 if status == "skipped" else 14, "evaluations_full": 0 if status == "skipped" else 3})
    uncertainty = {"contributions": contributions, "draws": DRAWS, "seed": 20261120, "axes_count": len(axes),
                   "axes": [{"key": a[0], "name": a[1], "kind": "value", "paths": [a[0]], "unit": a[2], "book": a[3],
                             "low": a[4], "high": a[5], "in_core": True} for a in axes],
                   "skipped_axes": [], "axis_prices": None, "mean": s["mean"],
                   "histogram_bins": [i * 250.0 for i in range(0, 25)], "subsample": 40, "delta": -12.0}

    # ── календарь и ближайший отчёт (иллюстрация) ──
    events = [
        {"date": "2026-12-19", "title": "Заседание Совета директоров Банка России по ключевой ставке", "kind": "cbr", "confirmed": True, "note": None},
        {"date": "2027-02-18", "title": "Финансовые результаты YDEX за 4 кв. и 2026 г. (МСФО)", "kind": "ifrs", "confirmed": False,
         "note": "дата — по прошлому году", "covers": "2026H2"},
        {"date": "2027-03-20", "title": "Заседание Совета директоров Банка России по ключевой ставке", "kind": "cbr", "confirmed": False, "note": None},
        {"date": "2027-04-24", "title": "Финансовые результаты YDEX за 1 кв. 2027 г. (МСФО)", "kind": "ifrs", "confirmed": False,
         "note": None, "covers": None},
    ]
    exp = path_halves[0]
    prev = next(h for h in hist_halves if h["period"] == "2025H2")
    next_report = {"period": "2026H2", "events": events,
                   "closing": {"date": "2027-02-18", "title": events[1]["title"], "confirmed": False, "published": False},
                   "expectation": {"period": "2026H2", "revenue": exp["revenue"], "ebitda": exp["ebitda"], "margin": exp["margin"],
                                   "revenue_growth": r(exp["revenue"] / prev["revenue"] - 1, 6), "blocks": exp["blocks"]},
                   "guidance": None, "benchmarks": [], "table": [],
                   "note": "что даст отчёт, модель в первой версии не считает"}
    journal = {"entries": [], "rule": "журнал прогнозов в первой версии не пополняется: записи прошлых выпусков переносятся как есть",
               "status": "не ведётся (первая версия)", "releases": {}}

    # ── проверки ──
    gate_names = [("roic_increment", "Отдача прироста капитала против отдачи окна", True),
                  ("terminal_share", "Доля PV терминала в EV клетки", True),
                  ("margin_corridor", "Маржа EBITDA группы клетки в коридоре", False),
                  ("capex_corridor", "Вложения к выручке в коридоре", False)]
    gates = [{"name": n, "title": t, "fired": f, "mass": 0.3 if f else 0.0, "value": 0.25, "low": 0.1, "high": 0.4,
              "corridor": [0.12, 0.35], "unit": "share", "cells": 8 if f else 0, "detail": "",
              "explanation": "вложения опережают выручку в цикле ИИ 2026–2030 гг." if f else None,
              "valid_until": "2027-04-30" if f else None, "expected_mass": [0.2, 0.4] if f else None,
              "status": "explained" if f else "ok", "blocking": False} for n, t, f in gate_names]
    checks = {
        "invariants": [{"name": n, "title": t, "ok": True, "detail": ""} for n, t in [
            ("probabilities", "вероятности клеток в сумме 1"), ("group_identity", "группа = сумма блоков"),
            ("fcff_identity", "поток сходится"), ("ev_identity", "EV клетки = сумма PV")]],
        "gates": gates,
        "traps": [{"name": "bank_not_in_debt", "title": "банк вне долга", "ok": True, "detail": ""},
                  {"name": "sbc_once", "title": "выплаты акциями учтены один раз", "ok": True, "detail": ""}],
        "magnitudes": [{"name": "ev_ebitda_2027", "title": "EV / EBITDA 2027 г. точки", "value": 3.8, "reference": [3.0, 7.0], "detail": ""}],
        "forks": [
            {"name": "it_tax", "title": "Налог IT после 2030 г.", "basis": f"медиана полосы ({DRAWS} прогонов)",
             "variants": [{"label": "15 % (книга)", "value": 0.15, "price": 0.0}, {"label": "5 %", "value": 0.05, "price": 200.0},
                          {"label": "25 %", "value": 0.25, "price": -195.0}], "status": "ok", "detail": ""},
            {"name": "stress", "title": "Стресс платформы", "basis": f"медиана полосы ({DRAWS} прогонов)",
             "variants": [{"label": "равные режимы (книга)", "value": 0.25, "price": 0.0}, {"label": "стресс 40 %", "value": 0.4, "price": -420.0}],
             "status": "ok", "detail": ""},
            {"name": "bank_money", "title": "Деньги банка", "basis": "мост (точка)",
             "variants": [{"label": "МСФО: 203,0 млрд (книга)", "value": 203.0, "price": 0.0}, {"label": "ф.101: 198,7 млрд", "value": 198.7, "price": 10.0}],
             "status": "ok", "detail": ""},
        ],
        "flags": [{"name": n, "title": t, "raised": False, "status": "ok", "detail": ""} for n, t in [
            ("book_update", "Книгу пора обновить"), ("dividend_register", "Отсечка прошла, дивиденда в реестре нет"),
            ("report_fact", "Отчёт вышел, факт не внесён"), ("price_fallback", "Цена — запасная"), ("stale_release", "Выпуск устарел")]],
        "note": "тождества и ловушки роняют сборку; сработавшая проверка требует объяснения со сроком",
    }

    # ── рынок, живые входы, плитки ──
    hist_px = []
    day = 0
    px = 4100.0
    import datetime as dt
    start = dt.date(2025, 11, 20)
    while day <= 365:
        d0 = start + dt.timedelta(days=day)
        if d0.weekday() < 5:
            px *= math.exp(rng.gauss(-0.0009, 0.016))
            hist_px.append({"date": d0.isoformat(), "close": r(px, 1)})
        day += 3
    hist_px[-1]["close"] = PRICE
    closes = [p["close"] for p in hist_px]
    lo_i, hi_i = closes.index(min(closes)), closes.index(max(closes))
    market = {"price": PRICE, "price_date": VALUATION, "price_time": "18:39", "price_source": "Мосбиржа, режим основных торгов",
              "price_status": "live", "book_price": 3277.5, "shares_outstanding_mln": SHARES_OUT,
              "market_cap": r(PRICE * SHARES_OUT / 1000, 3), "claims": CLAIMS, "market_ev": r(v_star, 3),
              "equity_share_of_ev": r((v_star - CLAIMS) / v_star, 6), "ebitda_2027": ebitda_2027,
              "ev_ebitda_2027": r(v_star / ebitda_2027, 4), "peers": None, "brokers": None, "price_history": hist_px,
              "price_min": {"date": hist_px[lo_i]["date"], "close": closes[lo_i]},
              "price_max": {"date": hist_px[hi_i]["date"], "close": closes[hi_i]}}
    nodes = {"1": 0.139, "3": 0.141, "5": 0.142, "10": 0.143}
    live = {"price": {"value": PRICE, "date": VALUATION, "time": "18:39", "source": "Мосбиржа", "status": "live",
                      "accepted": True, "reason": None, "kind": "close", "last_accepted": VALUATION},
            "curve": {"as_of": VALUATION, "nodes": nodes, "book_nodes": {"1": 0.140, "3": 0.149, "5": 0.153, "10": 0.155},
                      "shift_bp": {"5": -110, "10": -120}},
            "key_rate": {"value": 0.155, "date": VALUATION, "since": "2026-10-24"},
            "valuation_date": VALUATION, "fetched_at": "2026-11-20T16:00:00Z", "errors": {}, "degraded": False}
    inputs = {"rows": [
        {"name": "Цена акции", "value": PRICE, "unit": "rub", "as_of": VALUATION, "source": "Мосбиржа", "status": "ok"},
        {"name": "Кривая ОФЗ", "value": 0.142, "unit": "pct", "as_of": VALUATION, "source": "Мосбиржа, бескупонная кривая ОФЗ", "status": "ok"},
        {"name": "Ключевая ставка", "value": 0.155, "unit": "pct", "as_of": "2026-10-24", "source": "Банк России", "status": "ok"},
        {"name": "Книга допущений", "value": "1.0", "unit": "version", "as_of": "2026-11-20", "source": "репозиторий модели", "status": "ok"},
        {"name": "Факты отчётности", "value": "1П 2026", "unit": None, "as_of": "2026-06-30", "source": "МСФО и датабук Яндекса", "status": "ok"}]}
    spark = [{"date": p["date"], "value": p["close"]} for p in hist_px[-40:]]
    indicators = {"tiles": [
        {"id": "price", "title": "Цена YDEX", "unit": "rub", "value": PRICE, "date": VALUATION, "change": r(PRICE - closes[-21], 1),
         "change_from": hist_px[-21]["date"], "since": None, "min": min(closes), "max": max(closes), "history": spark},
        {"id": "key_rate", "title": "Ключевая ставка", "unit": "pct", "value": 0.155, "date": VALUATION, "change": -0.005,
         "change_from": "2026-09-12", "since": "2026-10-24", "min": 0.155, "max": 0.21, "history": []}]}
    changes = {"vs_previous": {"previous_sha": "f" * 64, "previous_generated_at": "2026-11-19T15:10:00Z",
                               "rows": [{"component": "residual", "title": "книга, факты и код", "rub": 12.4},
                                        {"component": "market_price", "title": "цена рынка", "rub": 0.0},
                                        {"component": "valuation_date", "title": "дата оценки (перекат)", "rub": 3.1}],
                               "total_rub": 15.5, "reference": None, "note": ""}}
    book = {"version": "1.0", "date": "2026-11-20", "tag": "book-1.0",
            "sections": [{"id": k, "title": t} for k, t in [("growth", "Рост блоков"), ("margin", "Цели маржи"),
                                                             ("capex", "Капвложения"), ("valuation", "Оценка")]],
            "worlds_source": "миры семейства 1.7, кривая 2026-10-28", "facts_date": "2026-06-30",
            "key_judgements": [
                {"id": "beta_u", "name": "β активов", "value": 0.968, "unit": "share", "path": "valuation.beta_u"},
                {"id": "erp", "name": "Премия за риск", "value": 0.0557, "unit": "share", "path": "valuation.erp"},
                {"id": "asset_life", "name": "Срок амортизации", "value": 6.5, "unit": "years", "path": "capex.asset_life_years"},
                {"id": "governance", "name": "Дисконт за управление", "value": GOV, "unit": "share", "path": "valuation.governance_discount"}]}

    payload = {
        "schema": "yandex-v1",
        "meta": {"generated_at": "2026-11-20T16:05:00Z", "valuation_date": VALUATION, "facts_date": "2026-06-30",
                 "book_version": "1.0", "book_date": "2026-11-20", "engine_commit": "0" * 40,
                 "basis": "скорр. EBITDA компании (после аренды офисов)", "shares_mln": SHARES,
                 "shares_outstanding_mln": SHARES_OUT, "governance_discount": GOV, "anchor_period": "2026H1",
                 "first_period": "2026H2", "last_period": "2036H2", "open_period": "2026H2", "curve_as_of": "2026-10-28",
                 "closed_periods": 0, "elapsed": 0.77, "worlds_version": "1.7", "fast": False, "draws": DRAWS,
                 "payload_sha256": None, "bytes": None, "previous_sha256": "f" * 64},
        "market": market, "headline": headline, "fair_value": fair_value, "layers": layers,
        "grid": {"cells": cells, "world_order": W, "regime_order": REG, "capex_order": CAP},
        "worlds": worlds, "regimes": regimes, "capex_levels": capex_levels, "paths": paths, "debt": debt,
        "dividends": dividends, "history": history,
        "reverse_dcf": {"rows": reverse_rows, "target": PRICE, "provisional": False,
                        "method": "поиск на подвыборке прогонов со сдвигом к полной полосе, уточнение на полной полосе"},
        "judgements": {"rows": judgements}, "uncertainty": uncertainty, "next_report": next_report, "journal": journal,
        "calendar": {"events": events}, "checks": checks, "inputs": inputs, "live": live, "changes": changes,
        "book": book, "indicators": indicators,
    }
    assert all(k in payload for k in REQUIRED_TOP_LEVEL)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload["meta"]["payload_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    payload["meta"]["bytes"] = len(body.encode("utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    out.write_text(compact + "\n", encoding="utf-8", newline="\n")
    print(f"мок выпуска: {out} · {payload['meta']['bytes']} байт · медиана {median} ₽ · точка {r(central, 1)} ₽")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
