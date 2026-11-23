"""Вход тактов сервера к сборщикам YDEX: `python -m indicators.collect <команда>`.

Тот же вход, что у образца сервера (`ops/run.sh`: `collect`, `health`, `status`), но сборщик у модели один —
`indicators.live` (цена YDEX и история ISS, кривая ОФЗ ISS, ключевая ставка ЦБ); рядов с винтажами и
нау-каста у модели нет.

    daily [--retry] [--simulate-failure ИСТОЧНИК]   сбор живых входов → $STATE/live/live.json
    health                                          здоровье состояния: свежесть и годность живых входов, объём архива
    status                                          объём состояния (сырой архив по источникам, выпуски, живые входы)
    prune [--keep-days N]                           ротация сырого архива: дни старше N (400) удаляются

Состояние — `$YANDEX_STATE_DIR` (на сервере `/var/lib/yandex-850`), без него — `var/` репозитория. Сырой архив
ответов — `$YANDEX_RAW_DIR`, без него — `$YANDEX_STATE_DIR/indicators/raw` (под `ProtectSystem=strict` каталог
кода для архива не годится: туда пишет только `git reset`).

Коды `daily`: 0 — всё собрано; `ALARM_EXIT` (3) — живые входы записаны, но отказал наблюдаемый источник
(`WATCHED`) или цена ЗАПАСНАЯ (последняя принятая из прошлого выпуска): такт доводит выпуск до витрины, тревога —
кодом 8 в конце такта; 1 — годной цены нет и заменить нечем, или прошлый выпуск не читается: выпуска не будет;
64 — проба тревоги с источником не из `WATCHED`. Эталон цены — прошлый выпуск `$STATE/release/latest.json`.

`--retry` (вечерний такт): отказал наблюдаемый источник — одна повторная попытка через `RETRY_PAUSE_S` секунд (ЦБ
отвечает 403 на плотные серии; ISS моргает), берётся прогон с меньшим числом отказов.

`--simulate-failure` (проба доставки тревоги, `YANDEX_SIMULATE_FAILURE` в `ops/run.sh`): названный источник
объявляется отказавшим БЕЗ обращения к нему, остальные собираются как обычно.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import http, live  # noqa: E402
from indicators.http import FetchError, read_json_source  # noqa: E402
from indicators.runlog import report  # noqa: E402

ALARM_EXIT = 3
# Источники, отказ которых — тревога такта. Облигации и аналоги в сборе не заданы (indicators/iss.py: BOND_EMITTER,
# PEERS) — их строки в `errors` видны в журнале, но тревогой не становятся.
WATCHED = ("quotes", "price_history", "curve", "key_rate")
RETRY_PAUSE_S = 60
RAW_KEEP_DAYS = 400
# Живые входы старше — состояние не в порядке: суточный такт идёт по будням, разрыв пятница → понедельник — 3 дня.
MAX_LIVE_AGE_DAYS = 4
# Потолок сырого архива: два сбора в день по ≈ 40 КБ — ≈ 30 МБ в год; диск сервера общий (30 ГБ на всё).
RAW_CAP_BYTES = 300 * 1024 * 1024


def state_dir() -> Path:
    return Path(os.environ["YANDEX_STATE_DIR"]) if os.environ.get("YANDEX_STATE_DIR") else ROOT / "var"


def raw_dir() -> Path:
    if os.environ.get("YANDEX_RAW_DIR"):
        return Path(os.environ["YANDEX_RAW_DIR"])
    if os.environ.get("YANDEX_STATE_DIR"):
        return Path(os.environ["YANDEX_STATE_DIR"]) / "indicators" / "raw"
    return http.raw_root()


def live_path() -> Path:
    return state_dir() / "live" / "live.json"


def release_path() -> Path:
    return state_dir() / "release" / "latest.json"


# Имя ответа (`name` у `indicators.http.fetch`) → источник в `errors` живых входов.
def _source_of(source: str, name: str) -> str:
    if source == "cbr":
        return "key_rate"
    if name.startswith("quotes"):
        return "quotes"
    if name.startswith("history_"):
        return "price_history"
    if name.startswith("zcyc"):
        return "curve"
    return "bonds"


def _getter(simulate: tuple[str, ...]):
    def getter(url, *, source, name, **kw):
        if _source_of(source, name) in simulate:
            raise FetchError(url, None, "ПРОБА ТРЕВОГИ: источник объявлен отказавшим без обращения к нему")
        return http.fetch(url, source=source, name=name, **kw)
    return getter


def _troubles(result: dict) -> list[str]:
    """Причины тревоги прогона: отказы наблюдаемых источников и запасная цена."""
    errors = result.get("errors") or {}
    out = [f"{name}: {errors[name]}" for name in WATCHED if name in errors]
    price = result.get("price") or {}
    if price.get("status") == "fallback":
        out.append(f"цена ЗАПАСНАЯ: {price.get('reason')}")
    return out


def cmd_daily(*, retry: bool = False, simulate: tuple[str, ...] = (), today: date | None = None,
              sleep=time.sleep) -> int:
    unknown = [name for name in simulate if name not in WATCHED]
    if unknown:
        report(f"ПРОБА ТРЕВОГИ: источника {', '.join(unknown)} в такте нет (есть: {', '.join(WATCHED)})")
        return 64
    os.environ.setdefault("YANDEX_RAW_DIR", str(raw_dir()))
    previous_file = release_path()
    try:
        previous = read_json_source(str(previous_file)) if previous_file.exists() else None
    except (OSError, ValueError, FetchError) as exc:
        report(f"ПРОВАЛ на шаге чтение прошлого выпуска {previous_file}: {exc}")
        return 1
    if previous is None:
        print(f"  прошлого выпуска нет ({previous_file}): эталон цены — последнее закрытие истории ISS")
    getter = _getter(tuple(simulate))
    try:
        result = live.collect_live(previous, today=today, getter=getter)
        troubles = _troubles(result)
        if retry and troubles:
            print(f"  отказы: {len(troubles)} — повтор через {RETRY_PAUSE_S} с")
            sleep(RETRY_PAUSE_S)
            again = live.collect_live(previous, today=today, getter=getter)
            if len(_troubles(again)) < len(troubles):
                result, troubles = again, _troubles(again)
    except live.PriceUnavailable as exc:
        report(f"ПРОВАЛ на шаге сбор живых входов: {exc}")
        return 1
    live.log_live(result, indent="  ")
    live.write_live(result, live_path())
    print(f"  живые входы записаны: {live_path()}")
    if troubles:
        for line in troubles:
            print(f"  ОШИБКА: {line}")
        report(f"ТРЕВОГА: живые входы записаны, но {len(troubles)} причин(ы) требуют внимания (строки «ОШИБКА:» выше)")
        return ALARM_EXIT
    return 0


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def _raw_days(root: Path) -> dict[str, list[str]]:
    if not root.exists():
        return {}
    return {src.name: sorted(d.name for d in src.iterdir() if d.is_dir())
            for src in sorted(root.iterdir()) if src.is_dir()}


def cmd_health(*, today: date | None = None) -> int:
    today = today or live.msk_today()
    problems, data = [], None
    path = live_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"живых входов нет: {path}")
    except (OSError, ValueError) as exc:
        problems.append(f"живые входы не читаются: {path}: {exc}")
    if data is not None:
        if data.get("schema") != live.SCHEMA:
            problems.append(f"живые входы: схема {data.get('schema')!r}, ожидалась {live.SCHEMA!r}")
        try:
            age = (today - date.fromisoformat(str(data.get("run_date")))).days
        except ValueError:
            age = None
        if age is None or age > MAX_LIVE_AGE_DAYS:
            problems.append(f"живые входы от {data.get('run_date')} — старше {MAX_LIVE_AGE_DAYS} дн.")
        price = data.get("price") or {}
        if price.get("status") != "live":
            problems.append(f"цена YDEX запасная: {price.get('reason')}")
        failed = [name for name in WATCHED if name in (data.get("errors") or {})]
        if failed:
            problems.append(f"отказали источники: {', '.join(failed)}")
    raw = _size(raw_dir())
    if raw > RAW_CAP_BYTES:
        problems.append(f"сырой архив {raw / 2**20:.0f} МБ — больше потолка {RAW_CAP_BYTES / 2**20:.0f} МБ")
    for line in problems:
        print(f"  НЕ В ПОРЯДКЕ: {line}")
    if not problems:
        print(f"  состояние в порядке: живые входы от {data.get('run_date')}, сырой архив {raw / 2**20:.1f} МБ")
    return 1 if problems else 0


def cmd_status() -> int:
    state, raw = state_dir(), raw_dir()
    print(f"  состояние {state}: {_size(state) / 2**20:.1f} МБ")
    for source, days in _raw_days(raw).items():
        size = _size(raw / source)
        print(f"  сырой архив {source}: {len(days)} дн. ({days[0]} — {days[-1]}), {size / 2**10:.0f} КБ"
              if days else f"  сырой архив {source}: пусто")
    print(f"  выпуски {state / 'release'}: {_size(state / 'release') / 2**10:.0f} КБ; "
          f"клон ветки release: {_size(state / 'release-repo') / 2**20:.1f} МБ")
    try:
        data = json.loads(live_path().read_text(encoding="utf-8"))
        price = data.get("price") or {}
        print(f"  живые входы: прогон {data.get('run_date')}, цена {price.get('value')} ₽ от {price.get('date')} "
              f"({price.get('status')})")
    except (OSError, ValueError):
        print(f"  живых входов нет: {live_path()}")
    return 0


def prune_raw(keep_days: int = RAW_KEEP_DAYS, *, today: date | None = None) -> list[str]:
    """Удаляет дни сырого архива старше `keep_days`; возвращает «источник/день» удалённых."""
    cutoff = (today or live.msk_today()) - timedelta(days=keep_days)
    root, removed = raw_dir(), []
    for source, days in _raw_days(root).items():
        for day in days:
            try:
                old = date.fromisoformat(day) < cutoff
            except ValueError:
                continue
            if old:
                shutil.rmtree(root / source / day)
                removed.append(f"{source}/{day}")
    return removed


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Сборщики YDEX для тактов сервера")
    parser.add_argument("command", choices=("daily", "health", "status", "prune"))
    parser.add_argument("--retry", action="store_true", help="daily: повтор отказавших источников")
    parser.add_argument("--simulate-failure", action="append", default=[], metavar="ИСТОЧНИК",
                        help=f"daily: проба тревоги — источник ({', '.join(WATCHED)}) объявляется отказавшим")
    parser.add_argument("--keep-days", type=int, default=RAW_KEEP_DAYS, help="prune: сколько дней хранить")
    parser.add_argument("--today", help="дата прогона ГГГГ-ММ-ДД (по умолчанию — сегодня, Москва)")
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else None
    if args.command == "daily":
        return cmd_daily(retry=args.retry, simulate=tuple(args.simulate_failure), today=today)
    if args.command == "health":
        return cmd_health(today=today)
    if args.command == "status":
        return cmd_status()
    removed = prune_raw(args.keep_days, today=today)
    print(f"  прорежено дней: {len(removed)}")
    for item in removed[:10]:
        print(f"    {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
