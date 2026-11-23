# Ядро модели YDEX: как звать из кода и из командной строки

Методика — `docs/MODEL.md` (авторитетна); здесь — только как её звать. Единицы: деньги — млрд ₽, акции — млн шт.,
ставки и доли — доли единицы (годовые), цена — ₽/акция. В коде ядра нет чисел книги и фактов
(`tests/test_core_services.py::test_no_book_numbers_in_core_code`).

Свои модули Яндекса (задача 5а): `book.py`, `book_schema.py`, `facts.py`, `rederive.py`, `core.py`, `cell.py`,
`point.py`. Общие модули сектора на ядре сервисов (задача 5б): `grid.py` (сетка 36 клеток, три слоя, точка — бит в
бит как `point.py`, ожидаемый путь по блокам, тождества сетки), `uncertainty.py` (оси полосы §9, двукусочное
нормальное, гиперкуб, пул процессов, суждения по цене ошибки, цены осей, обратный DCF §11), `quick.py` (быстрые
числа), `book_results.py` (полная полоса и таблицы книги), `payload.py` (выпуск `yandex-v1`, `docs/PAYLOAD.md`),
`attribution.py` («что изменилось»), `sample_release.py`. `checks.py` (задача 7) — проверки §13 на ядре сервисов:
инварианты, гейты с объяснениями (`data/assumptions/gate_explanations.yaml`), ловушки и «ухудшение не повышает цену»,
порядок величин, развилки с ценой, флаги — блок `checks` выпуска. `journal.py` — пока образец X5 (перенос записей).

**Ось 6 полосы (расход автономных технологий, вид `path`)** — ключ книги `f.at.position` (книга — 0): ядро считает
путь `центр + |x| × (край − центр)`, края — `f.at.perpetual_share` (низ) и `f.at.high_zero_from` (верх) (§4.5, §9).
Книга без ключа — прежняя механика (ось не прикладывается, `uncertainty.skipped_axes`). Настройки полосы и обратного
DCF — в книге: `valuation.uncertainty` (прогоны полной и малой полосы, семя, оси) и `valuation.reverse_dcf`
(подвыборка, окно поиска, оси).

## Входы

**Книга** — `data/assumptions/assumptions.yaml`, `model.book.load_book(path=None)`: YAML + закрытая схема
`model/book_schema.py::validate_book` (незнакомый ключ, пропуск, не тот тип, среднее режимов ≠ центру — `BookError`).
Суждения — числа устава с источником в комментарии; выведенные ключи §17 пишет перевывод, руками не правятся.
`override(A, paths, kind, value)` — копия книги с подменой (`value`, `shift`, `dict`); траектории — `path_value`.

**Факты** — `data/facts/*.json`, `model.facts.load_facts()`; `core_facts(F, anchor)` → `CoreFacts`: ряды блоков
A–G, частей F, исключений X и группы (кварталы, полугодия, годы), GTV B и C, выплаты акциями, капвложения, поступления
права пользования машин, земли и прочего, амортизация без офисов, «разница в ставках», проценты, ИПЦ, ключевая,
реклама рынка, баланс якоря (чистый долг без банка, деньги банка, капитал банка, оборотный без банка, ОС + НМА + право
пользования без помещений, активы моста), акции и опционы, объявленные дивиденды. Число без источника — `FactsError`.

## Команды

| Команда | Что делает |
|---|---|
| `python -m common.cli rederive [--dry-run] [--table]` (= `python -m model.rederive`) | выведенные ключи §17 из фактов, рядов β (`data/market/beta_inputs.json`) и суждений: строки «изменено / было бы: ключ: было → стало» (после перевывода `--dry-run` пуст), `ФЛАГ:` (сверка амортизации) и `ИТОГ:`; `--table` — вся таблица «ключ — значение — из чего»; без `--dry-run` пишет книгу точечно (комментарии целы) и проверяет её схемой |
| `python -B ops/tools/build_market.py [--src …] [--check]` | ряды β из выгрузок ISS (первичка вне репозитория, `--src`; sha256 — в выходе) и реестра `data/market/dividends.csv` → `data/market/beta_inputs.json` (sha256 входов) |
| `python -m model.cell --world … --regime … --capex … [--book …] [--asof …] --json` | одна клетка — раздел «Интерфейс клетки» ниже |
| `python -m model.point [--book …] [--asof …] [--json файл]` | точка по сетке 36 клеток (§8): точка, цены слоёв, V0 и требования, V0 по мирам, EV/EBITDA 2027 г., доля терминала, делитель; `ИТОГ:` |
| `python -m common.cli quick [--draws N] [--workers K] [--axes]` (= `python -m model.quick`) | быстрые числа: сетка и малая полоса (`valuation.uncertainty.small_draws` прогонов книги, 2 процесса, семя `valuation.uncertainty.seed`): медиана (заголовок) и точная, полоса P10–P90 и P25–P75, среднее, P(ниже рынка), точка (низ, верх), вклад осей, время и оценка времени полной полосы; `--axes` — ещё суждения по цене ошибки и цены осей; `ИТОГ:` |
| `python -m common.cli band [--draws N] [--workers K] [--out каталог]` (= `python -m model.book_results`) | полная полоса (2 000 прогонов), суждения, цены осей, обратный DCF → `data/assumptions/results.json` и `run_output.txt` (`--out` — другой каталог); `ИТОГ:` |
| `python -m model.checks [--release ФАЙЛ]` | таблица проверок §13 готового выпуска (по умолчанию `var/release/latest.json`): инварианты, гейты (значение, клетки, коридор, масса, статус), ловушки, порядок величин, развилки с ценой, флаги; `ИТОГ:`, код 1 — есть блокирующие |
| `python -m common.cli server-tests [--groups K]` (= `python -m ops.server_tests`) | тесты такта (метка `tact`, белый список `tests/conftest.py: TACT_FILES`, отбор `TACT_TESTS` из `ops/run.sh`) в K процессах pytest (4): группы, счётчики против ожидаемых `EXPECTED`, время против потолка 180 с; `ИТОГ:` |
| `python -m common.cli release [--fast] [--live-file F] […]` (= `python ops/build_release.py`) | выпуск `yandex-v1` в `var/release/latest.json`; без источника входов — `--book`; `--fast` — малая полоса, обратный DCF только для точки (не выпуск); последняя строка — «готово: …» с медианой, полосой и точкой |

В коде: `ctx = core.Context(A, cf, valuation_date=None)`; `core.run_cell(ctx, world, regime, capex) -> CellResult`;
`point.evaluate(A, cf, asof=None) -> dict`; `grid.evaluate(A, cf, valuation_date=None, market_price=None) -> Grid`;
`uncertainty.band(A, cf, draws=2000, n_workers=None) -> Band`; `payload.build_payload(live=None, previous=None,
fast=False) -> dict`. Процессов полосы — `YANDEX_WORKERS` (по умолчанию ядра машины, не больше 16); числа от него не
зависят.

## Интерфейс клетки

Второй счёт читает только этот раздел.

```
python -m model.cell --world <N|H|M> --regime <stress|slow|base|strong> --capex <low|base|high>
                     [--book <путь книги>] [--asof <ГГГГ-ММ-ДД>] --json
```

* Запуск — из корня дерева, Python venv модели (3.12). `--book` — путь YAML книги (по умолчанию
  `data/assumptions/assumptions.yaml`); `--asof` — дата оценки (по умолчанию `meta.valuation_date` книги); факты —
  всегда `data/facts/`.
* Код выхода 0 — в stdout один JSON (UTF-8); 2 — клетка не посчитана, причина — строкой в stderr (например,
  `valuation.beta_u = null`: β в книге 1.0 не выведена — считать на копии книги с β).
* Числа — без округления (float); полугодовые ряды — списки длины `len(periods)` в порядке `periods`.

| Поле | Смысл (методика) | Единица |
|---|---|---|
| `world`, `regime`, `capex`, `asof`, `book_version` | клетка, дата оценки, номер книги | — |
| `periods` | прогнозные полугодия `meta.first_period` … `meta.last_period` (2026H2 … 2036H2, 21 шт.) | — |
| `blocks.<A…G>.revenue`, `.ebitda` | выручка и скорр. EBITDA блока: A–E, G — §4.2–4.4; F — выручка f_R × R (§4.1), EBITDA = 2 × E_F(2026Q3) в 2026H2, дальше E_at + E_o (§4.5) | млрд ₽ |
| `blocks.X.revenue`, `.ebitda` | исключения R_X = −x_R × (S + R_F), E_X = x_E × (S + R_F) (§4.1) | млрд ₽ |
| `group.revenue` | R группы (§4.1) = Σ блоков A–G + X | млрд ₽ |
| `group.ebitda` | E группы = Σ EBITDA блоков A–G + X + ущерб атак `group.attack` (только 2026H2) | млрд ₽ |
| `group.sbc` | выплаты акциями (§4.8) | млрд ₽ |
| `group.da` | амортизация когортами (§4.9) | млрд ₽ |
| `group.tax` | налог без рычага tax_u (§4.11) | млрд ₽ |
| `group.capex` | капвложения без аренды (K(y) − ℓ) × R (§4.9) | млрд ₽ |
| `group.lease_new` | новые договоры аренды машин, земли и прочего ℓ × R (§4.9); `capex + lease_new` = CX | млрд ₽ |
| `group.acq` | покупки компаний MA (§4.9) | млрд ₽ |
| `group.dwc` | прирост оборотного капитала без банка ΔNWC (§4.10; первое — от факта якоря) | млрд ₽ |
| `group.dkb` | прирост капитала банка ΔKb (§4.6) | млрд ₽ |
| `group.fcff` | поток FCFF = ebitda − sbc − tax − capex − lease_new − acq − dwc − dkb (§4.12) | млрд ₽ |
| `group.interest` | проценты I = ND(якорь) × (key + σ_d) / 2 (§4.13) | млрд ₽ |
| `group.shield` | щит S = t_S × I (§4.13) | млрд ₽ |
| `group.attack`, `ebit`, `nopat`, `nwc`, `fa`, `tax_rate_cash` | справочно: ущерб атак, EBIT = E − SBC − DA, NOPAT, NWC, FA, t_S | млрд ₽, доля |
| `tv` | терминальная стоимость TV на дату конца прогноза, без щита (§6.3) | млрд ₽ |
| `v0` | EV клетки = PV потока + PV щита явного участка + (TV + TV_S) × df(t_end) (§5) | млрд ₽ |
| `claims` | требования на дату оценки (§7.1): `net_debt_nonbank` (ND якоря = `net_debt_company` + `bank_money`), `bridge.<строка>` (активы — со знаком минус), `rollover` (= −перекат FCFF + S − I закрытых и текущего полугодий), `dividends` (объявленные с отсечкой ≤ даты оценки), `total` = ND + мост + rollover + dividends; `net_debt_company` и `bank_money` — справочно, в `total` не складываются | млрд ₽ |
| `shares` | делитель N_eff = N + k_net × O_v (§7.2) | млн шт. |
| `price` | цена клетки (v0 − claims.total) × (1 − g_gov) × 1000 / shares (без дисконта при капитале ≤ 0) | ₽ |
| `detail` | справочно: `tv_shield`, `pv_explicit`, `pv_shield`, `pv_tv`, `pv_tv_shield`, `c_T`, `g`, `r`, строки терминала T1, T2 (`terminal`), `warnings` | — |

Тождества, которые держит ядро (до 1e-9): Σ `blocks.<A…G, X>.revenue` = `group.revenue`; Σ EBITDA блоков +
`group.attack` = `group.ebitda`; `group.fcff` = сумма строк; `v0` = `pv_explicit + pv_shield + pv_tv + pv_tv_shield`;
`claims.total` = сумма статей.
