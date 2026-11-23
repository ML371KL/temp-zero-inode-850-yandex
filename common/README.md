# Общий слой семейства «850»

Каталог `common/` в корне дерева модели — пакет Python, побайтово одинаковый у всех моделей семейства. Внутри только
общее: ни имени эмитента, ни чисел книги, ни путей машины. Своё модели — в одном переходнике `ops/common_adapter.py`
(раздел «Переходник модели»). Когда у слоя появится свой источник, каталог переезжает туда без правок.

Версия слоя — `1.1.0`. Сделано: правила слоя, набор команд (слой делает `state`, `report`, `worlds`,
`adopt-common`), лист фактов, шапка выпуска, календарь, приём отчёта и обвязка теста «на ожидаемом пути = перекат».

## 1. Правила слоя

- Побайтово одинаков у всех моделей. `common/MANIFEST.sha256` — sha256 каждого файла слоя (кроме самого манифеста и
  кэшей Python): строки `<sha256>  <путь от common/>` по алфавиту путей, концы строк LF; `common/VERSION` — версия слоя.
  Манифест у источника слоя переписывает `python -m common.layer manifest` (`--check` — только сверить).
- Состояние у модели — `data/common_state.json`: `{version, source, source_commit, manifest_sha256}`; его пишет
  только команда приёма слоя.
- Тест-сторож лежит внутри слоя (`common/tests/test_common_guard.py`) и сам входит в манифест. Он проверяет без сети:
  sha256 манифеста = `common_state.json`, версия = состоянию, каждый файл = манифесту, лишних файлов в `common/` нет.
  Тесты слоя (`common/tests/`) модель включает в свой набор (`testpaths` в `pytest.ini`).
- Приём слоя: `python -m common.cli adopt-common --source <каталог common/ источника> [--dry-run]` — сверяет
  источник с его манифестом (источник с незакоммиченными правками слоя принимается только сухим прогоном), копирует
  новые и изменённые файлы, удаляет лишние, сверяет результат с манифестом, пишет состояние и печатает, что переходнику
  модели нужно поправить (отсутствующие имена и функции, команды без строки `COMMANDS`, строку `.gitattributes`).
  Повтор приёма того же слоя (та же версия и тот же манифест) состояние не меняет: источник и коммит — прежние.
  Сухой прогон печатает то же и ничего не пишет. Обновление у всех моделей — та же команда по моделям реестра.
- Байты защищает `.gitattributes` модели: строка `common/** -text`. Python 3.12. Тесты слоя не ходят в сеть и не
  зависят от «сегодня» (дата — параметром).
- Совместимость: выпуск модели несёт и шапку, и прежние поля, которые читает хаб; хаб и сводка состояния переходят на
  шапку отдельной правкой, с запасным путём на прежние поля.

## 2. Шапка выпуска — блок `header`, схема `family-header-v1`, модуль `common.header`

Один блок в корне выпуска; строит `common.header.build(inputs)`, проверяет `common.header.validate(header)` (схема —
`common/schemas/header-v1.json`). Хэш выпуска `meta.payload_sha256` считается после, по всему выпуску.

| Поле | Что |
|---|---|
| `schema` | `"family-header-v1"` |
| `model` | `slug` (как в реестре), `tickers[]`, `name` |
| `headline` | `printed` (печатаемая медиана), `median`, `point`, `band80 [p10, p90]`, `band50 [p25, p75]`, `print_step`, `p_below_market`; при двух бумагах — `by_ticker{тикер: {printed, median, band80}}` |
| `market` | `ticker`, `price`, `price_date`, `upside` (printed/price − 1) |
| `book` | `version`, `date`, `tag` |
| `facts` | `anchor_period`, `facts_date`, `first_period`, `open_period`, `periods_closed[]`, `first_period_closed` (bool) |
| `worlds` | `version`, `record_asof`, `record_sha256`, `engine_sha256` |
| `common` | `version`, `manifest_sha256` |
| `release` | `generated_at`, `valuation_date`, `engine_commit`, `fast` (bool) |
| `flags[]` | только поднятые советующие флаги: `name`, `level` (`info` / `warn` / `alert`), `title` |
| `gates[]` | сработавшие гейты: `key`, `explained` (bool), `valid_until`, `mass` |
| `deadlines[]` | все сроки, после которых сборка откажет или модель устареет, ближний первым: `date`, `what`, `kind` (`explanation` / `book` / `data` / `policy`), `action` |
| `report` | `state`: `current` / `report_out` (вышел, факт не внесён) / `closing_due` (ждём закрывающий отчёт) / `overdue` (срок прошёл, отчёта нет); `next{date, title, covers, precision, confirmed}`; `last_fact_period`; `out_since` |
| `status` | `level`: `green` / `yellow` / `red` и `reasons[]` — red: нарушен инвариант или истёк срок объяснения; yellow: поднят флаг `warn`/`alert`, срок ближе 30 дней или `report_out`; иначе green. Срок меняет состояние в шапке, сборку не роняет |

- Входы `build` — словарь `header_inputs(payload)` переходника: поля `model`, `headline`, `market` (без `upside`),
  `book`, `facts`, `worlds`, `release`; `flags` (в шапку идут поднятые), `gates` (сработавшие), `deadlines` модели
  (объяснения гейтов, книга, правила), `invariants` (нарушенные, строками), `calendar` (документ или путь), `today`.
- Слой сам добавляет: `common` (версия и манифест слоя этого дерева), `market.upside`, срок данных и `report` из
  календаря (`common.calendar`), порядок сроков (ближний первым) и `status`. Без календаря — `report.state` `current`
  и жёлтый статус «календаря нет».
- «Сегодня» шапки — `today` переходника (у модели — день сборки по её часам, с `FAKE_TODAY`); без него — дата
  `release.generated_at`. Шапку ставит сборка выпуска модели после проверки её контракта, в корень сразу за
  `schema`; затем модель пересчитывает хэш и размер по выпуску с шапкой. `release.generated_at` в хэш не входит, как
  время сборки выпуска. `validate` — схема `schemas/header-v1.json` (подмножество JSON Schema, без сторонних
  библиотек) и правила: сроки по возрастанию, `upside` = печать / цена − 1, статус с причинами.
- `python -m common.cli state` печатает шапку из выпуска (`RELEASE_PATH` переходника); у выпуска без шапки — шапку,
  собранную слоем из `header_inputs`.

## 3. Календарь — `data/calendar.json`, схема `family-calendar-v1`, модуль `common.calendar`

`{schema, issuer, as_of, events[]}`; событие: `id` (`<slug>.<вид>.<период>`), `kind` (`report` закрывающий период /
`operating` / `guidance` / `dividend` / `agm` / `strategy` / `cbr` / `other`), `date`, `precision` (`day` / `window` /
`month` / `estimate`), `earliest`, `latest`, `confirmed`, `covers` (период: `2026H2`, `2026Q4`, `2026`), `gives[]`
(что даёт: `revenue`, `ebitda`, `dividend`, `guidance` …), `title`, `src`, `note`. Модуль `common.calendar`: загрузка и
проверка, `next_fact(today)`, `closing_report(period)`, `report_state(today, anchor_period)` → поле `header.report`.

- Проверка (`check`, `load`, `python -m common.calendar [файл]`): `id` = `<issuer>.<kind>.<covers или дата>[.уточнение]`
  и не повторяется; `kind`, `precision` — из перечней; `date`, `earliest`, `latest` — ГГГГ-ММ-ДД, дата внутри окна;
  у `window` и `estimate` окно обязательно, у `day` — пусто или та же дата; подтверждённая дата — только `day`;
  `covers` — период или null, у `report` обязателен; `src` и `title` не пусты — у каждого события источник.
- Дата без объявления компании — `precision: estimate`: `date` — прошлогодняя дата компании, окно `earliest`–`latest` —
  её даты прошлых лет, в `src` — откуда эти даты.
- `next_fact(cal, today)` — ближнее событие с фактами (`report`, `operating`), окно неподтверждённой даты ещё не
  прошло; `closing_report(cal, period)` — `kind report` и `covers == period`; `deadlines(cal, anchor)` — срок данных:
  закрывающий отчёт периода после якоря.
- `report_state(cal, today, anchor)` → `header.report`: `current` — период после якоря ещё идёт; `closing_due` —
  период кончился, отчёт впереди или даты нет; `report_out` — подтверждённая дата отчёта наступила, а якорь прежний;
  `overdue` — окно неподтверждённой даты прошло, отчёта нет (календарь пора сверить с эмитентом).

Соответствие прежним форматам (переходники пишутся при переводе моделей, шаг В плана перехода):

| `family-calendar-v1` | А: X5 `{as_of, horizon, events[]}` | Б: Магнит, Лента `<m>-calendar-v1` | В: банки `data/facts/calendar.json` |
|---|---|---|---|
| `schema` | — (ставится `family-calendar-v1`) | `schema` | — |
| `issuer` | — (имя модели в реестре) | — | — |
| `as_of` | `as_of` | `as_of` | `as_of` |
| `events[].id` | — (собрать `<модель>.<вид>.<covers>`) | `id` (`lenta.q3_2026` → `lenta.operating.2026Q3`) | `id` (`cbr-2026-10-23` → `<модель>.cbr.2026-10-23`) |
| `kind` | `kind`: `ifrs` → `report`, если закрывает период книги, иначе `operating`; `cbr`, `dividend` — как есть | — (из `id` и `gives`) | `kind`: `cbr_forecast` → `cbr`; отчёт — `report` или `operating` |
| `date` | `date` | `when` | `date` |
| `precision` | — (`confirmed` → `day`, иначе `estimate`) | `precision` | `precision`; `estimated: true` → `estimate` с той же датой отказа |
| `earliest`, `latest` | — | `window.from`, `window.to` | `earliest`, `latest` |
| `confirmed` | `confirmed` | — (`precision == day`) | `confirmed` |
| `covers` | `covers` | — (из `id`) | `covers` |
| `gives[]` | — | `gives` | — |
| `title`, `src`, `note` | `title`, `src`, `note` | `title`, —, `note` | `title`, `src`, `note` |
| нет в формате | `horizon` | `purpose`, `precision_note`, `when_rule`, `prior_for`, у Ленты `watch[]` | `horizon`, `note`, `sources[]`, `in_book` |

## 4. Лист фактов — `data/facts/sheets/`, модуль `common.facts_sheet`

- `documents.csv`: `doc_id, title, issuer_url, published, sha256` — реестр документов; самих документов в
  репозитории нет. `published` — ГГГГ-ММ-ДД, `sha256` — полный (64 знака 0-9a-f), адрес — http(s).
- `<период>.csv` (`2026H1.csv`, `2026Q3.csv`): `metric, segment, period, value, unit, doc_id, page, note` — каждое число
  со ссылкой на документ и страницу (лист и ячейку для xlsx). `segment` пуст — число группы. Период — `ГГГГ`, `ГГГГH1`,
  `ГГГГH2`, `ГГГГQ1`…`ГГГГQ4`; лист периода несёт и ряды прошлых периодов; ключ (`metric`, `segment`, `period`) в листе
  один раз. Единицы — перечислением: `bn_rub`, `mln_shares`, `pct`, `rub`, `count`. Значение — число с точкой.
  Файлы — UTF-8 без BOM, разделитель — запятая.
- Загрузка и проверка: `load_sheet(dir, period)` (все расхождения разом — `SheetError.problems`), `check_dir(dir)`.
- Сборка: `to_facts(sheet, mapping)` → `{имя файла: текст JSON}`; `mapping` — `facts_mapping` переходника:
  `{файл: дерево}`, в дереве `Fact(metric, segment, period)` → число листа, `Ref(...)` → `{doc_id, page}`, функция
  `f(get)` → её результат, остальное — как есть. Несколько листов — списком (`merge`). Запись — `write_facts`.
- Тест модели «факты = сборка из листа» (байт в байт): `check_built(sheet, mapping, facts_dir) == []`.

## 5. Приём отчёта — одна команда, модули `common.report` и `common.testing`

`python -m common.cli report --period <П> [--dry-run] [--out var/report-<П>]`: лист нового периода → факты →
перенос книги на новый якорь функцией переходника `reanchor(facts, period, out_dir)` → разложение скачка заголовка на
«перекат / механика / сюрприз» (`out_dir/REPORT.md`). В `data/` не пишет; кандидат лежит в `--out`. Общая обвязка
теста `common.testing.reanchor_invariant`: «на ожидаемом пути = перекат» — модель подаёт свой ожидаемый путь
(`expected_path(period)`) как факты, механика должна быть ≈ 0; тест слоя запускается и на сервере.

- Внутри дерева модели `--out` — только под `var/` (по умолчанию `var/report-<период>`). Сухой прогон (`--dry-run`)
  листа не читает: факты = ожидаемый путь, сюрприз = 0. Без него — ещё и факты листа (`facts_sheets(period)`
  переходника, нет — лист самого периода).
- Разложение по медиане малой полосы и по точке, на тех же случайных числах: перекат = rolled − before (прежняя книга
  по своему пути на новый якорь), механика = after(ожидаемый путь) − rolled, сюрприз = after(факты) − after(ожидание).
  Итог — `REPORT.md` и `report.json` в `--out`.
- Ответ `reanchor`: `{before, rolled, after: {median, point[, printed, draws, seed]}, valuation_date, anchor_before,
  candidate: {что: путь}, notes[]}`. Перенос, которого модель не умеет, — `NotImplementedError` с причиной.
- Коды: 0 — механика в допуске (`REANCHOR_TOLERANCE` переходника, по умолчанию 0,5 ₽), 1 — отказ или механика вне
  допуска, 2 — не подключено.

## 6. Набор команд — `python -m common.cli <команда>`

| Команда | Что делает | Кто делает |
|---|---|---|
| `state` | печатает состояние модели по последнему собранному выпуску и файлам репозитория: модель, слой, миры, выпуск, книга, флаги, сроки, следующий отчёт, `status`, полнота переходника, команды; раздел без данных — «нет» с причиной, без падения | слой |
| `rederive` | перевывод: выведенные числа книги под суждения (`--dry-run` печатает разницу) | модель |
| `quick` | быстрые числа: сетка и малая полоса | модель |
| `band` | полная полоса (2 000 прогонов) и таблицы результатов книги | модель |
| `server-tests` | набор, который сервер гоняет перед сборкой; печатает счётчики против ожидаемых | модель |
| `release` | сборка выпуска (`--fast`, `--live-file`); печатает шапку | модель |
| `report` | приём отчёта (раздел 5) — через `expected_path`, `reanchor`, `facts_mapping` переходника | слой |
| `worlds` | приём миров — вызывает `ops/tools/adopt_worlds.py` модели с теми же аргументами; без `--version` (и не `--check`) — последняя версия индекса источника миров | слой |
| `adopt-common` | приём общего слоя (раздел 1) | слой |

Команды модели — словарь `COMMANDS` переходника: значение — функция `f(argv) -> код выхода`, список аргументов
команды модели (`"{python}"` — интерпретатор запуска; аргументы после имени команды дописываются; запуск — из корня
дерева) или строка «почему не подключено». Команда без строки или со строкой-причиной печатает «не подключено: …» и
выходит с кодом 2.

## 7. Переходник модели — `ops/common_adapter.py` (своё модели)

| Имя | Что |
|---|---|
| `SLUG`, `TICKERS` | имя модели в реестре, тикеры |
| `COMMANDS` | команда → вызов модели (раздел 6) |
| `header_inputs(payload) -> dict` | из выпуска модели — во входы шапки (раздел 2) |
| `facts_mapping` | лист → файлы фактов (раздел 4) |
| `reanchor(facts, period, out_dir)` | перенос книги на новый якорь (раздел 5) |
| `expected_path(period)` | ожидаемый путь — для теста переноса (раздел 5) |
| `NAME`, `RELEASE_PATH` | по желанию: название модели; путь последнего собранного выпуска от корня (его читает `state`) |
| `facts_sheets(period)`, `REANCHOR_TOLERANCE` | по желанию: листы фактов периода для `report`; допуск механики, ₽ |

Всё, что слою нужно от модели, — только через переходник. Чего не хватает — печатают `adopt-common` и `state`.
