"""Выпуск YDEX: `build_payload(...)` → словарь по docs/PAYLOAD.md (контракт `yandex-v1`),
`validate(payload)` → список нарушений (пусто — выпуск годен).

Сборка: входы (книга, факты, живые входы конвейера) → сетка 36 клеток на дату оценки (`model.grid`) → полоса §9,
суждения, цены осей и обратный DCF §11 на её гиперкубе (`model.uncertainty`) → тождества сетки → блоки выпуска →
«что изменилось» → хэш содержания → проверка контракта. Блоки — сервисы: блоки устава A–G и исключения вместо
площадей и форматов образца; поля образца, которые читают хаб и сводка состояния (`schema`, `meta.*`, `headline.*`,
`market.price*`, `book.version`, `checks.flags[].raised`, `checks.gates[].fired`, `next_report.closing`,
`calendar.events`), — на прежних местах.

**Живые входы** (`live`, формат — `indicators.live`) дают только цену акции и дату оценки (§13.6): дата оценки =
дата принятой цены, не раньше даты книги. Без `live` — входы книги (`meta.valuation_date`, `meta.market_price`).

**Хэш** (`meta.payload_sha256`) — sha256 канонического JSON содержания: без момента сборки (`meta.generated_at`,
`live.fetched_at`), собственного хэша и размера, ссылок на выпуски (`meta.previous_sha256`, блок `changes`,
`release_sha` записей журнала). Одни входы — один хэш, сколько ни пересобирай.

**Печать.** Статистики полосы считаются по прогонам в том виде, в каком они лежат в выпуске (0,1 ₽), — ползунок λ
витрины воспроизводит заголовок бит в бит; печать = округлению этих чисел к шагу книги, половина вверх.

**Проверки §13** — `model/checks.py`: `checks.invariants` (§13.1), `checks.gates` (§13.2, объяснения —
`data/assumptions/gate_explanations.yaml`), `checks.traps` (§13.3), `checks.magnitudes` (§13.4), `checks.forks`
(§13.5, цена — на тех же случайных числах, что полоса выпуска), `checks.flags` (§13.6); здесь же защита заголовка.
Нарушенный инвариант или ловушка, сработавший гейт без объяснения — сборка не выходит (`ReleaseBlocked`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from model import attribution, checks as checks_mod, journal as journal_mod
from model.book import BOOK_DIR, ROOT, load_book, open_period, prev_same_half, today as _today
from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
from model.checks import round_to_step
from model.facts import core_facts, load_facts
from model.grid import (BLOCK_TITLES, BLOCKS, LAYER_TITLES, LAYERS, annual_path, evaluate, expected_path,
                        invariants as grid_invariants)
from model.uncertainty import DRAW_DECIMALS, band_stats, distribution

fsum = math.fsum

SCHEMA = "yandex-v1"
REQUIRED_TOP_LEVEL = (
    "schema", "meta", "market", "headline", "fair_value", "layers", "grid", "worlds", "regimes", "capex_levels",
    "paths", "debt", "dividends", "history", "reverse_dcf", "judgements", "uncertainty", "next_report", "journal",
    "calendar", "checks", "inputs", "live", "changes", "book", "indicators")
# Необязательный блок: шапка семейства `family-header-v1` — её ставит сборка (ops/build_release.py, common.header) после
# сборки ядра, до проверки контракта: контракт ядра и схема шапки проверяются на выпуске с шапкой; `seal` пересчитывает
# хэш и размер по выпуску с шапкой.
OPTIONAL_TOP_LEVEL = ("header",)
# Каталоги и файлы кода, который считает число выпуска или его шапку (общий слой `common`, календарь): их правка с
# опубликованного выпуска будит пересборку.
# Ровно `CODE_DIRS` в ops/run.sh, в том же порядке (тест `test_run_sh_code_dirs_are_the_release_code_dirs`).
CODE_DIRS = ("model", "indicators", "ops", "common", "data/assumptions", "data/facts", "data/calendar.json",
             "requirements.txt")
PAYLOAD_MAX_BYTES = 500_000
LAMBDA_ROWS = 21                # положений ползунка λ: 0; 0,05; …; 1
LAMBDA_STEP = 1 / (LAMBDA_ROWS - 1)
PRICE_DECIMALS = 2              # точные цены заголовка и точки — до копейки
HALF_CENT = 0.0051              # допуск сверки заголовка с прогонами (округление до копейки)
DERIVED_REL = 1e-7              # допуск тождеств выпуска от масштаба слагаемых (9 значащих цифр)
NUMBER_FORMAT = ".9g"           # прочие числа выпуска — 9 значащих цифр
MAX_HEADLINE_JUMP = 0.25        # защита заголовка (§13.6)
MAX_V0_JUMP = 0.10
NOTE_TOLERANCE_PCT = 5
TILE_POINTS = 60
PRICE_HISTORY_POINTS = 130
STALE_DAYS = 7
HISTOGRAM_MAX_BINS = 40
GIT_TIMEOUT_S = 60
BP = 10_000

RELEASE_NOTES = BOOK_DIR / "release_notes.yaml"
CALENDAR = ROOT / "data" / "calendar.json"
NOT_CONTENT_META = ("generated_at", "payload_sha256", "bytes", "previous_sha256")
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

REGIME_TITLES = {"stress": "Стресс", "slow": "Медленный", "base": "Базовый", "strong": "Сильный"}  # устав, раздел 2
CAPEX_TITLES = {"low": "Низкий", "base": "Базовый", "high": "Высокий"}
SECTIONS = (("worlds", "Миры ставок"), ("joint", "Сетка и вероятности"), ("path_2026h2", "Путь 2П 2026"),
            ("attack", "Ущерб атак"), ("growth", "Рост блоков"), ("margin", "Цели маржи блоков"),
            ("f", "Блок F"), ("excl", "Исключения"), ("bank", "Капитал банка"), ("sbc", "Выплаты акциями"),
            ("capex", "Капвложения и аренда"), ("ma", "Покупки компаний"), ("nwc", "Оборотный капитал"),
            ("tax", "Налог"), ("terminal", "Терминал"), ("financing", "Долг и щит"),
            ("valuation", "Ставка, делитель, печать"), ("bridge", "Мост"), ("checks", "Коридоры проверок"),
            ("forks", "Развилки"))
# Ключевые суждения книги для экрана «Книга» (путь — ключ книги; нет пути — строка пропускается).
KEY_JUDGEMENTS = (
    ("beta_u", "valuation.beta_u", "β активов", "share"), ("erp", "valuation.erp", "Премия за риск", "share"),
    ("sbc", "sbc.share", "Выплаты акциями", "share"), ("it_tax_lt", "tax.it_rate.LT", "IT-налог после 2030 г.",
                                                       "share"),
    ("terminal_growth", "terminal.real_growth", "Реальный рост терминала", "share"),
    ("roic", "terminal.roic", "Отдача капитала окна", "share"),
    ("asset_life", "capex.asset_life_years", "Срок амортизации", "years"),
    ("governance", "valuation.governance_discount", "Дисконт за управление", "share"),
    ("net_exercise", "valuation.net_exercise_k", "Нетто-исполнение опционов", "share"),
    ("lambda", "joint.lambda", "Вес своего взгляда на ставки λ", "share"))
EV_ROW_TITLES = {"pv_explicit": "Свободный поток прогноза (PV)", "pv_shield": "Щит прогноза (PV)",
                 "pv_tv": "Терминальная стоимость (PV)", "pv_tv_shield": "Щит терминала (PV)"}
CLAIM_TITLES = {"net_debt_nonbank": "Чистый долг без банка (якорь)", "rollover": "Перекат на дату оценки",
                "dividends": "Объявленные дивиденды к выплате"}
SOURCE_WORDS = {"ISS TQBR": "Мосбиржа, режим TQBR", "ISS TQBR history": "Мосбиржа, закрытия TQBR",
                "ISS zcyc": "Мосбиржа, бескупонная кривая ОФЗ"}
# Гейты §13.2 и флаги §13.6 — model/checks.py; блокирующие статусы гейта.
BLOCKING_GATE_STATUSES = ("unexplained", "expired", "mass_outside")


class ReleaseBlocked(Exception):
    """Инвариант или контракт: выпуск не выходит."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# ------------------------------------------------------------ мелочи


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _r(x, nd: int = PRICE_DECIMALS):
    return round(x, nd) if _num(x) and math.isfinite(x) else x


def _tidy(obj: Any) -> Any:
    """Числа выпуска — 9 значащих цифр (хвосты двоичной арифметики не меняют хэш); рекурсивно."""
    if isinstance(obj, float):
        return float(format(obj, NUMBER_FORMAT)) if math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _tidy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_tidy(v) for v in obj]
    return obj


def _div(a, b):
    return a / b if _num(a) and _num(b) and b else None


def _day(x) -> dt.date | None:
    if x is None:
        return None
    if isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _words(source) -> str | None:
    if not source:
        return source
    return SOURCE_WORDS.get(str(source), str(source))


def _thin(rows: list, limit: int) -> list:
    """Не больше `limit` точек, последняя — всегда."""
    if len(rows) <= limit:
        return list(rows)
    step = -(-len(rows) // limit)
    return rows[::-1][::step][::-1][-limit:]


def price_from_equity(equity: float, g: float, shares: float) -> float:
    """Цена из капитала (§7.2): (V0 − D) × (1 − g) × 1000 / N_eff; при капитале ≤ 0 — без дисконта."""
    return equity * (1.0 - g) * 1000.0 / shares if equity > 0 else equity * 1000.0 / shares


def v0_from_price(price: float, d: float, g: float, shares: float) -> float:
    """V0, при котором цена = `price` (§7.3)."""
    return d + (price * shares / 1000.0 / (1.0 - g) if price > 0 else price * shares / 1000.0)


def compact_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def content_digest(payload: dict) -> str:
    """sha256 содержания выпуска (без времени сборки, своего хэша, размера и ссылок на выпуски: `release_sha`
    записей журнала и `journal.releases`; время сборки шапки `header.release.generated_at` — тоже)."""
    body = {k: v for k, v in payload.items() if k not in ("meta", "changes")}
    body["meta"] = {k: v for k, v in (payload.get("meta") or {}).items() if k not in NOT_CONTENT_META}
    if isinstance(body.get("header"), dict) and isinstance(body["header"].get("release"), dict):
        body["header"] = {**body["header"], "release": {k: v for k, v in body["header"]["release"].items()
                                                        if k != "generated_at"}}
    if isinstance(body.get("live"), dict):
        body["live"] = {k: v for k, v in body["live"].items() if k != "fetched_at"}
    if isinstance(body.get("journal"), dict):
        J = {k: v for k, v in body["journal"].items() if k != "releases"}
        body["journal"] = {**J, "entries": [{k: v for k, v in e.items() if k != "release_sha"}
                                            for e in J.get("entries") or []]}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def engine_commit() -> str:
    """Коммит кода сборки: `GITHUB_SHA` в Actions, иначе `git rev-parse HEAD`."""
    env = os.environ.get("GITHUB_SHA")
    if env:
        return env
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def generated_at() -> str:
    """Момент сборки UTC; при `FAKE_TODAY` — полдень этого дня."""
    if os.environ.get("FAKE_TODAY"):
        return f"{_today().isoformat()}T12:00:00Z"
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ входы


def live_inputs(A: dict, live: dict | None) -> dict:
    """Дата оценки, цена рынка и её происхождение (§13.6)."""
    M = A["meta"]
    if not live or not isinstance(live.get("price"), dict) or not _num(live["price"].get("value")):
        return {"valuation_date": dt.date.fromisoformat(M["valuation_date"]),
                "market_price": float(M["market_price"]), "price_date": M["valuation_date"],
                "price_time": None, "price_source": "книга (meta.market_price)",
                "status": "book", "accepted": None, "reason": None}
    price = live["price"]
    day = _day(price.get("date"))
    if day is None:
        raise ReleaseBlocked([f"живые входы: у цены {price.get('value')} нет даты"])
    status = price.get("status") or ("live" if price.get("accepted") else "fallback")
    return {"valuation_date": max(day, dt.date.fromisoformat(M["date"])),
            "market_price": float(price["value"]), "price_date": day.isoformat(),
            "price_time": price.get("time"), "price_source": price.get("source") or "ISS TQBR",
            "status": status, "accepted": price.get("accepted"), "reason": price.get("reason")}


# ------------------------------------------------------------ блоки


def market_block(A, cf, grid, inputs, live) -> dict:
    an = grid.layers["analytical"]
    mp = inputs["market_price"]
    v_star = grid.point.v_star
    hist = [{"date": str(r["date"]), "close": float(r["close"])}
            for r in (live or {}).get("price_history") or [] if _num(r.get("close"))]
    return {"price": mp, "price_date": inputs["price_date"], "price_time": inputs["price_time"],
            "price_source": _words(inputs["price_source"]), "price_status": inputs["status"],
            "book_price": float(A["meta"]["market_price"]), "shares_outstanding_mln": cf.shares_mln,
            "market_cap": mp * cf.shares_mln / 1000.0, "claims": an.d, "market_ev": v_star,
            "equity_share_of_ev": (v_star - an.d) / v_star if v_star else None,
            "ebitda_2027": an.ebitda_2027, "ev_ebitda_2027": _div(v_star, an.ebitda_2027),
            "peers": None, "brokers": None,
            "price_history": _thin(hist, PRICE_HISTORY_POINTS),
            "price_min": min(hist, key=lambda h: h["close"]) if hist else None,
            "price_max": max(hist, key=lambda h: h["close"]) if hist else None}


def headline_block(A, stats: dict, mp: float, draws: int, lam: float) -> dict:
    step = float(A["valuation"]["headline"]["print_step"])
    band, inner = [stats["p10"], stats["p90"]], [stats["p25"], stats["p75"]]
    return {"central": _r(stats["median"]), "printed_central": round_to_step(stats["median"], step),
            "band": [_r(x) for x in band], "printed_band": [round_to_step(x, step) for x in band],
            "inner": [_r(x) for x in inner], "printed_inner": [round_to_step(x, step) for x in inner],
            "mean": _r(stats["mean"]), "p_central_below_market": stats["p_below"], "market_price": mp,
            "print_step": step, "draws": draws, "lambda": lam}


def fair_value_block(A, grid, stats, draws_low, draws_high, mp) -> dict:
    step = float(A["valuation"]["headline"]["print_step"])
    P, ctx, an = grid.point, grid.ctx, grid.layers["analytical"]
    rows = []
    for i in range(LAMBDA_ROWS):
        x = i * LAMBDA_STEP
        s = band_stats(draws_low, draws_high, x, mp)
        rows.append({"lambda": x, "point": _r(P.low + x * (P.high - P.low)), "median": _r(s["median"]),
                     "p10": _r(s["p10"]), "p25": _r(s["p25"]), "p75": _r(s["p75"]), "p90": _r(s["p90"]),
                     "mean": _r(s["mean"]), "p_below": s["p_below"]})
    v0_med = ctx.v0_of(stats["median"], an.d)
    # База пары «модель — рынок» — та же EBITDA 2027 г., что у знаменателя EV/EBITDA точки и гейта брокеров: книга 1.4
    # (`joint.expectation: headline`, MODEL.md §8) — веса заголовка, без ключа — слой «свой взгляд» (книга 1.3).
    e27 = checks_mod.headline_ebitda_2027(grid) if A["joint"].get("expectation") == "headline" else an.ebitda_2027
    return {"low": _r(P.low), "central": _r(P.central), "high": _r(P.high),
            "printed": {k: round_to_step(v, step) for k, v in (("low", P.low), ("central", P.central),
                                                                 ("high", P.high))},
            "lambda": P.lam, "lambda_step": LAMBDA_STEP, "rates_view": {"rub": P.rates_view},
            "by_lambda": rows, "draws_low": draws_low, "draws_high": draws_high,
            "by_world": {w: grid.worlds_only[w].price for w in WORLDS},
            "center_ev": {"v0_median": v0_med, "v0_point": P.v0_point, "v_star": P.v_star,
                          "gap_median": v0_med / P.v_star - 1.0, "gap_point": P.gap_point,
                          "rub_per_1pct_ev_median": ctx.rub_per_1pct(v0_med),
                          "rub_per_1pct_ev_point": P.rub_per_1pct_ev_point, "ebitda_2027": e27,
                          "ev_ebitda_2027_median": _div(v0_med, e27),
                          "ev_ebitda_2027_point": P.ev_ebitda_2027_point,
                          "ev_ebitda_2027_market": _div(P.v_star, e27)},
            "equity_share_of_ev": P.equity_share_of_ev}


def _layer(L) -> dict:
    return {"title": L.title, "world_weights": L.world_weights, "v0": L.v0, "d": L.d, "equity": L.equity,
            "price": L.price, "pv_explicit": L.pv_explicit, "pv_shield": L.pv_shield, "pv_tv": L.pv_tv,
            "pv_tv_shield": L.pv_tv_shield, "terminal_share": L.terminal_share, "ebitda_2027": L.ebitda_2027,
            "ev_ebitda_2027": L.ev_ebitda_2027, "v0_to_d": L.v0_to_d}


def grid_block(grid) -> dict:
    cells = []
    y30 = grid.year_2027 + 3
    for c in grid.cells:
        x = c.result
        rev30 = x.group["revenue"]
        idx = [i for i, p in enumerate(x.periods) if p.startswith(str(y30))]
        margin30 = _div(fsum(x.group["ebitda"][i] for i in idx), fsum(rev30[i] for i in idx)) if idx else None
        cells.append({"world": x.world, "regime": x.regime, "capex": x.capex,
                      "p_analytical": c.p["analytical"], "p_market_implied": c.p["market_implied"],
                      "p_neutral": c.p["macro_neutral"], "ev": x.ev, "d": x.claims["total"],
                      "equity": x.ev - x.claims["total"], "price": x.price, "c_T": x.c_T,
                      "terminal_share": (x.pv_tv + x.pv_tv_shield) / x.ev if x.ev else None,
                      "ebitda_2027": x.ebitda_year(grid.year_2027),
                      "ev_ebitda_2027": _div(x.ev, x.ebitda_year(grid.year_2027)), "margin_2030": margin30})
    return {"cells": cells, "world_order": list(WORLDS), "regime_order": list(REGIMES),
            "capex_order": list(CAPEX_LEVELS)}


def worlds_block(A, grid) -> dict:
    out = {"order": list(WORLDS), "source": dict(A["worlds"]["source"])}
    from model.grid import layer_world_weights
    weights = layer_world_weights(A)
    for w in WORLDS:
        W = A["worlds"][w]
        base = grid.cell(w, "base", "base").result
        out[w] = {"name": W.get("name"), "weights": {L: weights[L][w] for L in LAYERS},
                  "key_rate": [{"period": p, "value": float(v)} for p, v in W["key_rate"].items() if _num(v)],
                  "cpi": [{"period": p, "value": float(v)} for p, v in W["cpi"].items() if _num(v)],
                  "zero_curve": {k: float(v) for k, v in W["zero_curve"].items()},
                  "lt_inflation": float(W["lt"]["inflation"]), "r_terminal": base.r, "g_terminal": base.g,
                  "price": grid.worlds_only[w].price, "v0": grid.worlds_only[w].v0}
    return out


def regimes_block(A) -> dict:
    J, G, M = A["joint"], A["growth"], A["margin"]
    blocks = {}
    for b in BLOCKS:
        if b not in M["targets"]:
            continue
        blocks[b] = {"title": BLOCK_TITLES[b], "margin_center": M["center"].get(b),
                     "margin_range": M["range"].get(b), "margin_targets": dict(M["targets"][b]),
                     "target_year": M["target_year"].get(b),
                     "growth_shift": dict(G["shift"][b]) if b in G["shift"] else None,
                     "shift_range": G["shift_range"].get(b) if isinstance(G.get("shift_range"), dict) else None,
                     "converge_year": G["converge_year"].get(b)}
    return {"order": list(REGIMES), "titles": dict(REGIME_TITLES),
            "prob": {r: float(J["regime_prob"][r]) for r in REGIMES}, "blocks": blocks,
            "shift_to": G.get("shift_to"),
            "note": "режимы — середины квартилей двукусочного нормального распределения суждений 1–5 (§3.1); "
                    "вероятности книги, обновления в первой версии нет (§10)"}


def capex_block(A, ctx=None) -> dict:
    C, J = A["capex"], A["joint"]
    out = {"order": list(CAPEX_LEVELS), "titles": dict(CAPEX_TITLES),
           "levels": {c: float(C["levels"][c]) for c in CAPEX_LEVELS} if "levels" in C else None,
           "prob": {c: float(J["capex_prob"][c]) for c in CAPEX_LEVELS}, "levels_to": C["levels_to"],
           "converge_year": C.get("converge_year"), "lease_share": C["lease_share"],
           "asset_life_years": C["asset_life_years"], "rule": C.get("rule"),
           "prob_given_regime": ({r: {c: float(J["capex_prob_given_regime"][r][c]) for c in CAPEX_LEVELS}
                                  for r in REGIMES} if "capex_prob_given_regime" in J else None)}
    if C.get("rule") == "kappa":     # правило капиталоёмкости (MODEL.md §4.9): уровни — κ на конец levels_to
        out.update({"kappa_levels": {c: float(C["kappa_levels"][c]) for c in CAPEX_LEVELS},
                    "kappa_base_capex": C["kappa_base_capex"], "kappa_high_capex": C["kappa_high_capex"],
                    "first_share": C["first_share"], "floor_da": C["floor_da"],
                    # книга 1.2 (MODEL.md §0, §4.9): база κ и долей вложений уровней — `ex_fs` (без финсервисов) или
                    # null (выручка группы); окно базового уровня и правило высокого
                    "balance_base": A["meta"].get("balance_base"), "kappa_window": C.get("kappa_window"),
                    "kappa_high": C.get("kappa_high")})
        if A["meta"].get("release_rules") and ctx is not None:     # книга 1.4 (MODEL.md §14): правила капитала
            from model.core import anchor_rest, land_anchor
            from model.rederive import derive_world_weights
            land = C.get("anchor_land")
            out["rules"] = {
                "season": C.get("season"), "h1_dip": C.get("h1_dip"), "h1_share": C.get("h1_share"),
                "anchor_rest": C.get("anchor_rest"),
                "anchor_rest_bn": anchor_rest(ctx) if C.get("anchor_rest") == "cohort" else None,
                "anchor_land_bn": land_anchor(ctx) if land else None,
                "land_da_half": float(land["da_half"]) if land else None,
                "kappa_target": C.get("kappa_target"), "base_cell_capex": C.get("kappa_base_cell_capex"),
                "expectation": J.get("expectation"), "world_weights": derive_world_weights(A)}
    return out


def paths_block(grid) -> dict:
    halves = expected_path(grid)
    years = annual_path(grid, halves)
    flows = ("revenue", "ebitda", "attack", "sbc", "da", "ebit", "tax", "capex", "lease_new", "acq", "dwc", "dkb",
             "fcff", "nopat", "interest", "shield")
    annual = []
    prev = None
    for y in years:
        row = {"year": y["year"], **{k: y[k] for k in flows}, "nwc": y["nwc"], "fa": y["fa"],
               "margin": y["margin"], "capex_pct": y["capex_pct"],
               "revenue_growth": _div(y["revenue"], prev["revenue"]) - 1.0 if prev else None,
               "blocks": {b: dict(y["blocks"][b]) for b in BLOCKS}, "fact": y["fact"],
               "forecast_periods": y["forecast_periods"]}
        annual.append(row)
        prev = y
    return {"layer": "analytical", "block_order": list(BLOCKS), "block_titles": dict(BLOCK_TITLES),
            "annual": annual,
            "halves": [{"period": h["period"], "revenue": h["revenue"], "ebitda": h["ebitda"], "margin": h["margin"],
                        "fcff": h["fcff"], "capex": h["capex"] + h["lease_new"],
                        "blocks": {b: dict(h["blocks"][b]) for b in BLOCKS}} for h in halves],
            "note": "ожидаемый путь слоя «свой взгляд»: Σ вероятность клетки × строка; маржа — отношение средних; "
                    "год якоря — факт 1П + прогноз 2П в выручке и EBITDA (поле fact)"}


def debt_block(A, cf, grid) -> dict:
    an = grid.layers["analytical"]
    cl = grid.cells[0].result.claims
    static = cl["net_debt_nonbank"] + fsum(v for k, v in cl.items() if k.startswith("bridge.")) + cl["dividends"]
    labels = {line.get("fact", "").split(".")[-1]: line.get("label") for line in A["bridge"]["include"]}
    if A["meta"].get("release_rules"):        # книга 1.4 (MODEL.md §14): подписи строк моста словами
        from model.core import BRIDGE_LABELS
        labels = {**BRIDGE_LABELS, **labels}
    rows = [{"key": "net_debt_nonbank", "label": CLAIM_TITLES["net_debt_nonbank"], "amount": cl["net_debt_nonbank"]}]
    for k, v in cl.items():
        if k.startswith("bridge."):
            key = k.removeprefix("bridge.")
            rows.append({"key": key, "label": labels.get(key) or key, "amount": v})
    rows += [{"key": "dividends", "label": CLAIM_TITLES["dividends"], "amount": cl["dividends"]},
             {"key": "rollover", "label": CLAIM_TITLES["rollover"], "amount": an.d - static}]
    return {"anchor": {"as_of": A["meta"]["facts_date"], "net_debt_nonbank": cl["net_debt_nonbank"],
                       "net_debt_company": cl["net_debt_company"], "bank_money": cl["bank_money"]},
            "bridge": {"ev_rows": [{"key": k, "label": EV_ROW_TITLES[k], "amount": v} for k, v in an.ev_parts],
                       "v0": an.v0, "rows_at_valuation": rows, "total": an.d, "equity": an.equity,
                       "equity_rows": [], "treasury_mln": 0.0, "shares_mln": grid.ctx.n_eff},
            "bonds": [], "bank_loans": None, "wall": [], "wall_note": None}


def dividends_block(cf, v: dt.date) -> dict:
    reg = [{"label": d.label, "amount": d.amount, "record_date": d.record_date.isoformat(), "paid": d.paid,
            "in_claims": d.record_date <= v} for d in cf.dividends]
    return {"policy": None, "register": reg, "history": [], "model": [], "next_expected": None,
            "yield_ltm": None, "note": "путь дивидендов модель не считает: поток — до финансирования (FCFF)"}


def history_block(cf) -> dict:
    def row(p):
        blocks = {b: {"revenue": cf.rev[b].get(p), "ebitda": cf.ebitda[b].get(p)} for b in BLOCKS}
        R, E = cf.rev["group"].get(p), cf.ebitda["group"].get(p)
        return {"period": p, "revenue": R, "ebitda": E, "margin": _div(E, R), "blocks": blocks}
    periods = sorted(p for p in cf.rev["group"] if "H" in p)
    years = sorted(p for p in cf.rev["group"] if len(p) == 4)
    return {"halves": [row(p) for p in periods], "annual": [{**row(y), "year": int(y)} for y in years],
            "block_order": list(BLOCKS)}


def judgements_block(dist) -> dict:
    rows = [{"id": j["key"], "name": j["name"], "unit": j["unit"], "book": j["book"], "low": j["low"],
             "high": j["high"], "price_low": j["price_low"], "price_high": j["price_high"], "swing": j["swing"],
             "share": j["share"], "kind": j["kind"], "paths": j["paths"], "in_core": j["in_core"]}
            for j in dist.judgements]
    rows.sort(key=lambda r: (not r["in_core"], -(r["swing"] or 0.0)))
    return {"rows": rows}


def uncertainty_block(dist, draws_low, draws_high) -> dict:
    b = dist.band
    c = [lo + b.lam * (hi - lo) for lo, hi in zip(draws_low, draws_high)]
    lo, hi = min(c), max(c)
    width = max(1.0, (hi - lo) / HISTOGRAM_MAX_BINS)
    step = 10 ** math.floor(math.log10(width))
    width = math.ceil(width / step) * step
    start = math.floor(lo / width) * width
    bins = [start + i * width for i in range(int((hi - start) // width) + 2)]
    return {"contributions": [{"axis": x["axis"], "key": x["key"], "share": x["share"], "rank_corr": x["rank_corr"]}
                              for x in dist.contributions],
            "draws": b.n, "seed": b.seed, "axes_count": len(b.used),
            "axes": [{"key": ax.key, "name": ax.name, "kind": ax.kind, "paths": list(ax.paths), "unit": ax.unit,
                      "book": ax.book, "low": ax.low, "high": ax.high, "in_core": ax.in_core} for ax in b.axes],
            "skipped_axes": [ax.name for ax in b.axes if not ax.in_core],
            "axis_prices": [{"key": a["key"], "name": a["name"], "price": a["price"],
                             "median_without": a["median_without"]} for a in dist.axis_prices or []] or None,
            "mean": fsum(c) / len(c), "histogram_bins": bins, "subsample": dist.subsample, "delta": dist.delta}


def reverse_block(dist, mp) -> dict:
    return {"rows": dist.reverse_dcf, "target": mp, "provisional": any(r["provisional"] for r in dist.reverse_dcf),
            "method": f"поиск на подвыборке {dist.subsample} прогонов со сдвигом к полной полосе, уточнение секущей "
                      "на полной полосе (docs/MODEL.md §11); состав осей — оси полосы, окно поиска — центр ± 3 полуразмаха (решение ведущего)"}


def _calendar() -> list[dict]:
    if not CALENDAR.exists():
        return []
    try:
        data = json.loads(CALENDAR.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    events = data.get("events") if isinstance(data, dict) else data
    return [e for e in events or [] if isinstance(e, dict) and _day(e.get("date"))]


def next_report_block(A, cf, grid, period: str | None, v: dt.date) -> dict:
    events = sorted((e for e in _calendar() if _day(e["date"]) >= v), key=lambda e: e["date"])
    # закрывающий отчёт — `kind == "report"` календаря семейства (common.calendar.closing_report); событие без вида
    # (прежний формат) — по одному `covers`
    closing = next((e for e in sorted(_calendar(), key=lambda e: e["date"])
                    if e.get("covers") == period and e.get("kind", "report") == "report"), None)
    exp = None
    halves = expected_path(grid)
    h = next((x for x in halves if x["period"] == period), None)
    if h is not None:
        was = cf.rev["group"].get(prev_same_half(period))
        exp = {"period": period, "revenue": h["revenue"], "ebitda": h["ebitda"], "margin": h["margin"],
               "revenue_growth": _div(h["revenue"], was) - 1.0 if _num(was) and was else None,
               "blocks": {b: dict(h["blocks"][b]) for b in BLOCKS}}
    return {"period": period, "events": events,
            "closing": None if closing is None else {
                "date": closing["date"], "title": closing.get("title"), "confirmed": closing.get("confirmed"),
                "published": _day(closing["date"]) <= v},
            "expectation": exp, "guidance": None, "benchmarks": [], "table": [],
            "note": "календарь data/calendar.json (family-calendar-v1); «что даст отчёт» в первой версии нет (§12)"}


def journal_block(previous, journal) -> tuple[dict, dict | None]:
    prev = journal if journal is not None else (previous or {}).get("journal")
    entries = journal_mod.entries_of(prev) if prev is not None else []
    out = {"entries": entries, "rule": "журнал прогнозов в первой версии не пополняется: записи прошлых выпусков "
                                       "переносятся как есть", "status": "не ведётся (первая версия)",
           "releases": {}}
    out["releases"] = journal_releases(out, previous, prev)
    return out, prev


def journal_releases(journal: dict, previous: dict | None, previous_journal=None, history=None) -> dict:
    """Книга и время сборки выпусков, записавших прогнозы журнала: {release_sha: {book_version, generated_at}}."""
    known: dict[str, dict] = {}
    for J in (previous_journal, (previous or {}).get("journal")):
        releases = J.get("releases") if isinstance(J, dict) else None
        for sha, info in (releases.items() if isinstance(releases, dict) else ()):
            if isinstance(info, dict):
                known.setdefault(sha, info)
    meta = (previous or {}).get("meta") or {}
    if meta.get("payload_sha256") and meta.get("book_version") is not None:
        known.setdefault(meta["payload_sha256"], {"book_version": str(meta["book_version"]),
                                                  "generated_at": meta.get("generated_at")})
    for row in (history if isinstance(history, list) else ()):
        if (isinstance(row, dict) and row.get("event") == "publish" and row.get("payload_sha256")
                and row.get("book_version") is not None):
            known.setdefault(row["payload_sha256"], {"book_version": str(row["book_version"]),
                                                     "generated_at": row.get("generated_at")})
    wanted = sorted({e.get("release_sha") for e in journal.get("entries") or [] if e.get("release_sha")})
    return {sha: {"book_version": known[sha].get("book_version"), "generated_at": known[sha].get("generated_at")}
            for sha in wanted if sha in known}


def checks_block(A, report, inputs: dict, period: str | None, closing: dict | None, v: dt.date) -> dict:
    """Проверки §13 (model/checks.py): инварианты, гейты, ловушки, порядок величин, развилки с ценой, флаги."""
    flags = checks_mod.flags(A, price_status=inputs["status"], price_reason=inputs.get("reason"), period=period,
                             closing=closing, valuation_date=v)
    return {"invariants": report.invariants, "gates": [g.to_json() for g in report.gates], "traps": report.traps,
            "magnitudes": report.magnitudes, "forks": report.forks,
            "flags": [{"name": f.name, "title": f.title, "raised": f.raised, "status": f.status, "detail": f.detail}
                      for f in flags],
            "note": "§13: инварианты и ловушки роняют сборку; сработавший гейт — только с объяснением "
                    "(data/assumptions/gate_explanations.yaml); not_computed — нечем посчитать, причина в detail"}


def _tile(tid, title, unit, value, date, history, previous_tiles) -> dict | None:
    if value is None:
        return previous_tiles.get(tid)
    if history is None:
        history = [h for h in (previous_tiles.get(tid) or {}).get("history") or [] if h.get("date") != date]
        history.append({"date": date, "value": value})
    full = sorted(history, key=lambda h: h["date"])
    earlier = [h for h in full if h["date"] < str(date)]
    lo = min(full, key=lambda h: h["value"]) if full else None
    hi = max(full, key=lambda h: h["value"]) if full else None
    return {"id": tid, "title": title, "unit": unit, "value": value, "date": date,
            "change": value - earlier[-1]["value"] if earlier else None,
            "change_from": earlier[-1]["date"] if earlier else None, "since": None,
            "min": lo and {"date": lo["date"], "value": lo["value"]},
            "max": hi and {"date": hi["date"], "value": hi["value"]}, "history": _thin(full, TILE_POINTS)}


def indicators_block(inputs, live, previous) -> dict:
    prev = {t.get("id"): t for t in ((previous or {}).get("indicators") or {}).get("tiles") or []}
    live = live or {}
    tiles = []
    if inputs["status"] != "book":
        hist = [{"date": str(r["date"]), "value": float(r["close"])}
                for r in live.get("price_history") or [] if _num(r.get("close")) and str(r["date"]) < inputs["price_date"]]
        hist.append({"date": inputs["price_date"], "value": inputs["market_price"]})
        tiles.append(_tile("yandex.price", "Акция YDEX", "rub", inputs["market_price"], inputs["price_date"], hist,
                           prev))
    key = live.get("key_rate") or {}
    if _num(key.get("value")):
        tiles.append(_tile("cbr.key_rate", "Ключевая ставка", "pct", float(key["value"]), str(key.get("date")),
                           None, prev))
    curve = live.get("curve") or {}
    for node, tid in (("5", "ofz.5y"), ("10", "ofz.10y")):
        value = (curve.get("nodes") or {}).get(node)
        tiles.append(_tile(tid, f"ОФЗ {node} лет, бескупонная", "pct", float(value) if _num(value) else None,
                           curve.get("as_of"), None, prev))
    return {"tiles": [t for t in tiles if t]}


def live_block(A, live, inputs) -> dict:
    live = live or {}
    price = live.get("price") or {}
    curve = live.get("curve") or {}
    nodes = curve.get("nodes") if isinstance(curve, dict) else None
    book_curve = A["worlds"][A["joint"]["neutral_world"]]["zero_curve"]
    key = live.get("key_rate") or {}
    return {"price": {"value": inputs["market_price"], "date": inputs["price_date"], "time": inputs["price_time"],
                      "source": _words(inputs["price_source"]), "status": inputs["status"],
                      "accepted": inputs["accepted"], "reason": inputs["reason"], "kind": price.get("kind"),
                      "last_accepted": price.get("last_accepted")},
            "curve": ({"as_of": curve.get("as_of"), "nodes": nodes,
                       "book_nodes": {k: float(book_curve[k]) for k in nodes if k in book_curve},
                       "shift_bp": {k: (float(nodes[k]) - float(book_curve[k])) * BP
                                    for k in ("5", "10") if k in nodes and k in book_curve}} if nodes else None),
            "key_rate": {"value": key.get("value"), "date": key.get("date"), "since": None} if key else None,
            "valuation_date": inputs["valuation_date"].isoformat(), "fetched_at": live.get("fetched_at"),
            "errors": live.get("errors") or {}, "degraded": bool(live.get("errors"))}


def inputs_block(A, inputs, live, v: dt.date) -> dict:
    live = live or {}
    curve, key = live.get("curve") or {}, live.get("key_rate") or {}

    def fresh(date) -> str:
        d = _day(date)
        return "ok" if d and (v - d).days <= STALE_DAYS else "stale"

    status_price = {"live": "ok", "book": "ok", "fallback": "fallback"}.get(inputs["status"], "ok")
    src = _words(inputs["price_source"]) + (f", сделка {inputs['price_time']} МСК" if inputs["price_time"] else "")
    anchor = A["meta"]["anchor_period"]
    return {"rows": [
        {"name": "Цена акции", "value": inputs["market_price"], "unit": "rub", "as_of": inputs["price_date"],
         "source": src, "status": status_price},
        {"name": "Кривая ОФЗ", "value": "узлы 1, 3, 5, 10 лет" if curve else None, "unit": None,
         "as_of": curve.get("as_of"), "source": _words(curve.get("source") or "ISS zcyc"),
         "status": fresh(curve.get("as_of")) if curve else "stale"},
        {"name": "Ключевая ставка", "value": key.get("value"), "unit": "pct", "as_of": key.get("date"),
         "source": _words(key.get("source")) or "Банк России", "status": "ok" if key else "stale"},
        {"name": "Книга допущений", "value": str(A["meta"]["version"]), "unit": "version",
         "as_of": A["meta"]["date"], "source": "репозиторий модели, книга data/assumptions", "status": "ok"},
        {"name": "Факты отчётности", "value": journal_mod.half_label(anchor), "unit": None,
         "as_of": A["meta"]["facts_date"], "source": "МСФО и датабук Яндекса (лист фактов)", "status": "ok"}]}


def book_block(A) -> dict:
    from model.book import get_node
    from model.book_schema import BookError

    src = A["worlds"]["source"]
    kj = []
    for jid, path, name, unit in KEY_JUDGEMENTS:
        try:
            node = get_node(A, path)
        except BookError:
            continue
        kj.append({"id": jid, "name": name, "value": float(node) if _num(node) else node, "unit": unit,
                   "path": path})
    return {"version": str(A["meta"]["version"]), "date": A["meta"]["date"], "tag": f"book-{A['meta']['version']}",
            "sections": [{"id": k, "title": t} for k, t in SECTIONS if k in A],
            "worlds_source": f"миры семейства {src.get('version')}: {src.get('book')}, кривая {src.get('curve_date')}",
            "facts_date": A["meta"]["facts_date"], "key_judgements": kj}


# ---------------------------------------------------------------- сборка


def build_payload(live: dict | None = None, previous: dict | None = None, journal=None, fast: bool = False, *,
                  book: dict | None = None, facts=None, strict: bool = True, notes_path=None,
                  n_workers: int | None = None, release_history=None, draws: int | None = None) -> dict:
    """Выпуск `yandex-v1` на книге, фактах и живых входах.

    `live` — живые входы конвейера или None (входы книги); `previous` — прошлый выпуск (атрибуция, защита
    заголовка, плитки); `journal` — журнал прогнозов ветки `data` (или из `previous`); `release_history` —
    `history.json` ветки `data`; `fast` — малая полоса (`valuation.uncertainty.small_draws`), обратный DCF только для точки, без цен осей
    (не выпуск: `meta.fast`). `strict` — при нарушенном тождестве или контракте бросить `ReleaseBlocked`; False —
    вернуть как есть (образец).
    """
    A = book if book is not None else load_book()
    F = facts if facts is not None else load_facts()
    cf = core_facts(F, A["meta"]["anchor_period"])
    inputs = live_inputs(A, live)
    v, mp = inputs["valuation_date"], inputs["market_price"]
    grid = evaluate(A, cf, valuation_date=v, market_price=mp)
    period = open_period(A)
    inv = grid_invariants(grid)
    blocking = [f"тождество {i['name']}: {i['detail']}" for i in inv if not i["ok"]]
    if strict and blocking:
        raise ReleaseBlocked(blocking)

    dist = distribution(A, cf, valuation_date=v, market_price=mp, draws=draws, fast=fast,
                        n_workers=n_workers)
    draws_low, draws_high = (list(x) for x in dist.band.rounded(DRAW_DECIMALS))
    stats = dist.stats
    report = checks_mod.run(grid, dist.band, stats["median"], n_workers=n_workers)
    if strict and report.blocking:
        raise ReleaseBlocked(report.blocking)
    next_report = next_report_block(A, cf, grid, period, v)
    journal_out, previous_journal = journal_block(previous, journal)
    if release_history is not None:
        journal_out["releases"] = journal_releases(journal_out, previous, previous_journal, release_history)

    meta = {"generated_at": generated_at(), "valuation_date": v.isoformat(), "facts_date": A["meta"]["facts_date"],
            "book_version": str(A["meta"]["version"]), "book_date": A["meta"]["date"],
            "engine_commit": engine_commit(), "basis": "скорр. EBITDA компании (после аренды офисов)",
            "shares_mln": grid.ctx.n_eff, "shares_outstanding_mln": cf.shares_mln,
            "governance_discount": grid.ctx.g_gov, "anchor_period": A["meta"]["anchor_period"],
            "first_period": A["meta"]["first_period"], "last_period": A["meta"]["last_period"],
            "open_period": period, "curve_as_of": A["meta"]["curve_as_of"],
            "closed_periods": grid.ctx.timing.closed, "elapsed": grid.ctx.timing.elapsed,
            "worlds_version": A["worlds"]["source"].get("version"), "fast": bool(fast), "draws": dist.band.n,
            "payload_sha256": None, "bytes": 0,
            "previous_sha256": ((previous or {}).get("meta") or {}).get("payload_sha256")}
    payload = {
        "schema": SCHEMA, "meta": meta, "market": market_block(A, cf, grid, inputs, live),
        "headline": headline_block(A, stats, mp, dist.band.n, dist.band.lam),
        "fair_value": fair_value_block(A, grid, stats, draws_low, draws_high, mp),
        "layers": {name: _layer(L) for name, L in grid.layers.items()},
        "grid": grid_block(grid), "worlds": worlds_block(A, grid), "regimes": regimes_block(A),
        "capex_levels": capex_block(A, grid.ctx), "paths": paths_block(grid), "debt": debt_block(A, cf, grid),
        "dividends": dividends_block(cf, v), "history": history_block(cf), "reverse_dcf": reverse_block(dist, mp),
        "judgements": judgements_block(dist), "uncertainty": uncertainty_block(dist, draws_low, draws_high),
        "next_report": next_report, "journal": journal_out,
        "calendar": {"events": [e for e in _calendar() if _day(e["date"]) >= v]},
        "checks": checks_block(A, report, inputs, period, next_report.get("closing"), v),
        "inputs": inputs_block(A, inputs, live, v),
        "live": live_block(A, live, inputs),
        "changes": {"vs_previous": attribution.vs_previous(A, cf, previous, valuation_date=v, market_price=mp,
                                                           point_now=grid.point.central, meta_now=meta)},
        "book": book_block(A), "indicators": indicators_block(inputs, live, previous)}
    payload = _tidy(payload)
    _seal(payload)

    problems = validate(payload, notes_path=notes_path, previous_journal=previous_journal)
    if problems:
        if strict:
            raise ReleaseBlocked([f"контракт: {p}" for p in problems])
        print("выпуск с нарушениями (strict=False):", *problems, sep="\n  ", file=sys.stderr)
    return payload


def seal(payload: dict) -> None:
    """Хэш содержания и размер заново — после шапки семейства (ops/build_release.py)."""
    _seal(payload)


def _seal(payload: dict) -> None:
    """Хэш содержания и размер."""
    payload["meta"]["payload_sha256"] = content_digest(payload)
    size = 0
    for _ in range(5):
        payload["meta"]["bytes"] = size
        new = len(compact_json(payload).encode("utf-8"))
        if new == size:
            break
        size = new


# ------------------------------------------------------------ проверка


def _nonfinite(node, where: str, out: list[str]) -> None:
    if isinstance(node, float) and not math.isfinite(node):
        out.append(f"{where} = {node!r}")
    elif isinstance(node, dict):
        for k, v in node.items():
            _nonfinite(v, f"{where}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _nonfinite(v, f"{where}[{i}]", out)


def _read_notes(path=None) -> list[dict]:
    path = Path(path) if path else RELEASE_NOTES
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [n for n in (data.get("notes") or []) if isinstance(n, dict)]


def release_notes_problems(path=None) -> list[str]:
    """Записка без срока или без ожидаемой медианы — ошибка сборки."""
    out = []
    for i, n in enumerate(_read_notes(path), start=1):
        where = f"release_notes.yaml, записка {i} ({n.get('date', '—')})"
        exp = n.get("expected_median")
        if not _num(exp) or exp <= 0:
            out.append(f"{where}: нет expected_median — ожидаемой медианы после правки, ₽")
        tol = n.get("tolerance_pct", NOTE_TOLERANCE_PCT)
        if not _num(tol) or not 0 < tol <= MAX_HEADLINE_JUMP * 100:
            out.append(f"{where}: tolerance_pct = {tol!r} — нужен процент в (0; 25]")
        if _day(n.get("valid_until")) is None:
            out.append(f"{where}: нет valid_until ГГГГ-ММ-ДД — бессрочная записка запрещена")
    return out


def headline_problems(payload: dict, *, notes_path=None, today: dt.date | None = None) -> list[str]:
    """Защита заголовка (§13.6): скачок медианы > 25 % или V0 медианы > 10 % против прошлого выпуска без новой
    книги, фактов или действующей записки."""
    ref = ((payload.get("changes") or {}).get("vs_previous") or {}).get("reference") or {}
    head, meta = payload.get("headline") or {}, payload.get("meta") or {}
    was_head, now_head = ref.get("printed_central"), head.get("printed_central")
    was_v0 = ref.get("v0_median")
    now_v0 = ((payload.get("fair_value") or {}).get("center_ev") or {}).get("v0_median")
    if ref.get("fast") or meta.get("fast"):
        return []           # быстрая сборка — не выпуск
    jumps = []
    if _num(was_head) and _num(now_head) and was_head > 0 and abs(now_head / was_head - 1) > MAX_HEADLINE_JUMP:
        jumps.append(f"медиана {was_head:.0f} → {now_head:.0f} ₽ ({now_head / was_head - 1:+.0%})")
    if _num(was_v0) and _num(now_v0) and was_v0 > 0 and abs(now_v0 / was_v0 - 1) > MAX_V0_JUMP:
        jumps.append(f"V0 медианы {was_v0:.1f} → {now_v0:.1f} млрд ₽ ({now_v0 / was_v0 - 1:+.1%})")
    if not jumps:
        return []
    if ref.get("book_version") != meta.get("book_version") or ref.get("facts_date") != meta.get("facts_date"):
        return []
    today = today or _today()
    central = head.get("central")
    for n in _read_notes(notes_path):
        until, exp = _day(n.get("valid_until")), n.get("expected_median")
        tol = n.get("tolerance_pct", NOTE_TOLERANCE_PCT)
        if until is None or until < today or not _num(exp) or exp <= 0 or not _num(tol):
            continue
        if _num(central) and abs(central / exp - 1) <= tol / 100:
            return []
    return ["скачок заголовка без новой книги, новых фактов или действующей записки "
            "(data/assumptions/release_notes.yaml): " + "; ".join(jumps)]


LAYER_P = {"analytical": "p_analytical", "market_implied": "p_market_implied", "macro_neutral": "p_neutral"}


def derived_problems(payload: dict) -> list[str]:
    """Производные выпуска против того, из чего они посчитаны (контроль после сборки).

    Прогоны: P ниже рынка и среднее заголовка, строки `fair_value.by_lambda`; рынок один; точка на отрезке
    низ–верх, концы — цены слоёв; слои: V0 = Σ PV-строк = Σ p·EV клеток, капитал = V0 − D, цена из капитала (§7.2);
    мост и `market.claims` — слой «свой взгляд»; пара «модель — рынок»; тождества сетки — все `ok`; вклады осей.
    Допуски — от округления выпуска (9 значащих цифр, цены — копейка)."""
    out: list[str] = []
    head, fv, mk, meta = payload["headline"], payload["fair_value"], payload["market"], payload["meta"]
    layers = payload.get("layers") or {}
    draws_low, draws_high, mp = fv["draws_low"], fv["draws_high"], head.get("market_price")

    def same(where: str, got, want, tol: float, basis: str) -> None:
        if not (_num(got) and _num(want) and abs(got - want) <= tol):
            shown = f"{want:.9g}" if _num(want) else repr(want)
            out.append(f"{where} = {got!r}, а {basis} даёт {shown}")

    def rel(*xs) -> float:
        return DERIVED_REL * max([1.0, *(abs(x) for x in xs if _num(x))])

    if mp != mk.get("price"):
        out.append(f"headline.market_price = {mp!r} ≠ market.price {mk.get('price')!r}")
    if not _num(mp):
        return out
    s = band_stats(draws_low, draws_high, head["lambda"], mp)
    if head.get("p_central_below_market") != _tidy(s["p_below"]):
        out.append(f"headline.p_central_below_market = {head.get('p_central_below_market')!r}, а доля прогонов "
                   f"ниже рынка — {_tidy(s['p_below'])!r}")
    same("headline.mean", head.get("mean"), s["mean"], HALF_CENT, "среднее прогонов")
    low, high, lam = fv.get("low"), fv.get("high"), fv.get("lambda")
    ends = _num(low) and _num(high)
    for r in fv.get("by_lambda") or []:
        x = r.get("lambda")
        if not _num(x):
            continue
        where = f"fair_value.by_lambda[λ={x:.2f}]"
        t = band_stats(draws_low, draws_high, x, mp)
        for k in ("median", "p10", "p25", "p75", "p90", "mean"):
            same(f"{where}.{k}", r.get(k), t[k], HALF_CENT, "статистика прогонов")
        if r.get("p_below") != _tidy(t["p_below"]):
            out.append(f"{where}.p_below = {r.get('p_below')!r}, а доля прогонов ниже рынка — {_tidy(t['p_below'])!r}")
        if ends:
            same(f"{where}.point", r.get("point"), low + x * (high - low), 2 * HALF_CENT, "низ + λ·(верх − низ)")
    an, neutral = layers.get("analytical") or {}, layers.get("macro_neutral") or {}
    same("fair_value.low", low, neutral.get("price"), HALF_CENT, "цена слоя macro_neutral")
    same("fair_value.high", high, an.get("price"), HALF_CENT, "цена слоя analytical")
    if lam != head.get("lambda"):
        out.append(f"fair_value.lambda = {lam!r} ≠ headline.lambda {head.get('lambda')!r}")
    if ends and _num(lam):
        same("fair_value.central", fv.get("central"), low + lam * (high - low), 2 * HALF_CENT,
             "низ + λ книги·(верх − низ)")

    g, shares = meta.get("governance_discount"), meta.get("shares_mln")
    if not (_num(g) and _num(shares) and shares > 0 and g < 1):
        out.append("meta.governance_discount или meta.shares_mln — не числа: цену слоёв из капитала не проверить")
        g = None
    cells = (payload.get("grid") or {}).get("cells") or []
    for name, L in layers.items():
        v0, d, equity = L.get("v0"), L.get("d"), L.get("equity")
        terms = [L.get("pv_explicit"), L.get("pv_shield"), L.get("pv_tv"), L.get("pv_tv_shield")]
        if not all(_num(x) for x in (v0, d, equity, *terms)):
            out.append(f"layers.{name}: v0, d, equity или PV-строки — не числа")
            continue
        same(f"layers.{name}.v0", v0, fsum(terms), rel(v0, *terms), "PV потока + щита + терминала + щита терминала")
        same(f"layers.{name}.equity", equity, v0 - d, rel(v0, d), "V0 − D")
        if g is not None:
            want = price_from_equity(equity, g, shares)
            same(f"layers.{name}.price", L.get("price"), want, rel(abs(equity) * 1000.0 / shares, want),
                 "цена из капитала (§7.2)")
        col = LAYER_P.get(name)
        if col and cells:
            p = [c.get(col) for c in cells]
            ev = [c.get("ev") for c in cells]
            if not all(_num(x) for x in (*p, *ev)):
                out.append(f"grid.cells: {col} или ev — не числа")
                continue
            same(f"grid.cells: Σ {col}", fsum(p), 1.0, DERIVED_REL, "сумма вероятностей слоя")
            same(f"layers.{name}.v0", v0, fsum(a * b for a, b in zip(p, ev)), rel(v0, *(a * b for a, b in zip(p, ev))),
                 "Σ p·EV клеток")

    bridge = (payload.get("debt") or {}).get("bridge") or {}
    for key, lkey in (("v0", "v0"), ("total", "d"), ("equity", "equity")):
        same(f"debt.bridge.{key}", bridge.get(key), an.get(lkey), rel(an.get(lkey)), f"слой analytical ({lkey})")
    same("market.claims", mk.get("claims"), an.get("d"), rel(an.get("d")), "D слоя analytical")
    for key, total in (("rows_at_valuation", "total"), ("ev_rows", "v0")):
        rows = [r.get("amount") for r in bridge.get(key) or []]
        if rows and all(_num(x) for x in rows):
            same(f"debt.bridge.{total}", bridge.get(total), fsum(rows), rel(*rows), f"сумма строк debt.bridge.{key}")
    ce = fv.get("center_ev") or {}
    same("fair_value.center_ev.v_star", ce.get("v_star"), mk.get("market_ev"), rel(mk.get("market_ev")),
         "market.market_ev")
    if g is not None and _num(an.get("d")):
        for key, price in (("v_star", mp), ("v0_median", head.get("central"))):
            if _num(price):
                want = v0_from_price(price, an["d"], g, shares)
                same(f"fair_value.center_ev.{key}", ce.get(key), want, rel(want, an["d"]) + HALF_CENT * shares / 1000,
                     f"V0 цены {price} (§7.3)")
    inv = (payload.get("checks") or {}).get("invariants")
    if not inv:
        out.append("checks.invariants пуст")
    else:
        failed = [str(i.get("name")) for i in inv if i.get("ok") is not True]
        if failed:
            out.append(f"checks.invariants: не выполнены {', '.join(failed)}")
    ck = payload.get("checks") or {}
    gl = ck.get("gates") or []
    if [g.get("name") for g in gl] != list(checks_mod.GATES):
        out.append("checks.gates: состав не равен гейтам §13.2 (model/checks.py: GATES)")
    for g in gl:
        if g.get("status") not in checks_mod.STATUSES or not isinstance(g.get("fired"), bool):
            out.append(f"checks.gates[{g.get('name')}]: статус {g.get('status')!r} или fired не из контракта")
        elif g["status"] in BLOCKING_GATE_STATUSES or g["fired"] != (g["status"] in ("explained",)):
            out.append(f"checks.gates[{g.get('name')}]: сработал без объяснения ({g['status']})")
    failed = [str(t.get("name")) for t in ck.get("traps") or [] if t.get("ok") is not True]
    if not ck.get("traps") or failed:
        out.append(f"checks.traps: {'не выполнены ' + ', '.join(failed) if failed else 'пуст'}")
    contrib = (payload.get("uncertainty") or {}).get("contributions") or []
    if contrib:
        rc = [c.get("rank_corr") for c in contrib]
        share = [c.get("share") for c in contrib]
        if not all(_num(x) for x in (*rc, *share)):
            out.append("uncertainty.contributions: share или rank_corr — не числа")
        elif (total := fsum(r * r for r in rc)) > 0:
            same("uncertainty.contributions: Σ share", fsum(share), 1.0, DERIVED_REL, "сумма долей осей")
            for c, r, s_ in zip(contrib, rc, share):
                same(f"uncertainty.contributions[{c.get('axis')}].share", s_, r * r / total, 10 * DERIVED_REL,
                     "rank_corr² / Σ rank_corr²")
    return out


def validate(payload: dict, *, notes_path=None, previous_journal=None, today: dt.date | None = None) -> list[str]:
    """Нарушения контракта `yandex-v1` (пусто — годен)."""
    out: list[str] = []
    if not isinstance(payload, dict):
        return ["выпуск — не объект"]
    if payload.get("schema") != SCHEMA:
        out.append(f"schema = {payload.get('schema')!r}, ожидается {SCHEMA}")
    missing = [b for b in REQUIRED_TOP_LEVEL if b not in payload]
    extra = [b for b in payload if b not in REQUIRED_TOP_LEVEL and b not in OPTIONAL_TOP_LEVEL]
    if missing:
        out.append(f"нет блоков: {', '.join(missing)}")
    if extra:
        out.append(f"необъявленные блоки: {', '.join(extra)}")
    if missing:
        return out
    bad: list[str] = []
    _nonfinite(payload, "$", bad)
    if bad:
        out.append("неконечные числа: " + "; ".join(bad[:5]))
    try:
        size = len(compact_json(payload).encode("utf-8"))
    except ValueError as exc:
        out.append(f"не строгий JSON: {exc}")
        size = None
    if size is not None:
        if size > PAYLOAD_MAX_BYTES:
            out.append(f"размер {size} байт при потолке {PAYLOAD_MAX_BYTES}")
        if payload["meta"].get("bytes") != size:
            out.append(f"meta.bytes = {payload['meta'].get('bytes')} ≠ {size}")
    try:
        if payload["meta"].get("payload_sha256") != content_digest(payload):
            out.append("meta.payload_sha256 не совпадает с содержанием")
    except ValueError:
        pass
    head, fv = payload["headline"], payload["fair_value"]
    step = head.get("print_step")
    if not _num(step) or step <= 0:
        out.append(f"headline.print_step = {step!r}")
    else:
        pairs = [("headline.central", head.get("central"), head.get("printed_central"))]
        pairs += [(f"headline.band[{i}]", a, b)
                  for i, (a, b) in enumerate(zip(head.get("band") or [], head.get("printed_band") or []))]
        pairs += [(f"headline.inner[{i}]", a, b)
                  for i, (a, b) in enumerate(zip(head.get("inner") or [], head.get("printed_inner") or []))]
        pairs += [(f"fair_value.{k}", fv.get(k), (fv.get("printed") or {}).get(k)) for k in ("low", "central", "high")]
        for where, value, printed in pairs:
            if not _num(value) or printed != round_to_step(value, step):
                out.append(f"печать {where}: {printed!r} ≠ округлению {value!r} к шагу {step}")
        if len(head.get("band") or []) != 2 or len(head.get("inner") or []) != 2:
            out.append("headline.band и inner — пары [нижняя, верхняя]")
    n = head.get("draws")
    if not isinstance(n, int) or n < 1 or len(fv.get("draws_low") or []) != n or len(fv.get("draws_high") or []) != n:
        out.append(f"длины draws_low/draws_high ≠ headline.draws ({n})")
    elif not bad and _num(head.get("lambda")) and _num(head.get("market_price")):
        s = band_stats(fv["draws_low"], fv["draws_high"], head["lambda"], head["market_price"])
        got = [s["median"], s["p10"], s["p90"], s["p25"], s["p75"]]
        want = [head.get("central"), *(head.get("band") or [None] * 2), *(head.get("inner") or [None] * 2)]
        if not all(_num(w) and abs(g - w) <= HALF_CENT for g, w in zip(got, want)):
            out.append("заголовок не совпадает со статистикой прогонов draws_low/draws_high")
        try:
            out += derived_problems(payload)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError) as exc:
            out.append(f"сверка производных не прошла: {type(exc).__name__}: {exc}")
    rows = fv.get("by_lambda") or []
    if len(rows) != LAMBDA_ROWS or any(not _num(r.get("lambda")) or abs(r["lambda"] - i / (LAMBDA_ROWS - 1)) > 1e-9
                                       for i, r in enumerate(rows)):
        out.append(f"fair_value.by_lambda — не {LAMBDA_ROWS} строк λ = 0; 0,05; …; 1")
    if abs(fv.get("lambda_step", 0) * (LAMBDA_ROWS - 1) - 1) > 1e-9:
        out.append("fair_value.lambda_step ≠ 0,05")
    cells = (payload.get("grid") or {}).get("cells") or []
    if len(cells) != len(WORLDS) * len(REGIMES) * len(CAPEX_LEVELS):
        out.append(f"grid.cells: {len(cells)} клеток")
    ids = [e.get("id") for e in (payload.get("journal") or {}).get("entries") or []]
    if len(ids) != len(set(ids)) or None in ids:
        out.append("journal: id записей не уникальны или пусты")
    if previous_journal is not None:
        out += journal_mod.immutability_problems(previous_journal, payload["journal"])
    out += release_notes_problems(notes_path)
    out += headline_problems(payload, notes_path=notes_path, today=today)
    return out
