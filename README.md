# YDEX — модель справедливой стоимости акции МКПАО «Яндекс» (семейство «850»)

Витрина — https://tzi-850-yandex.pages.dev, выпуск данных — `/api/model`. Первая модель сектора «технологии и
интернет-сервисы»: семь блоков по сегментам отчётности, банк вне долга компании, полугодовой шаг от МСФО за
I полугодие 2026 г. Чем модель отличается от остальных — `docs/PASSPORT.md`; решения с ценой — `docs/DECISIONS.md`;
версии книги — `docs/CHANGELOG.md`; методика — `docs/MODEL.md`.

## Что где

| Путь | Что |
|---|---|
| `model/` | ядро сервисов (`core.py`), книга и её схема (`book.py`, `book_schema.py`), факты (`facts.py`), перевывод (`rederive.py`); сетка, слои, полоса, обратный DCF, проверки, выпуск — общее сектора (API — `model/README.md`) |
| `data/assumptions/` | книга допущений `assumptions.yaml`, объяснения проверок `gate_explanations.yaml`, запись миров ставок и её состояние |
| `data/facts/`, `data/calendar.json` | факты отчётности со страницами источников (лист фактов — `data/facts/sheets/`), календарь эмитента |
| `common/` | общий слой семейства, побайтово одинаковый у всех моделей (`common/README.md`); переходник модели — `ops/common_adapter.py` |
| `indicators/` | живые входы: цена YDEX и кривая ОФЗ с биржи, ключевая ставка Банка России; вход тактов `indicators.collect` |
| `ops/` | сборка выпуска `build_release.py`, публикация в репозиторий данных `publish.py`, такты сервера `run.sh`, юниты `systemd/yandex850-*`, образец настроек `env.example`, тесты такта `server_tests.py` (`ops/README.md`) |
| `ops/tools/` | приём миров `adopt_worlds.py` (общий, побайтово), числа в документах, предпросмотр и снимки витрины, мок выпуска |
| `functions/`, `web/` | дверь `/api/model` и витрина Cloudflare Pages (`docs/DASHBOARD.md`) |
| `docs/` | методика, контракт выпуска `PAYLOAD.md`, витрина `DASHBOARD.md`, паспорт, решения, история изменений |
| `tests/` | тесты ядра, книг, проверок, выпуска, витрины, сборщиков и сервера; второй счёт `independent_model.py`; мутационный набор `mutations.py` |

`var/` — рабочий каталог прогонов, в git не идёт.

## Запуск

Python 3.12; окружение — любое виртуальное окружение с пакетами `requirements.txt` (PyYAML, pytest). Для тестов витрины и
двери — Node 22+.

```
python -m common.cli state          # шапка: книга, якорь, миры, сроки, флаги
python -m common.cli rederive       # перевывод выведенных чисел книги
python -m common.cli quick          # быстрые числа: сетка и малая полоса
python -m common.cli band           # полная полоса
python -m common.cli server-tests   # тесты такта, счётчики против ожидаемых
python -m common.cli release --fast # сборка выпуска (без --fast — полная)
python -m pytest -q -m "not network" --basetemp var/pytest-tmp     # весь набор
```

Живые сборщики в тестах — только с `YANDEX_NETWORK=1` и `-m network`.
