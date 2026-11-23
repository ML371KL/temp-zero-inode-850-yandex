# Эксплуатация «Яндекс 850»

Конвейер модели справедливой стоимости «Яндекса» на общем VPS панелей владельца: сбор живых входов, сборка
выпуска, публикация в ветку `release` репозитория данных, сверка через боевую дверь
`https://tzi-850-yandex.pages.dev/api/model`. Сервер, такты, публикация и дверь — по образцу сервера семейства
(репозиторий и коммит — в рабочем файле проекта); отличия от него названы по месту.

Инфраструктурных подробностей сервера (адрес, имена ключей, доступы) здесь нет — они в справочнике владельца вне
репозитория: репозиторий публичный.

## 1. Что где

| Что | Где |
|---|---|
| Код на сервере | `/srv/dash/repo-850-yandex` — клон `main`, `--single-branch`, анонимный HTTPS |
| Окружение Python | `/srv/dash/repo-850-yandex/.venv` (Python 3.12); отметка зависимостей `.venv/.requirements.sha256` |
| Настройки | `/usr/local/etc/yandex-850/env` (`root:dash 640`), образец — `ops/env.example`; секретов в нём нет |
| Состояние | `/var/lib/yandex-850` (`StateDirectory=yandex-850`, владелец `dash`) |
| Юниты | `yandex850-{collect,daily,rebuild}.{service,timer}` из `ops/systemd/`, журнал `SyslogIdentifier=yandex850` |
| Репозиторий данных | `ML371KL/temp-zero-inode-850-yandex-data` (публичный, отдельный от кода): ветка `release` по умолчанию — `README.md`, `.nojekyll`, `latest.json`, `releases/<sha256>.json`. Deploy-ключ сервера пишет только сюда; в репозиторий кода ключ не добавляется (ключ записи в личном репозитории обходит правила веток) |
| Раздача данных | GitHub Pages репозитория данных: `https://ml371kl.github.io/temp-zero-inode-850-yandex-data/latest.json` |
| Дверь браузера | Pages Function `functions/api/model.js` на `tzi-850-yandex.pages.dev`; запасной источник — `raw.githubusercontent.com/…/release/latest.json` |
| Витрина | статика `web/`, выкладка с ноутбука (раздел 8) |
| Тревоги | общий мост `/usr/local/sbin/dash-alert` (`ExecStopPost`) и сторож `dash-watch` — из служебного репозитория панелей, не отсюда |

Внутри `/var/lib/yandex-850`:

| Путь | Что |
|---|---|
| `live/live.json` | живые входы последнего сбора (`indicators.collect daily`): цена YDEX, кривая ОФЗ, ключевая ставка, история цены |
| `indicators/raw/<источник>/<дата>/` | сырой архив ответов ISS и ЦБ (`ЧЧММСС_<имя>` + `.meta.json`), 400 дней |
| `release/latest.json` | локальный выпуск сборки; он же эталон цены и защиты заголовка для следующей сборки |
| `release-repo/` | одноразовый клон ветки `release` (каждый прогон — `fetch` + `reset --hard origin/release`) |
| `release.pushed` | выпуск отправлен в ветку, сверка ещё не прошла |
| `release.commit` | коммит кода выпуска, ПОДТВЕРЖДЁННОГО сверкой; по нему пересборка решает, есть ли что пересобирать |
| `rebuild.tried` | коммит, на котором пересборка уже запускалась (упавшая не повторяется каждые 15 минут) |
| `future.checked` | «день код» прогона «в будущем» |
| `run.lock` | один замок на все такты и на откат |
| `logs/<дата UTC>.log` | общий журнал тактов, 45 дней |

## 2. Канал данных

```
indicators.collect daily → $YANDEX_STATE_DIR/live/live.json
  → ops/build_release.py --live-file … [--previous release/latest.json] --out $YANDEX_STATE_DIR/release/latest.json
  → ops/publish.py: клон ветки release, releases/<sha256>.json + latest.json одним коммитом, push
  → GitHub Pages репозитория данных (1–3 минуты)
  → functions/api/model.js: Pages → raw (запасной) → копия края
  → браузер и сверка ops/publish.py --verify
```

Как у образца: адресация содержимым (`releases/<sha256>.json` пишется один раз, `published_at` вне хэша); коммит
только при разнице; push — три попытки; автор коммитов — `YANDEX_GIT_NAME`/`YANDEX_GIT_EMAIL`, только
noreply-адрес; сверка — до совпадения `payload_sha256` и `published_at` в окне `YANDEX_VERIFY_TIMEOUT` (600 с), не
дождалась — код 3, дожимает пересборка; прореживание — только после подтверждённой сверки. Подробности —
шапка `ops/publish.py`.

Живыми в число идут только цена YDEX и дата оценки (`docs/MODEL.md` §13.4); кривая, ставка и история — наблюдение
для плиток и флагов. Правила годности цены и запасная цена — шапка `indicators/live.py`.

## 3. Такты

| Юнит | Когда (UTC) | Что делает |
|---|---|---|
| `yandex850-collect` | каждый день 06:50 (±5 мин), `Persistent` | обновление кода → зависимости → сбор живых входов → по понедельникам тесты такта «в будущем» (+60 дней) → ротация сырого архива → объём → здоровье (тревога) |
| `yandex850-daily` | будни 15:55 (разброс до 2 мин), `Persistent` | обновление кода → зависимости → сбор с повтором отказавших → здоровье (напоминание) → тесты такта → сборка → публикация → сверка → прореживание → объём |
| `yandex850-rebuild` | `*:14,29,44,59` | тихая проверка: дожим сверки прошлого такта; если код в `CODE_DIRS` (`model`, `indicators`, `ops`, `common`, `data/assumptions`, `data/facts`, `data/calendar.json`, `requirements.txt`, без `*.md`) изменился с `release.commit` — тесты такта → сборка → публикация → сверка → прореживание, без сбора |

- **Сбор** — `python -m indicators.collect daily` (вечером `--retry`): один сборщик `indicators.live`; эталон цены —
  `release/latest.json`. Код 3 (тревога в конце такта) — отказал наблюдаемый источник (`quotes`, `price_history`,
  `curve`, `key_rate`) или цена ЗАПАСНАЯ (в шапке выпуска — флаги warn `live_inputs` и `price_fallback`, статус
  жёлтый); код 1 — годной цены нет и заменить нечем. Облигации и аналоги в сборе не заданы (`indicators/iss.py`:
  `BOND_EMITTER`, `PEERS`) — не опрашиваются, строка в журнале, не тревога и не отказ в `live.errors`. Отличие от
  образца: рядов с винтажами и нау-каста нет.
- **Обновление кода** — `git fetch` + `reset --hard origin/main` и `exec` нового `run.sh` один раз за такт.
- **Зависимости** — по отметке `sha256(requirements.txt)` в venv; неудача `pip install` — провал такта.
- **Тесты такта** — белый список по метке `tact` (`pytest.ini`, `TACT_FILES` в `tests/conftest.py`), выражение
  `TACT_TESTS` в `run.sh`, одним процессом; ноль выбранных — провал. Тот же отбор в 4 процессах со счётчиками против
  ожидаемых — `python -m common.cli server-tests` (`ops/server_tests.py`: `EXPECTED`); новый тест такта — поправить
  `EXPECTED` тем же коммитом.
- **Замок** один; плановый такт ждёт его до `LOCK_WAIT_SECONDS` (= потолок пересборки), пересборка на занятом
  замке молча выходит с 0.

## 4. Коды и тревоги

| Код юнита | Смысл | Повтор systemd |
|---|---|---|
| 0 | прошло | — |
| 1 | ПРОВАЛ шага, выпуска нет (в журнале «ПРОВАЛ на шаге «…» (код N)») | `daily`, `collect` — через 3 мин, до 3 раз за 30 мин |
| 8 | ТРЕВОГА: работа выполнена, выпуск (если такт его делает) на витрине, последняя строка называет причины | `daily` — нет (`RestartPreventExitStatus=8`); `collect` — да |
| 64 | неверный режим или проба тревоги с источником не из такта | нет |
| 75 | замок держит другой такт дольше `LOCK_WAIT_SECONDS` | да |
| 78 | нет env-файла или в нём нет `YANDEX_REPO_DIR`/`YANDEX_STATE_DIR` | нет |

Внутри такта шаги отвечают кодом 3 «сделано, но требует внимания»; причины копятся и уходят одним кодом 8 в конце.
Три провала подряд — `start-limit-hit`; после исправления `systemctl reset-failed <юнит>.service`.

Мост `dash-alert` зовётся из `ExecStopPost` всегда и шлёт смену состояния. Префикс `yandex850` новый: панель
регистрируется строкой в `PANELS` у `dash-watch` (адрес двери, порог свежести по `Last-Modified`) и веткой `case` в
`dash-alert` — по процедуре служебного репозитория панелей (справочник семейства, раздел 4).

**Проба доставки тревоги** (утренний такт; источник объявляется отказавшим без обращения к нему):

```bash
sudo -u dash env ENV_FILE=/usr/local/etc/yandex-850/env YANDEX_SIMULATE_FAILURE=curve \
     bash /srv/dash/repo-850-yandex/ops/run.sh collect
echo $?    # 8, последняя строка — «ТРЕВОГА (код 8): источник живых входов не собран …»
```

Имя не из `quotes`, `price_history`, `curve`, `key_rate` — код 64.

## 5. Установка на сервер (по шагам)

Пользователь `dash`, каталог `/srv/dash` и общие скрипты `dash-alert`/`dash-watch` уже есть (шаблон панелей
владельца). Команды под `dash` — `sudo -u dash -H bash -c 'cd /srv/dash/repo-850-yandex && …'`. Порядок
обязателен: первый выпуск руками и замер сборки → GitHub Pages → витрина → таймеры. Готовый набор команд с
проверкой и откатом каждой — у архитектора (файл команд задачи сервера в папке проекта).

1. **Код и окружение.**
   ```bash
   sudo -u dash git clone --single-branch --branch main \
        https://github.com/ML371KL/temp-zero-inode-850-yandex.git /srv/dash/repo-850-yandex
   sudo -u dash python3.12 -m venv /srv/dash/repo-850-yandex/.venv
   sudo -u dash -H bash -c 'cd /srv/dash/repo-850-yandex && .venv/bin/python -m pip install --quiet -r requirements.txt \
        && sha256sum requirements.txt | cut -d" " -f1 > .venv/.requirements.sha256'
   sudo install -d -o dash -g dash -m 750 /var/lib/yandex-850
   ```
   Каталог состояния потом ведёт systemd (`StateDirectory`); для ручных команд до первого юнита он нужен заранее.
2. **Настройки.**
   ```bash
   sudo install -d -o root -g dash -m 750 /usr/local/etc/yandex-850
   sudo install -o root -g dash -m 640 /srv/dash/repo-850-yandex/ops/env.example /usr/local/etc/yandex-850/env
   sudoedit /usr/local/etc/yandex-850/env      # проверить значения; секретов там нет
   ```
   Ключевые: `YANDEX_REPO_DIR`, `YANDEX_STATE_DIR` (без них — код 78), `YANDEX_RELEASE_REMOTE` (только ssh-алиас
   `gh-850-yandex:…`), `YANDEX_GIT_NAME` и `YANDEX_GIT_EMAIL` (noreply-адрес), `YANDEX_PUBLIC_URL` (боевая дверь);
   `YANDEX_WORKERS` — процессов полосы (раздел 9).
3. **Ключ записи в репозиторий данных.** Отдельный deploy-ключ ed25519 в `/srv/dash/.ssh/` (имя файла — в
   справочнике владельца), открытая часть — в **репозиторий данных** `temp-zero-inode-850-yandex-data`: Settings →
   Deploy keys → Allow write access. В `/srv/dash/.ssh/config` — алиас:
   ```
   Host gh-850-yandex
       HostName github.com
       User git
       IdentityFile /srv/dash/.ssh/<ключ Яндекса>
       IdentitiesOnly yes
   ```
   Ключ хоста GitHub — заранее в `/srv/dash/.ssh/known_hosts` (под `ProtectSystem=strict` юнит его не допишет).
   Проверка: `sudo -u dash ssh -T gh-850-yandex` — «successfully authenticated».
4. **Репозиторий данных** — один раз: публичный, ветка `release` по умолчанию (`README.md` и `.nojekyll`). Если
   ветки нет:
   ```bash
   sudo -u dash bash -c 'set -a; . /usr/local/etc/yandex-850/env; set +a;
        cd /srv/dash/repo-850-yandex && .venv/bin/python ops/publish.py init'
   ```
   Правила веток: код — `main` пишет только владелец; данные — `release` без удаления и force-push.
5. **Юниты — без таймеров.**
   ```bash
   sudo sh -c 'install -o root -g root -m 644 /srv/dash/repo-850-yandex/ops/systemd/* /etc/systemd/system/'
   sudo systemctl daemon-reload
   ```
   Таймеры ставятся файлами, но не включаются (`enable` — шаг 9).
6. **Первый выпуск руками и замер сборки** (видно все шаги):
   `sudo systemctl start yandex850-daily.service` и `journalctl -u yandex850-daily -f`. Такт кончится кодом 8
   «боевая дверь не подтвердила»: Pages ещё не включён — это ожидаемо. Время шагов — из журнала такта (каждый шаг
   печатает `── <шаг> ── ЧЧ:ММ:ССZ`), пик памяти — `systemctl show yandex850-daily -p MemoryPeak`. Сборка дольше
   потолка или память выше `MemoryMax` — до таймеров поправить `ops/budgets.json`, потолки юнитов,
   `LOCK_WAIT_SECONDS` и `YANDEX_WORKERS` (раздел 9) и поставить юниты заново.
7. **GitHub Pages репозитория данных**: Settings → Pages → Deploy from a branch → `release`, `/ (root)`. Через 1–3
   минуты `https://ml371kl.github.io/temp-zero-inode-850-yandex-data/latest.json` отдаёт выпуск.
8. **Витрина** — с ноутбука (раздел 8). Затем сверка руками:
   `sudo -u dash bash -c 'set -a; . /usr/local/etc/yandex-850/env; set +a; cd /srv/dash/repo-850-yandex && .venv/bin/python ops/publish.py --verify'`
   — строка «СВЕРКА: выпуск … — пройдена».
9. **Таймеры — только после замера и сверки расписания с соседями.** Сначала
   `systemctl list-timers --all 'lenta850-*' 'sber850-*' 't850-*' 'vtb850-*' 'svcb850-*' 'magnit850oa-*'` — расписание
   Яндекса выбрано мимо соседей на 24.11.2026 (их сборы: 03:10, 04:20, 04:50, 05:20, 05:50, 06:20 UTC; суточные:
   16:40, 17:25, 18:10, 19:00, 19:50, 20:40; пересборки — минуты 02, 04, 05, 07, 09, 12 четверти): утренний сбор
   06:50 UTC — через полчаса после последнего соседнего (ЦБ отвечает 403 на плотные серии с одного адреса), суточный —
   будни 15:55 UTC с разбросом до 2 мин (18:55–18:57 МСК: после основной сессии MOEX, с повтором сбора — до
   вечерней, раньше соседей), пересборка — `*:14,29,44,59`.
   Ближе получаса к утреннему сбору соседа или в ту же минуту пересборки — сдвинуть `OnCalendar` в таймерах коммитом
   до включения. Затем
   `sudo systemctl enable --now yandex850-collect.timer yandex850-daily.timer yandex850-rebuild.timer`.
10. **Сторож и мост** — регистрация по процедуре служебного репозитория (раздел 4), затем проба доставки тревоги.

## 6. Откат

```bash
sudo -u dash env ENV_FILE=/usr/local/etc/yandex-850/env \
     bash /srv/dash/repo-850-yandex/ops/run.sh rollback <sha выпуска, от 12 знаков>
```

Под тем же замком, что и такты: `latest.json` перезаписывается выпуском `releases/<sha>.json` с НОВЫМ
`published_at`, затем сверка. Список выпусков — `git log` и `ls releases/` в `/var/lib/yandex-850/release-repo`.
Откат — пауза на один такт: ближайший суточный такт (или пересборка на новом коммите) соберёт новый выпуск. Причина
не устранена — `sudo systemctl stop yandex850-daily.timer yandex850-rebuild.timer`.

## 7. Выкладка кода и юнитов

Код попадает на сервер только через `main`: рабочая ветка → CI зелёный → fast-forward `main` → ближайшая пересборка
публикует выпуск на новом коде. Меняются юниты: остановить таймер пересборки, поставить юниты из обновлённой копии
(`install … /etc/systemd/system/`), `daemon-reload`, запустить таймер.

## 8. Витрина

```bash
npx wrangler@4.135.0 pages deploy web --project-name tzi-850-yandex --branch main
```

Метка `--branch` обязана совпадать с производственной веткой проекта (`main`), иначе деплой уйдёт в preview.

## 9. Потолки времени и бюджеты

- **Время**: `RuntimeMaxSec` юнитов и `LOCK_WAIT_SECONDS` считаются из `ops/budgets.json` (формула — в его поле
  `about`); равенство сверяет `tests/test_ops_units.py`. До замера на сервере там оценка (`status: estimate`) —
  шаги образца сервера. Ноутбук (8 ядер, сухой прогон `run.sh daily` 23.11.2026, блок `laptop` файла): тесты такта
  одним процессом — 302 с, сборка с `--fast` (400 прогонов) — 192 с. Полная сборка выпуска (2 000 прогонов и развилки
  с перевыводом) — 714 с на 12 процессах, пик памяти дерева процессов ≈ 230 МБ (опрос раз в секунду) при
  `MemoryMax=512M` (`full_build` того же файла); полный набор тестов одним процессом — 24 мин. Тесты такта идут одним
  процессом, как у образца; потолки юнитов и `LOCK_WAIT_SECONDS` ставятся по замеру первого такта на сервере до
  таймеров. Потолок ниже настоящего времени убивает такт на середине.
- **Память**: `MemoryHigh=384M`, `MemoryMax=512M`, `Nice=10` — сервер общий. Полоса считается пулом процессов
  (`YANDEX_WORKERS`, по умолчанию — ядра машины): число процессов подбирается замером первого выпуска (шаг 6).
- **Диск**: сырой архив — 400 дней (`indicators.collect prune`), потолок 300 МБ (`health`).

## 10. Диагностика

```bash
systemctl list-timers 'yandex850-*'
journalctl -u yandex850-daily -n 200 --no-pager
tail -n 100 /var/lib/yandex-850/logs/$(date -u +%F).log
cat /var/lib/yandex-850/release.commit /var/lib/yandex-850/release.pushed 2>/dev/null
sudo -u dash -H bash -c 'cd /srv/dash/repo-850-yandex && YANDEX_STATE_DIR=/var/lib/yandex-850 .venv/bin/python -m indicators.collect status'
curl -sI -A yandex-850-check https://tzi-850-yandex.pages.dev/api/model | grep -iE '^(HTTP|last-modified|etag|x-data-source)'
```

`x-data-source: raw` или `edge-cache` — GitHub Pages ветки не отдаёт: проверить Settings → Pages и прогоны
`pages-build-deployment` в Actions.
