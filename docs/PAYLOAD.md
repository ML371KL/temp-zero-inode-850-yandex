# Выпуск YDEX: контракт `yandex-v1`

Выпуск — один JSON (`/api/model`), из которого витрина берёт ВСЕ числа. Единственный
пересчёт во фронте — ползунок λ по `fair_value.draws_low/draws_high`. Новое число для
экрана сначала добавляется сюда и в `model/payload.py`, потом во фронт. Потолок —
500 000 байт компактного JSON; ни одного NaN/Inf. Деньги — млрд ₽, цены — ₽, доли —
доли единицы (витрина сама переводит в проценты). Даты — ISO `YYYY-MM-DD`, время —
ISO UTC. Контракт — на базе `x5-v1`: те же блоки и поля, которые читают хаб и сводка
состояния; содержание — сервисы Яндекса (блоки устава A–G и исключения X вместо площадей,
форматов и магазинов образца).

Обязательные блоки верхнего уровня (`REQUIRED_TOP_LEVEL`): `schema, meta, market,
headline, fair_value, layers, grid, worlds, regimes, capex_levels, paths, debt,
dividends, history, reverse_dcf, judgements, uncertainty, next_report, journal,
calendar, checks, inputs, live, changes, book, indicators`. Витрина читает только
объявленные блоки (тест `test_frontend_reads_only_declared_blocks`).

**Контроль** — `model/payload.py::validate` в сборке и ещё раз над записанным файлом
(`ops/build_release.py --check`): блоки, конечность чисел, размер, хэш, печать = округлению,
защита заголовка — и производные против того, из чего они посчитаны (`derived_problems`):
заголовок, P ниже рынка и таблица `by_lambda` — по прогонам выпуска; точка — на отрезке
низ–верх; V0 слоя = сумма PV-строк = Σ p·EV клеток; капитал = V0 − D; цена слоя из капитала
(§7.2); мост и `market.claims` — слой «свой взгляд»; пара «модель — рынок»; тождества сетки
`checks.invariants`; доли осей `uncertainty.contributions` (из `rank_corr`, сумма 1).

**Поля, которые читают хаб и сводка** (обзор общих мест, раздел 2) — на местах образца:
`schema`; `meta.{generated_at, valuation_date, facts_date, book_version, anchor_period,
open_period, payload_sha256, engine_commit}`; `headline.{central, printed_central, band,
printed_band, inner, p_central_below_market, market_price}`; `market.{price, price_date,
price_status}`; `book.version`; `checks.flags[].raised`, `checks.gates[].fired`;
`live.degraded`; `calendar.events`, `next_report.closing`; `fair_value.by_world` +
`worlds.order` (миры в хабе). Хаб и сводка переходят на шапку `header` отдельной правкой, с запасным путём на
эти поля.

Необязательный блок (`OPTIONAL_TOP_LEVEL`): `header` — шапка семейства `family-header-v1` (`common/README.md`,
раздел 2). Её ставит сборка `ops/build_release.py` после сборки ядра, до проверки контракта (контракт ядра и схема шапки
проверяются на выпуске с шапкой), — в корень сразу за `schema`, из
`header_inputs` переходника `ops/common_adapter.py` и календаря `data/calendar.json`; затем `model.payload.seal`
пересчитывает `meta.payload_sha256` и `meta.bytes` по выпуску с шапкой. Прежние блоки и числа шапка не меняет;
`header.release.generated_at` в хэш не входит (как `meta.generated_at`). Шапку проверяет `common.header.validate`
при сборке и в `--check`.

## schema
`"yandex-v1"`.

## meta
`generated_at`, `valuation_date`, `facts_date`, `book_version`, `book_date`, `engine_commit`,
`basis` (скорр. EBITDA компании после аренды офисов), `shares_mln` (делитель N_eff, §7.2),
`shares_outstanding_mln`, `governance_discount`, `anchor_period`, `first_period`,
`last_period`, `open_period` (первое прогнозное без факта), `curve_as_of`, `closed_periods`,
`elapsed`, `worlds_version`, `fast` (быстрая сборка: малая полоса — не выпуск), `draws`,
`payload_sha256`, `bytes`, `previous_sha256` (или null).
**Хэш** — sha256 канонического JSON без `meta.generated_at`, `meta.payload_sha256`,
`meta.bytes`, `meta.previous_sha256`, `live.fetched_at`, блока `changes`,
`journal.entries[].release_sha` и `journal.releases`. Числа выпуска — 9 значащих цифр.

## market
`price`, `price_date`, `price_time`, `price_source` (словами), `price_status` ("live" |
"fallback" | "book"), `book_price`, `shares_outstanding_mln`, `market_cap` (по акциям в
обращении), `claims` (D слоя «свой взгляд»), `market_ev` (= V\*), `equity_share_of_ev`
((V\* − D) / V\*), `ebitda_2027` (ожидаемая EBITDA 2027 г. слоя «свой взгляд»),
`ev_ebitda_2027` (V\* / `ebitda_2027`), `peers`, `brokers` (null — в первой версии нет),
`price_history` [{`date`, `close`}] (≤ 130 точек), `price_min`, `price_max`.

## headline
`central` (медиана, до копейки), `printed_central`, `band` [P10, P90], `printed_band`,
`inner` [P25, P75], `printed_inner`, `mean`, `p_central_below_market`, `market_price`,
`print_step`, `draws`, `lambda`. Статистики — по прогонам выпуска, округлённым до 0,1 ₽
(так же их пересчитает фронт); печать — к шагу, половина вверх.

## fair_value
* `low`, `central`, `high` (точка: низ «рыночные ставки как есть», точка при λ книги, верх
  «свой макро-взгляд»), `printed` {low, central, high}, `lambda`, `lambda_step` (0,05),
  `rates_view` {`rub`: верх − низ}.
* `by_lambda`: [{`lambda`, `point`, `median`, `p10`, `p25`, `p75`, `p90`, `mean`,
  `p_below`}] — 21 строка λ = 0, 0,05, …, 1; сверка ползунка с прогонами.
* `draws_low`, `draws_high`: массивы длины `headline.draws` (₽, до 0,1).
* `by_world` {N, H, M}: цена слоя «только этот мир».
* `center_ev`: {`v0_median`, `v0_point`, `v_star`, `gap_median`, `gap_point`,
  `rub_per_1pct_ev_median`, `rub_per_1pct_ev_point`, `ebitda_2027`, `ev_ebitda_2027_median`,
  `ev_ebitda_2027_point`, `ev_ebitda_2027_market`} — пара «модель — рынок» на одной базе
  `ebitda_2027`: та же EBITDA 2027 г., что в знаменателе EV/EBITDA точки и в гейтах
  `ev_ebitda_2027` и `broker_ebitda_2027` (§8, §13.2). Книга с `joint.expectation: headline`
  (1.4) — ожидание на весах заголовка λ × E_свой + (1 − λ) × E_нейтр; без ключа (книги
  1.0–1.3) — слой «свой взгляд». `market.ebitda_2027` — всегда слой «свой взгляд».
* `equity_share_of_ev` (капитал / V0 точки).

## layers
`analytical`, `market_implied`, `macro_neutral` — у каждого: `title`, `world_weights`, `v0`,
`d`, `equity`, `price`, `pv_explicit`, `pv_shield`, `pv_tv`, `pv_tv_shield` (`v0` = сумма
четырёх, §5), `terminal_share` ((PV терминала + PV его щита) / V0), `ebitda_2027`,
`ev_ebitda_2027`, `v0_to_d`.

## grid
`cells`: 36 × {`world`, `regime`, `capex`, `p_analytical`, `p_market_implied`, `p_neutral`,
`ev`, `d`, `equity`, `price`, `c_T`, `terminal_share`, `ebitda_2027`, `ev_ebitda_2027`,
`margin_2030` (маржа группы 2030 г.)}; `world_order`, `regime_order` (stress, slow, base,
strong), `capex_order` (low, base, high).

## worlds
`order`, `source` (запись миров книги: `version`, `book`, `sha256`, `record_asof`,
`curve_date`); по N, H, M: `name`, `weights` {analytical, market_implied, macro_neutral},
`key_rate`, `cpi` [{`period`, `value`}] (полугодия книги), `zero_curve` {1, 3, 5, 10, LT},
`lt_inflation`, `r_terminal`, `g_terminal` (клетка base/base мира), `price` (слой «только
этот мир»), `v0`.

## regimes
`order`, `titles` («Стресс», «Медленный», «Базовый», «Сильный»), `prob`, `shift_to`;
`blocks` {A…G}: {`title`, `margin_center`, `margin_range`, `margin_targets` {режим},
`target_year`, `growth_shift` {режим} (или null), `shift_range`, `converge_year`}; `note`.

## capex_levels
`order`, `titles`, `levels` {low, base, high} (доли выручки 2027–2030; null по правилу κ), `prob`, `levels_to`,
`converge_year` (null по правилу κ), `lease_share`, `asset_life_years`, `rule` (`kappa` или null). По правилу κ
(MODEL.md §4.9) ещё `kappa_levels` {low, base, high} (капиталоёмкость на конец `levels_to`), `kappa_base_capex`,
`kappa_high_capex`, `first_share`, `floor_da`, книга 1.2 — `balance_base` (`ex_fs`: κ и доли вложений уровней — к
выручке без финсервисов; null — к выручке группы), `kappa_window` (`ltm` | null), `kappa_high` (`mirror` | null); `prob_given_regime` {режим: {уровень}} — с условностью по режиму (MODEL.md §3), иначе null. Книга 1.4 (ключ
`meta.release_rules`, MODEL.md §14) — ещё `rules`: {`season` (`h1_dip` | null), `h1_dip` (δ), `h1_share` (доля I
полугодия во вложениях), `anchor_rest` (`cohort` | null), `anchor_rest_bn` (остаток капитала якоря, млрд), `anchor_land_bn`
(право пользования землёй вне остатка), `land_da_half` (его списание за полугодие), `kappa_target` (`all_cells` | null),
`base_cell_capex` (D_b — доля клетки базового уровня), `expectation` (`headline` | null), `world_weights` {мир: вес
вывода κ}}; без ключа поля `rules` нет.

## paths
Ожидаемый путь слоя «свой взгляд» (Σ вероятность клетки × строка; маржа — отношение средних).
`layer`, `block_order`, `block_titles`, `note`;
`annual`: [{`year`, `revenue`, `ebitda`, `attack`, `sbc`, `da`, `ebit`, `tax`, `capex`,
`lease_new`, `acq`, `dwc`, `dkb`, `fcff`, `nopat`, `interest`, `shield` (потоки — суммы
полугодий), `nwc`, `fa` (на конец года), `margin`, `capex_pct` ((капвложения + новые договоры
аренды) / выручка), `revenue_growth`, `blocks` {A…G, X: {revenue, ebitda}}, `fact` (строки
года, где есть факт: год якоря — факт 1П + прогноз 2П в выручке и EBITDA), `forecast_periods`}];
`halves`: [{`period`, `revenue`, `ebitda`, `margin`, `fcff`, `capex`, `blocks`}].

## debt
* `anchor`: {`as_of`, `net_debt_nonbank`, `net_debt_company`, `bank_money`}.
* `bridge`: {`ev_rows` [{`key`, `label`, `amount`}] — из чего складывается V0 слоя «свой
  взгляд» (сумма = `v0`); `v0`; `rows_at_valuation` [{`key`, `label`, `amount`}] — D на дату
  оценки: чистый долг без банка, строки моста (активы — со знаком минус; книга 1.4 — налог к уплате,
  авансы по нему и налог нетто-исполнения подписаны словами), объявленные дивиденды, перекат (сумма = `total`); `total`; `equity` (= `v0` − `total`); `equity_rows`
  ([] — казначейского пакета нет); `treasury_mln` (0); `shares_mln` (N_eff)}.
* `bonds` [], `bank_loans` null, `wall` [], `wall_note` null — реестра облигаций в первой
  версии нет.

## dividends
`register` [{`label`, `amount`, `record_date`, `paid`, `in_claims`}] — объявленные
дивиденды фактов; `policy`, `next_expected`, `yield_ltm` — null; `history`, `model` — [];
`note` (путь дивидендов модель не считает: поток — до финансирования).

## history
Факты окна по блокам: `halves` [{`period`, `revenue`, `ebitda`, `margin`, `blocks`
{A…G, X: {revenue, ebitda}}}], `annual` [то же + `year`], `block_order`.

## reverse_dcf
`rows`: [{`key`, `name`, `unit`, `kind`, `paths`, `book`, `solved`, `delta`, `in_range`,
`range`, `search`, `status` ("solved" | "unreachable" | "skipped" — быстрая сборка),
`search_value`, `gap_full`, `point_solved`, `point_status`, `provisional`, `fixed_axes`,
`evaluations`, `evaluations_full`}]; `target` (рыночная цена); `provisional` (состав осей не из книги —
временный); `method` (словами, §11).

## judgements
`rows`: [{`id`, `name`, `unit`, `book`, `low`, `high`, `price_low`, `price_high`, `swing`,
`share`, `kind`, `paths`, `in_core`}] — «суждения по цене ошибки» (точка на краях оси, §9)
и вклад в полосу; по убыванию `swing`, оси вне ядра — в конце с null.

## uncertainty
`contributions` [{`axis`, `key`, `share`, `rank_corr`}] (сумма 1), `draws`, `seed`,
`axes_count` (оси в полосе), `axes` [{`key`, `name`, `kind`, `paths`, `unit`, `book`, `low`,
`high`, `in_core`}] (все оси §9), `skipped_axes` (названия осей, которые ядро не считает),
`axis_prices` [{`key`, `name`, `price`, `median_without`}] (цена оси — медиана с осью минус
медиана с осью на значении книги, на тех же случайных числах; null в быстрой сборке), `mean`,
`histogram_bins`, `subsample`, `delta`.

## next_report
`period` (открытое полугодие), `events` (календарь от даты оценки), `closing` ({`date`,
`title`, `confirmed`, `published`} или null — событие `kind: report` с `covers` = `period`), `expectation`
({`period`, `revenue`, `ebitda`, `margin`, `revenue_growth`, `blocks`} слоя «свой взгляд»),
`guidance` null, `benchmarks` [], `table` [] («что даст отчёт» в первой версии нет, §12),
`note`. Календарь — `data/calendar.json` в формате семейства `family-calendar-v1` (`common.calendar`).

## journal
`entries` (записи прошлых выпусков переносятся как есть; новых в первой версии нет),
`rule`, `status`, `releases` {`release_sha`: {`book_version`, `generated_at`}}.

## calendar
`events`: события `data/calendar.json` (`family-calendar-v1`: `id`, `kind`, `date`, `precision`, `earliest`,
`latest`, `confirmed`, `covers`, `gives`, `title`, `src`, `note`) от даты оценки.

## checks
Считает `model/checks.py` (§13); таблица готового выпуска — `python -m model.checks`.
`invariants`: [{`name`, `title`, `ok`, `detail`}] — §13.1: тождества сетки (вероятности, группа =
Σ блоков, поток, EV, требования, конечность), `regime_center` (среднее режимов = центру; допуск —
шаг округления перевывода 1e-10), `facts_window` (A–G + исключения = группа якоря); `gates`:
[{`name`, `title`, `fired`, `mass`, `value`, `low`, `high`, `corridor`, `unit`, `cells`, `detail`,
`explanation`, `valid_until`, `expected_mass`, `status`, `blocking`}] — двенадцать гейтов §13.2 в
порядке `checks.GATES`; `value` — слой «свой взгляд» или точка, `low`/`high` — по клеткам; `status`
— `ok` | `explained` | `unexplained` | `expired` | `mass_outside` | `not_computed` (нечем
посчитать: нет коридора в книге или факта — причина в `detail`, сборку не роняет) | `flag` (сработали
только клетки с вероятностью 0 и объяснения нет — печать, сборку не роняет, §13.2); `traps`:
[{`name`, `title`, `ok`, `detail`}] — ловушки §13.3 (`sbc_once`, `bank_not_in_debt`, `one_lease`,
`one_base`, `worsening`); `magnitudes`: [{`name`, `title`, `value`, `reference`, `detail`}] —
§13.4; `forks`: [{`name`, `title`, `basis`, `variants`: [{`label`, `value`, `price`}], `status`,
`detail`}] — развилки §13.5, `price` — сдвиг медианы (на тех же случайных числах, что полоса
выпуска) или точки, ₽; `flags`: [{`name`, `title`, `raised`, `status` (`ok` | `not_connected`),
`detail`}] — `book_update`, `dividend_register`, `report_fact`, `price_fallback`, `stale_release`;
`note`. Нарушенный инвариант или ловушка, сработавший гейт без годного объяснения — сборка не
выходит; `validate` ловит их и в готовом файле.

## inputs
`rows`: [{`name`, `value`, `unit`, `as_of`, `source`, `status` ("ok" | "stale" |
"fallback")}] — цена, кривая, ключевая, книга, факты.

## live
`price` {value, date, time, source, status, accepted, reason, kind, last_accepted}, `curve`
{as_of, nodes, book_nodes, shift_bp {5, 10}} или null, `key_rate` {value, date, since} или
null, `valuation_date`, `fetched_at`, `errors`, `degraded`.

## changes
`vs_previous`: {`previous_sha`, `previous_generated_at`, `rows` [{`component`, `title`,
`rub`}], `total_rub`, `reference` (опорные числа прошлого выпуска для защиты заголовка),
`note`} — разложение изменения точки: книга, факты и код (остатком), цена рынка (в точку
ядра не входит — 0), дата оценки (перекат).

## book
`version`, `date`, `tag`, `sections`: [{id, title}], `worlds_source`, `facts_date`,
`key_judgements`: [{id, name, value, unit, path}].

## indicators
`tiles`: [{`id`, `title`, `unit`, `value`, `date`, `change`, `change_from`, `since`, `min`,
`max`, `history`}] — цена YDEX, ключевая ставка, ОФЗ 5 и 10 лет (из живых входов; на
входах книги — пусто).
