"""Мутационный набор модели Яндекса (задача 14г плана): каждая порча своего кода и изменённого общего кода сектора
обязана уронить тест такта или второго счёта.

Порча — правка одной строки ИСХОДНИКА (кода или книги) в отдельной копии репозитория. Копия рабочего дерева
делается один раз на рабочий процесс (`git clone --shared` и поверх неё файлы рабочего дерева, включая
несохранённые правки; прежние книги тесты читают копиями `tests/fixtures/books`, история им не нужна); порча
ставится в копии, проверки идут подпроцессом `pytest -x <выбранные тесты>`, после прогона файл возвращается. Рабочая копия не трогается.

Проверки порчи — выбранные тесты, которые обязаны её уронить: тождества и тесты правил ядра (такт), второй счёт
(`tests/test_control_model.py`: клетка второго счёта на книге ядра против клетки ядра до 1e-9). Закреплённое число
засчитывается только там, где его же закрепляет второй счёт (точка и поток 9М26 книги 1.4, выведенные числа книги —
`test_all_derived_match_core_book`, `test_book14_gates`, `test_book14_own_numbers`). Порча с непустым `uncaught` —
«не ловится»: место и предложение теста; раннер проверяет, что она действительно проходит.

Сторож общего слоя (`common/tests/test_common_guard.py`, хэши `common/MANIFEST.sha256`) роняет ЛЮБУЮ порчу
`common/`; в проверки он не входит — меряется, ловят ли порчу тесты поведения.

Команды (из корня репозитория, venv модели):
    python -m tests.mutations                  # весь набор: сначала чистый прогон всех проверок, потом порчи
    python -m tests.mutations --jobs 3         # три рабочих копии параллельно
    python -m tests.mutations --only 415-C_flow,403-M2 [--no-baseline]   # точный id или начало id группы
    python -m tests.mutations --list           # порчи и их проверки, без прогона
    python -m tests.mutations --targets        # только: каждая строка-цель находится (секунда)
    python -m tests.mutations --json var/mutations.json
Время (оценка по пробам 415): чистый прогон проверок ≈ 3 мин; порча — 1–20 с, если её ловит тест книги или
перевывода, ≈ 80 с, если доходит до клетки второго счёта; весь набор ≈ 35 мин в одну копию, ≈ 12 мин с --jobs 4.
Код выхода: 0 — каждая порча поймана (а отмеченные «не ловится» и холостая — не пойманы); 1 — нет; 2 — набор
сломан (строка-цель не найдена, чистый прогон красный, тест не найден). Если строку-цель переписали, порчу
перенацеливают, а не удаляют (образец — `850-models/t/tests/mutations.py`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    id: str                                  # «403-M1» — порча приёмки 403; «414-K2_cohort» — 414; «415-…» — новая
    title: str
    path: str                                # путь от корня репозитория
    find: str
    replace: str
    checks: tuple[str, ...]                  # узлы pytest, которые обязаны упасть (быстрые и верные — первыми)
    nth: int | None = None                   # None — строка единственная; иначе номер вхождения с нуля
    uncaught: str = ""                       # «не ловится»: место и предложение теста
    idle: bool = False                       # холостая порча (проверка раннера): обязана пройти
    module: str = field(default="")          # группа отчёта; по умолчанию — путь


# ------------------------------------------------------------------------------------------------ проверки
B13 = "tests/test_book_13.py::"
B14 = "tests/test_book_14.py::"
CHK = "tests/test_checks.py::"
CS = "tests/test_core_services.py::"
CTL = "tests/test_control_model.py::"
PUB = "tests/test_publish.py::"

# второй счёт: клетки на книге ядра против ядра до 1e-9 (строки блоков и группы, TV, V0, требования)
CELL = (CTL + "test_all_cells_match_core_on_core_book[N-base-base]",
        CTL + "test_all_cells_match_core_on_core_book[H-stress-low]",
        CTL + "test_all_cells_match_core_on_core_book[M-strong-high]")
FIX = CS + "test_rederive_is_a_fixed_point_of_the_book"        # перевывод = выведенные числа книги (их держит 2-й счёт)
TRAPS = CHK + "test_traps_of_section_13_3_hold"                 # ловушки §13.3, среди них «одна база»
PIN14 = B14 + "test_book_14_point_median_and_flow_are_pinned"    # точка и поток 1.4 = числам второго счёта
PIN13 = B13 + "test_book_13_point_median_and_flow_are_pinned"    # точка книги 1.3 = числам второго счёта 1.3
YAML = "data/assumptions/assumptions.yaml"


def M(id_, title, path, find, replace, checks, **kw) -> Mutation:
    return Mutation(id_, title, path, find, replace, tuple(checks), **kw)


MUTATIONS: list[Mutation] = [
    # ---------------------------------------------------------------- порчи приёмки 403 (книга 1.3), перенацеленные
    M("403-M1", "остаток якоря — 2L−1 полугодий вместо 2L (ветка книги 1.4, с землёй)", "model/core.py",
      "add = (rest / N2 if 1 <= k <= N2 else 0.0) + land_da(ctx, k)",
      "add = (rest / N2 if 1 <= k < N2 else 0.0) + land_da(ctx, k)",
      [CELL[0], B13 + "test_anchor_rest_is_written_off_as_a_cohort"]),
    M("403-M1b", "остаток якоря — 2L−1 полугодий (ветка книги 1.3, без земли)", "model/core.py",
      "if rest is not None and 1 <= k <= N2:", "if rest is not None and 1 <= k < N2:",
      [B13 + "test_anchor_rest_is_written_off_as_a_cohort", PIN13]),
    M("403-M2", "провал κ на 31.12 вместо 30.06", "model/core.py",
      "(period_index(first) + i) % 2 == 0", "(period_index(first) + i) % 2 == 1",
      [B13 + "test_h1_dip_lowers_kappa_on_30_06_only", CELL[0]]),
    M("403-M3", "хвост когорт терминала без провала κ", "model/core.py",
      "cx_k = max(d_k + (kH1 if h == 0 else kC) * (ro_k + r_prev) - fa, phi * d_k)",
      "cx_k = max(d_k + kC * (ro_k + r_prev) - fa, phi * d_k)",
      [B13 + "test_terminal_cohorts_carry_the_dip", CELL[0]]),
    M("403-M4", "высокий уровень κ зеркален окну, а не D_b", "model/rederive.py",
      "return expect(kappa_for(d), kappa_for(2.0 * d - d_low))",
      "return expect(kappa_for(d), kappa_for(2.0 * window - d_low))",
      [TRAPS, FIX]),
    M("403-M5", "гейт отдачи прироста: сторона клеток по ставке клетки", "model/checks.py",
      'res.group["ebit"][i] - res.group["tax"][i] * tg / _tau(A, yl)', 'res.group["ebit"][i] - res.group["tax"][i]',
      [B13 + "test_roic_gate_without_the_key_uses_the_cell_rates",       # ветка без `tax.general_blocks` — книга 1.3
       B14 + "test_roic_gate_one_season_and_general_part"]),
    M("403-M6", "отдача окна по τ̄, а не по τ_g", "model/rederive.py", "t12 = tg if g4 else tau", "t12 = tau",
      [B13 + "test_window_roic_is_at_the_general_rate_with_the_it_contribution", FIX]),
    M("403-M7", "книга: граница оси налога IT — [0; 1] без общей ставки", YAML,
      "unit: ставка, bounds: [0.0, 0.25], bounds_from: [null, tax.general]}", "unit: ставка, bounds: [0.0, 1.0]}",
      [B14 + "test_book_14_carries_every_key", B13 + "test_it_axis_is_capped_at_the_general_rate"]),
    M("403-M8", "центр маржи A с долей взносов x_B", "model/rederive.py",
      '(ssum(eb["A"], cal.ltm) - xit["A"]) / RA', '(ssum(eb["A"], cal.ltm) - xit["B"]) / RA',
      [B13 + "test_margin_centres_and_other_share_without_the_4q25_contribution", FIX]),
    M("403-M9", "налог нетто-исполнения × доля нетто-исполнения 0,7", "model/core.py",
      'self.bridge["net_exercise_tax"] = float(nxt) * self.options_div *',
      'self.bridge["net_exercise_tax"] = float(nxt) * float(val["net_exercise_k"]) * self.options_div *',
      [B14 + "test_divisor_and_net_exercise_tax_with_the_recognised_part", CELL[0]]),
    M("403-M10", "прирост капитала банка 2П26 от R12 2025H2, а не якоря", "model/core.py",
      "dKb[0] = kappa * (RG[P[0]] + RG[anchor]) - cf.bank_capital[cf.facts_date]",
      "dKb[0] = kappa * (RG[P[0]] + RG[prev_same_half(P[0])]) - cf.bank_capital[cf.facts_date]",
      [B13 + "test_bank_capital_increment_of_2026h2_is_from_the_fact", CELL[0]]),
    M("403-M11", "поток 9М26: курсовая разница не вычтена", "model/checks.py",
      "f_fact = f_fact - fsum(fin.values()) - fsum(loan.values()) - fx",
      "f_fact = f_fact - fsum(fin.values()) - fsum(loan.values())",
      [B13 + "test_flow_9m26_without_bridge_asset_flows", PIN14]),
    M("403-M12", "поток 9М26: оборотный 30.09 без движения III кв.", "model/checks.py",
      "for k in excl) - fin[obs] - loan[obs])", "for k in excl))",
      [B13 + "test_flow_9m26_without_bridge_asset_flows", PIN14,
       CHK + "test_explanations_file_covers_every_fired_gate"]),
    M("403-M13", "«вес 0» не срабатывает у всех гейтов клеток, а не у двух (= 414 M13_zero)", "model/checks.py",
      "if zw and g.name in CELL_MASS_GATES and not g.layer:", "if zw and not g.layer:",
      [B14 + "test_zero_weight_pass_only_for_capital_and_terminal_share"]),
    M("403-M14", "щит хвоста когорт без предела 25 %", "model/core.py",
      "            if cap is not None:\n                t_k = min(t_k, cap)",
      "            if False:\n                t_k = min(t_k, cap)",
      [B13 + "test_shield_of_the_cohort_tail", *CELL]),
    M("403-M15", "доля I полугодия: 2026 г. без новой аренды", "model/rederive.py",
      "out[cal.anchor[:4]] = cx(cal.anchor) / (cx(cal.anchor) + h2)",
      "out[cal.anchor[:4]] = cf.capex[cal.anchor] / (cf.capex[cal.anchor] + h2)",
      [FIX, B13 + "test_h1_share_of_capex_equals_the_target"]),
    M("403-M16", "δ подбирается по вложениям без аренды", "model/rederive.py",
      'c = x.group["capex"][i] + x.group["lease_new"][i]', 'c = x.group["capex"][i]',
      [FIX, B13 + "test_h1_share_of_capex_equals_the_target"]),
    M("403-M17", "сверка срока без остатка якоря в месте вызова (= 414 M17_call)", "model/rederive.py",
      'chk = da_check(cf, cal, life, opt(A, "capex", "anchor_rest") == "cohort",\n'
      '                   None if land is None else float(land["da_half"]))', "chk = da_check(cf, cal, life)",
      [B14 + "test_derive_passes_rest_and_land_to_the_term_check"]),
    M("403-M19", "книга: строка моста долгосрочных инвестиций в аренду снята", YAML,
      "    - {fact: onca_net_lease_investment, side: asset, label: чистые инвестиции в аренду долгосрочные}", "    #",
      [B13 + "test_bridge_claims_of_book_13", PIN14]),
    M("403-M21", "книга: налог к уплате вне моста (`income_tax_place: none`)", YAML,
      "income_tax_place: bridge ", "income_tax_place: none ",
      [B13 + "test_bridge_claims_of_book_13", PIN14]),
    # ---------------------------------------------------------------- порчи приёмки 414 (книга 1.4)
    M("414-K1_year", "один состав не трогает годовые факты", "model/facts.py",
      'return period in (q, f"{y}H{1 if int(n) <= 2 else 2}", y)',
      'return period in (q, f"{y}H{1 if int(n) <= 2 else 2}")',
      [B14 + "test_facts_of_one_composition"]),
    M("414-K1_Fo", "один состав без «прочих» F", "model/facts.py",
      '(ebitda["F"], 1.0),\n                             (ebitda["F_o"], 1.0)):', '(ebitda["F"], 1.0)):',
      [B14 + "test_facts_of_one_composition"]),
    M("414-K2_cohort", "банк в когортах терминала без роста s_k", "model/core.py",
      "(gen[h][0] * s_k, gen[h][1]), d_k)", "(gen[h][0], gen[h][1]), d_k)",
      [B14 + "test_general_blocks_tax", *CELL]),
    M("414-K2_itshare", "доля IT подбирается на базе с банком", "model/rederive.py",
      "- general_part_fact(A, cf, p)) for p in tp)", "- 0.0) for p in tp)",
      [TRAPS, FIX]),
    M("414-K2_flow", "поток 9М26 без части банка", "model/checks.py",
      "(general_part_fact(A, cf, q9), 0.0), 0.0)", "(0.0, 0.0), 0.0)",
      [B14 + "test_flow_9m26_without_claims_movements", PIN14]),
    M("414-K2_roic", "гейт отдачи без части банка", "model/checks.py",
      'return e - res.group["da"][i] * sh', "return 0.0",
      [B14 + "test_roic_gate_one_season_and_general_part",
       CHK + "test_roic_increment_is_accepted_on_the_expectation_cells_only_advise"]),
    M("414-K3_levels", "уровни κ по весам слоя, а не заголовка", "model/rederive.py",
      "pw = derive_world_weights(B)                       # книга 1.4 (§4.9): веса заголовка",
      'pw = {w: float(B["joint"]["world_prob"][w]) for w in WORLDS}',
      [TRAPS, FIX], nth=1),
    M("414-K3_cellw", "веса гейтов клеток — слой, а не заголовок", "model/checks.py",
      'if opt(grid.ctx.A, "joint", "expectation") == "headline":', "if False:",
      [B14 + "test_headline_weights"]),
    M("414-K3_flow", "вложения 2П26 в потоке 9М26 по весам слоя", "model/checks.py",
      "pw = derive_world_weights(A)                 # книга 1.4 (`joint.expectation: headline`) — веса заголовка",
      'pw = {w: float(A["joint"]["world_prob"][w]) for w in WORLDS}',
      [PIN14, B14 + "test_headline_weights"]),
    M("414-K4_term", "земля в когортах терминала со сдвигом на полугодие", "model/core.py",
      "d_k = d_k + land_da(ctx, len(CX) + 1 + k)", "d_k = d_k + land_da(ctx, len(CX) + k)",
      [B14 + "test_land_is_written_off_by_the_fact_to_the_end", CELL[0]]),
    M("414-K4_check", "сверка срока без земли внутри функции", "model/rederive.py",
      "rv, da_next = rv - l0, da_next + min(land, l0)", "rv, da_next = rv - l0, da_next",
      [B14 + "test_term_check_uses_the_core_da_of_2026h2"]),
    M("414-K4_call", "сверка срока без земли в месте вызова", "model/rederive.py",
      'None if land is None else float(land["da_half"]))', "None)",
      [B14 + "test_derive_passes_rest_and_land_to_the_term_check"]),
    M("414-K5_dip", "строки Гордона без провала κ", "model/core.py",
      "f1 = (kc - dl) * (RTO[0] + RO[-1])", "f1 = kc * (RTO[0] + RO[-1])",
      [B14 + "test_gordon_rows_carry_the_dip", CELL[0]]),
    M("414-K6_tax", "налог нетто-исполнения с исполнимых, без признанной части", "model/core.py",
      'self.bridge["net_exercise_tax"] = float(nxt) * self.options_div *',
      'self.bridge["net_exercise_tax"] = float(nxt) * cf.options_vested_mln *',
      [B14 + "test_divisor_and_net_exercise_tax_with_the_recognised_part", CELL[0]]),
    M("414-K6_trap", "ловушка делителя без признанной части", "model/checks.py",
      'o_d = o_d + float(rho) * (need(cf.F, f"equity.{cf.facts_date}.options_outstanding") - o_d)', "o_d = o_d",
      [TRAPS, B14 + "test_divisor_and_net_exercise_tax_with_the_recognised_part"]),
    M("414-K7_bounds", "граница оси IT — число книги, а не общая ставка", "model/uncertainty.py",
      'if "bounds_from" in spec:', "if False:",
      [B14 + "test_it_axis_bound_follows_the_general_rate"]),
    M("414-K8", "книга: норма налога 2026–27 прежняя, книги 1.3 (= 403 M20)", YAML,
      "tax_2026_27: [0.208, 0.288]", "tax_2026_27: [0.202, 0.282]",
      [B14 + "test_book_14_carries_every_key", B14 + "test_tax_gate_norm_in_the_gate_form"]),
    M("414-K9_claims", "поток 9М26 без удержанного налога нетто-исполнения", "model/checks.py",
      "f_fact = f_fact + (np_start - np_end) - lease + wt", "f_fact = f_fact + (np_start - np_end) - lease",
      [B14 + "test_flow_9m26_without_claims_movements", PIN14]),
    M("414-K10_season", "гейт отдачи: капитал на 31.12, а не одного сезона", "model/checks.py",
      'fas[key] = res.group["fa"][-2] if season1 else res.group["fa"][-1]', 'fas[key] = res.group["fa"][-1]',
      [B14 + "test_roic_gate_one_season_and_general_part"]),
    M("414-K11_held", "ключи развилки не держатся при перевыводе", "model/checks.py",
      "return any(path[:len(h)] == h for h in held)", "return False",
      [B14 + "test_fork_rederivation_holds_the_variant_keys"]),
    M("414-K12_rules", "выпуск: правила капитала без земли", "model/payload.py",
      '"anchor_land_bn": land_anchor(ctx) if land else None,', '"anchor_land_bn": None,',
      [B14 + "test_release_carries_capital_rules_and_bridge_labels"]),
    M("414-K12_labels", "выпуск: подписи моста ключами", "model/payload.py",
      "labels = {**BRIDGE_LABELS, **labels}", "labels = labels",
      [B14 + "test_release_carries_capital_rules_and_bridge_labels"]),
    M("414-S_kappacx", "ловушка «одна аренда» без провала в своей линии κ", "model/checks.py",
      'line = line - float(C["h1_dip"])', "line = line + 0.0",
      [B14 + "test_one_lease_trap_does_not_share_the_kappa_line"]),
    # ---------------------------------------------------------------- 415: ключи 1.3 и 1.4 без своих порч
    M("415-K13_base", "книга: возвращён `tax.base_rule: f_other` (книга 1.3 — база налога книги 1.1)", YAML,
      "  general_blocks: [G] ", "  base_rule: f_other\n  general_blocks: [G] ",
      [FIX, PIN14]),
    M("415-K14_gbda", "доля амортизации банка не снята (`tax.general_blocks_da: none` не действует в ядре)",
      "model/core.py", '    if opt(A, "tax", "general_blocks_da") == "none":', "    if False:",
      [B14 + "test_general_blocks_tax", *CELL]),
    # ---------------------------------------------------------------- 415: общий код сектора, правки модели
    M("415-U_inrange", "допуск края `in_range` снят", "model/uncertainty.py",
      "    return lo - IN_RANGE_EPS * (hi - lo) <= x <= hi + IN_RANGE_EPS * (hi - lo)", "    return lo <= x <= hi",
      [B13 + "test_in_range_tolerance_of_the_reverse_dcf"]),
    M("415-U_offset", "края оси со сдвигами — сдвиги без значения книги", "model/uncertainty.py",
      "                low, high = book + d_lo, book + d_hi", "                low, high = d_lo, d_hi",
      ["tests/test_band.py::test_axes_follow_section_9_and_take_ranges_from_the_book"]),
    M("415-P_e27", "точка: знаменатель EV/EBITDA 2027 г. — слой «свой взгляд», не веса заголовка", "model/point.py",
      'if A["joint"].get("expectation") == "headline":', "if False:",
      [B14 + "test_ebitda_2027_of_the_broker_gate_and_ev_ebitda_are_on_headline_weights"]),
    M("415-G_e27", "сетка: EBITDA 2027 г. точки — слой «свой взгляд», не веса заголовка", "model/grid.py",
      'if ctx.A["joint"].get("expectation") == "headline":', "if False:",
      [B14 + "test_ebitda_2027_of_the_broker_gate_and_ev_ebitda_are_on_headline_weights"]),
    M("415-PAY_head", "хэш выпуска ядра считает время сборки шапки", "model/payload.py",
      'if k != "generated_at"}}', 'if k != "build_moment"}}',
      [PUB + "test_the_build_moment_of_the_family_header_is_not_content",
       "tests/test_payload.py::test_family_header_is_valid_and_sealed_after_it"]),
    M("415-PUB_head", "публикация: хэш считает время сборки шапки", "ops/publish.py",
      'if k != "generated_at"}}', 'if k != "build_moment"}}',
      [PUB + "test_the_build_moment_of_the_family_header_is_not_content"]),
    M("415-PUB_hash", "публикация: хэш без сортировки ключей — не зеркало ядра", "ops/publish.py",
      "sort_keys=True", "sort_keys=False",
      [PUB + "test_the_digest_matches_the_engine_and_ignores_the_moment_of_publication"]),
    # ---------------------------------------------------------------- 415: своё вне ключей — ядро
    M("415-C_flow", "поток: покупки компаний не вычитаются", "model/core.py",
      "FCFF.append(Eg[i] - SBC[i] - tax - CX[i] - MA[i] - dNWC[i] - dKb[i])",
      "FCFF.append(Eg[i] - SBC[i] - tax - CX[i] - dNWC[i] - dKb[i])",
      [CS + "test_the_flow_is_the_sum_of_its_lines", CELL[0]]),
    M("415-C_tax", "налог: ставка следующего года", "model/core.py",
      "tax = _tax_split(A, _tau(A, year[i]), base, gpart, DA[i])",
      "tax = _tax_split(A, _tau(A, year[i] + 1), base, gpart, DA[i])",
      [*CELL]),
    M("415-C_bridge", "мост: активы со знаком долга", "model/core.py",
      'self.bridge[line["fact"]] = -amount if line["side"] == "asset" else amount',
      'self.bridge[line["fact"]] = amount if line["side"] == "asset" else amount',
      [CELL[0]]),
    M("415-C_div", "делитель: опционы без доли нетто-исполнения", "model/core.py",
      'self.n_eff = cf.shares_mln + float(val["net_exercise_k"]) * self.options_div',
      "self.n_eff = cf.shares_mln + self.options_div",
      [B14 + "test_divisor_and_net_exercise_tax_with_the_recognised_part", CTL + "test_point_matches_core_point"]),
    M("415-C_term", "терминал: рост — сумма реального роста и инфляции, не произведение", "model/core.py",
      'g = (1.0 + float(A["terminal"]["real_growth"])) * (1.0 + pi_lt) - 1.0',
      'g = float(A["terminal"]["real_growth"]) + pi_lt',
      [CELL[0]]),
    # ---------------------------------------------------------------- 415: перевывод, факты, книга
    M("415-R_regimes", "режимы без сдвига к среднему = центру", "model/rederive.py",
      "    delta = center - fsum(xs) / 4", "    delta = 0.0",
      [CS + "test_regimes_rule_keeps_the_center_on_skewed_ranges", FIX]),
    M("415-R_beta", "β: доходность акции без дивиденда", "model/rederive.py",
      "            rs.append((ps[w] + div) / ps[prev] - 1)", "            rs.append(ps[w] / ps[prev] - 1)",
      [FIX]),
    M("415-R_life", "срок амортизации округлён до года, не до полугодия", "model/rederive.py",
      "    return round(2 * raw) / 2, raw, parts", "    return round(raw), raw, parts",
      [FIX]),
    M("415-F_fa", "капитал якоря без права пользования землёй", "model/facts.py",
      '+ sum(need(F, f"balance.{adate}.rou_{c}") for c in ("cars", "land", "other")))',
      '+ sum(need(F, f"balance.{adate}.rou_{c}") for c in ("cars", "other")))',
      [CS + "test_the_core_stands_on_the_anchor_facts", CELL[0]]),
    M("415-F_src", "факт без источника принимается", "model/facts.py",
      'if not (isinstance(node, dict) and "v" in node and has_source(node)):',
      'if not (isinstance(node, dict) and "v" in node):',
      ["tests/test_journal.py::test_need_refuses_a_number_without_source"]),
    M("415-B_curve", "кривая мира: между узлами — левый узел, без интерполяции", "model/book.py",
      "            return z0 + (z1 - z0) * (tenor - t0) / (t1 - t0)", "            return z0",
      [CELL[0]]),
    M("415-B_half", "траектория: ключ II полугодия не читается", "model/book.py",
      "    if p in spec:\n        return float(spec[p])",
      "    if p in spec and p.endswith(\"H1\"):\n        return float(spec[p])",
      [CELL[0]]),
    # 419: мёртвая `trajectory` с памятью траекторий убрана вместе с порчей 415-B_memo; на её месте — порча правила
    # «LT без LT_from — сразу после последнего года». Сход к LT ловит тест на траектории книги с LT_from 2033.
    M("419-B_lt", "траектория: LT без LT_from — на год позже", "model/book.py",
      "lt_from = int(spec.get(\"LT_from\", last_year + 1))", "lt_from = int(spec.get(\"LT_from\", last_year + 2))",
      [B14 + "test_path_value_converges_to_lt_by_lt_from", CELL[0]]),
    M("415-B_path", "траектория: сход к LT на год раньше", "model/book.py",
      "return last_value + (float(lt) - last_value) * (year - last_year) / (lt_from - last_year)",
      "return last_value + (float(lt) - last_value) * (year - last_year + 1) / (lt_from - last_year)",
      [B14 + "test_path_value_converges_to_lt_by_lt_from"]),
    # ---------------------------------------------------------------- 415: общий слой семейства common/
    M("415-H_report", "шапка: «отчёт вышел» не жёлтый", "common/header.py",
      '    if report.get("state") == "report_out":', '    if report.get("state") == "overdue":',
      ["common/tests/test_header.py::test_report_out_is_yellow"]),
    M("415-H_soon", "шапка: близкий срок не жёлтый", "common/header.py",
      "        if left < SOON_DAYS:", "        if left < 0:",
      ["common/tests/test_header.py::test_the_status_rule_of_the_layer"]),
    M("415-CAL_end", "календарь: конец декабрьского периода — год не переходит", "common/calendar.py",
      "    nxt = dt.date(y + (month == 12), month % 12 + 1, 1)", "    nxt = dt.date(y, month % 12 + 1, 1)",
      ["common/tests/test_calendar.py::test_periods"]),
    M("415-CAL_state", "календарь: состояние отчёта по периоду якоря, а не следующему", "common/calendar.py",
      "    period = next_period(anchor_period)", "    period = anchor_period",
      ["common/tests/test_calendar.py::test_report_state_by_the_calendar",
       "tests/test_common_yandex.py::test_the_closing_report_of_the_open_half_and_the_report_state"], nth=0),
    M("415-REP_mech", "приём отчёта: механика от «до», а не от переката", "common/report.py",
      '"mechanics": e - r', '"mechanics": e - b',
      ["common/tests/test_report.py::test_on_the_expected_path_the_reanchor_is_the_rollover"]),
    M("415-REP_surp", "приём отчёта: сюрприз от переката, а не от ожидаемого", "common/report.py",
      '"surprise": a - e', '"surprise": a - r',
      ["tests/test_common_yandex.py::test_report_surprise_is_counted_from_the_expected_path_not_from_the_rollover"]),
    # ---------------------------------------------------------------- проверка раннера
    M("415-Z_idle", "холостая порча: комментарий ядра (обязана пройти)", "model/core.py",
      "# ---------------- APV (§5)", "# ---------------- APV (§5) — холостая порча",
      [CS + "test_the_flow_is_the_sum_of_its_lines"], idle=True),
]


# ------------------------------------------------------------------------------------------------ правка строки

def _apply(source: str, m: Mutation) -> str:
    """Текст файла с порчей; AssertionError — строку-цель надо перенацелить."""
    find, repl = m.find, m.replace
    if find not in source and "\r\n" in source:
        find, repl = find.replace("\n", "\r\n"), repl.replace("\n", "\r\n")
    n = source.count(find)
    if m.nth is None:
        if n != 1:
            raise AssertionError(f"{m.id}: строка-цель в {m.path} встречается {n} раз (нужна одна) — перенацелить")
        return source.replace(find, repl, 1)
    if n <= m.nth:
        raise AssertionError(f"{m.id}: вхождения №{m.nth} строки-цели в {m.path} нет (их {n}) — перенацелить")
    i = -1
    for _ in range(m.nth + 1):
        i = source.index(find, i + 1)
    return source[:i] + repl + source[i + len(find):]


def check_targets(muts: list[Mutation], root: Path = ROOT) -> list[str]:
    bad = []
    for m in muts:
        try:
            _apply((root / m.path).read_text(encoding="utf-8"), m)
        except (AssertionError, OSError) as exc:
            bad.append(str(exc))
    return bad


# ------------------------------------------------------------------------------------------------ рабочие копии

def _git(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def make_worker(base: Path, i: int) -> Path:
    """Копия рабочего дерева с историей: `git clone --shared` (объекты — из этой копии, только чтение) и поверх —
    файлы рабочего дерева (отслеживаемые и неигнорируемые новые), индекс = HEAD."""
    dest = base / f"w{i}"
    _git("clone", "-q", "--shared", "--no-checkout", str(ROOT), str(dest))
    files = [f for f in _git("ls-files", "-co", "--exclude-standard", "-z", cwd=ROOT).split("\0") if f]
    for f in files:
        src = ROOT / f
        if src.is_file():
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f)
    _git("reset", "-q", cwd=dest)
    return dest


# ------------------------------------------------------------------------------------------------ прогон

@dataclass
class Outcome:
    id: str
    status: str            # caught | missed | broken
    seconds: float
    failed: str = ""       # первый упавший тест и причина
    code: int | None = None


def _env() -> dict:
    env = dict(os.environ)
    for k in ("CONTROL_CORE_ROOT", "PYTEST_ADDOPTS", "PYTEST_DEBUG_TEMPROOT"):
        env.pop(k, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


def run_checks(worker: Path, checks: tuple[str, ...], basetemp: Path, timeout: float) -> tuple[int | None, str, float]:
    """pytest -x по узлам в копии. → (код выхода или None при истечении времени, первая строка FAILED/ERROR, с)."""
    if basetemp.exists():
        shutil.rmtree(basetemp, ignore_errors=True)
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-rfE", "--no-header", "-p", "no:cacheprovider",
           "--basetemp", str(basetemp), *checks]
    t0 = time.monotonic()
    try:
        out = subprocess.run(cmd, cwd=worker, env=_env(), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"время вышло ({timeout:.0f} с)", time.monotonic() - t0
    dt_ = time.monotonic() - t0
    first = next((ln for ln in out.stdout.splitlines() if ln.startswith(("FAILED ", "ERROR "))), "")
    if not first and out.returncode not in (0, 1):
        first = (out.stdout.strip().splitlines() or out.stderr.strip().splitlines() or [""])[-1]
    return out.returncode, first[:300], dt_


def run_one(m: Mutation, worker: Path, timeout: float) -> Outcome:
    path = worker / m.path
    raw = path.read_bytes()
    try:
        source = raw.decode("utf-8")
        try:
            mutated = _apply(source, m)
        except AssertionError as exc:
            return Outcome(m.id, "broken", 0.0, str(exc))
        path.write_bytes(mutated.encode("utf-8"))
        code, first, secs = run_checks(worker, m.checks, worker.parent / f"bt-{worker.name}", timeout)
    finally:
        path.write_bytes(raw)
    if code is None:
        return Outcome(m.id, "caught", secs, first, None)          # зависание роняет такт так же, как падение
    if code in (1, 2):                                              # 1 — тест упал; 2 — сбор упал под порчей
        return Outcome(m.id, "caught", secs, first, code)
    if code == 0:
        return Outcome(m.id, "missed", secs, "", 0)
    return Outcome(m.id, "broken", secs, f"код pytest {code}: {first}", code)  # 4/5 — узел не найден / пусто


def verdict(m: Mutation, o: Outcome) -> tuple[str, bool]:
    """Слово отчёта и «как задумано»."""
    if o.status == "broken":
        return "СЛОМАНА", False
    expect_pass = m.idle or bool(m.uncaught)
    if o.status == "caught":
        return ("поймана" if not expect_pass else "поймана — снять отметку «не ловится»" if m.uncaught
                else "ПОЙМАНА холостая — раннер ошибается"), not expect_pass
    return ("не поймана" if not expect_pass else "не поймана (не ловится, отмечено)" if m.uncaught
            else "не поймана (холостая — верно)"), expect_pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.mutations", description="мутационный набор модели Яндекса")
    ap.add_argument("--only", default="", help="порчи через запятую: точный id или начало id группы (403-, 415-C_)")
    ap.add_argument("--jobs", type=int, default=1, help="рабочих копий параллельно (по умолчанию 1)")
    ap.add_argument("--timeout", type=float, default=1200.0, help="предел одной порчи, с")
    ap.add_argument("--no-baseline", action="store_true", help="без чистого прогона проверок")
    ap.add_argument("--list", action="store_true", help="порчи и проверки без прогона")
    ap.add_argument("--targets", action="store_true", help="только найти строки-цели")
    ap.add_argument("--json", default="", help="записать итог в файл JSON")
    ap.add_argument("--keep", action="store_true", help="не удалять рабочие копии")
    a = ap.parse_args(argv)

    muts = MUTATIONS
    if a.only:
        keys = [k.strip() for k in a.only.split(",") if k.strip()]
        exact = {m.id for m in MUTATIONS}                # точный id — только он; иначе — начало id (группа)
        muts = [m for m in MUTATIONS if any(m.id == k if k in exact else m.id.startswith(k) for k in keys)]
        if not muts:
            print(f"нет порч по выбору {a.only}")
            return 2
    ids = [m.id for m in MUTATIONS]
    assert len(ids) == len(set(ids)), "повтор id порчи"

    if a.list:
        for m in muts:
            mark = " [холостая]" if m.idle else " [не ловится]" if m.uncaught else ""
            print(f"{m.id}{mark}  {m.path}: {m.title}")
            for c in m.checks:
                print(f"    {c}")
        print(f"порч: {len(muts)}")
        return 0
    bad = check_targets(muts)
    if bad:
        print("строки-цели не найдены:\n  " + "\n  ".join(bad))
        return 2
    if a.targets:
        print(f"строки-цели на месте: {len(muts)} из {len(muts)}")
        return 0

    t_all = time.monotonic()
    base = Path(tempfile.mkdtemp(prefix="ydex-mut-"))
    print(f"рабочие копии: {base}", flush=True)
    try:
        jobs = max(1, min(a.jobs, len(muts)))
        workers = [make_worker(base, i) for i in range(jobs)]
        if not a.no_baseline:
            union = tuple(dict.fromkeys(c for m in muts for c in m.checks))
            code, first, secs = run_checks(workers[0], union, base / "bt-base", a.timeout * 2)
            print(f"чистый прогон проверок ({len(union)} узлов): код {code}, {secs:.0f} с", flush=True)
            if code != 0:
                print(f"  чистый код не проходит свои проверки: {first}")
                return 2
        free = list(workers)
        lock = threading.Lock()
        results: dict[str, Outcome] = {}

        def task(m: Mutation) -> Outcome:
            with lock:
                w = free.pop()
            try:
                o = run_one(m, w, a.timeout)
            finally:
                with lock:
                    free.append(w)
            word, ok = verdict(m, o)
            with lock:
                results[m.id] = o
                tail = f" — {o.failed}" if o.failed else ""
                print(f"{m.id:<18} {word:<12} {o.seconds:6.0f} с{tail}", flush=True)
            return o

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(task, muts))
    finally:
        if not a.keep:
            shutil.rmtree(base, ignore_errors=True)

    real = [m for m in muts if not m.idle]
    caught = [m for m in real if results[m.id].status == "caught"]
    missed = [m for m in real if results[m.id].status == "missed"]
    broken = [m for m in muts if results[m.id].status == "broken"]
    wrong = [m for m in muts if not verdict(m, results[m.id])[1]]
    by_mod: dict[str, list[int]] = {}
    for m in real:
        k = m.module or m.path
        by_mod.setdefault(k, [0, 0])
        by_mod[k][0] += 1
        by_mod[k][1] += results[m.id].status == "caught"
    print("\nпо модулям (поймано / порч):")
    for k, (n, c) in sorted(by_mod.items()):
        print(f"  {k:<36} {c} / {n}")
    print(f"\nитог: порч {len(real)}, поймано {len(caught)}, не поймано {len(missed)}"
          + (f" (из них отмечены «не ловится»: {sum(bool(m.uncaught) for m in missed)})" if missed else "")
          + (f", сломано {len(broken)}" if broken else "")
          + f"; не как задумано: {len(wrong)}; время {time.monotonic() - t_all:.0f} с")
    for m in missed:
        print(f"  не поймана {m.id}: {m.path} — {m.title}" + (f"; {m.uncaught}" if m.uncaught else ""))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(
            {m.id: {"status": results[m.id].status, "as_designed": verdict(m, results[m.id])[1],
                    "seconds": round(results[m.id].seconds, 1), "failed": results[m.id].failed, "path": m.path,
                    "title": m.title, "checks": list(m.checks), "uncaught": m.uncaught, "idle": m.idle}
             for m in muts}, ensure_ascii=False, indent=1), encoding="utf-8")
    if broken:
        return 2
    return 0 if not wrong else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
