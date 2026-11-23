"""Сборка выпуска YDEX (контракт `yandex-v1`, docs/PAYLOAD.md).

    python ops/build_release.py --live-file var/live/live.json \
        --previous var/state/latest.json --journal var/state/journal.json \
        --history var/state/history.json
    python ops/build_release.py --live [...]         собрать живые входы здесь же
    python ops/build_release.py --book --fast        на входах книги, малая полоса (CI, быстрые числа)
    python -m common.cli release [--fast] [...]      то же через набор команд (без источника — --book)
    python ops/build_release.py --check var/release/latest.json
                                                     проверить готовый файл

`--previous PATH|URL` — прошлый выпуск (эталон цены, защита заголовка, «что
изменилось»); `--journal PATH` — журнал прогнозов из ветки `data`; `--history PATH` —
`history.json` ветки `data` (книга каждого опубликованного выпуска — для `journal.releases`).

Шаги: чтение входов → импорт ядра → `model.payload.build_payload` → шапка
семейства `header` (`common.header`, хэш — после неё, `model.payload.seal`) →
`model.payload.validate` и схема шапки → строгий JSON и потолок размера → закрытые записи
журнала против `data/facts/actuals.json` → запись. Последняя
строка — «готово: …» (код 0) или «ПРОВАЛ на шаге …: причина» (код 1): любой
провал — выпуск не записан, на витрине остаётся прежний.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators.http import FetchError, read_json_source, strict_json  # noqa: E402
from indicators.runlog import report  # noqa: E402

MODEL_MODULE = "model.payload"
# Потолок контракта yandex-v1: 500 000 байт компактного JSON (docs/PAYLOAD.md).
MAX_PAYLOAD_BYTES = 500_000
DEFAULT_OUT = ROOT / "var" / "release" / "latest.json"
DEFAULT_LIVE_OUT = ROOT / "var" / "live" / "live.json"


class StepFailed(Exception):
    def __init__(self, step: str, reason: str):
        super().__init__(f"{step}: {reason}")
        self.step, self.reason = step, reason


def load_model():
    """`model.payload` с `build_payload` и `validate`; нет ядра — внятный провал."""
    try:
        module = importlib.import_module(MODEL_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name and MODEL_MODULE.startswith(exc.name):
            raise StepFailed("импорт модели", f"ядра ещё нет: модуль {MODEL_MODULE} не найден "
                             "(model/ пишется параллельно, см. model/README.md)") from exc
        raise StepFailed("импорт модели", f"ядро не импортируется: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — любая ошибка импорта ядра = провал шага
        raise StepFailed("импорт модели", f"{type(exc).__name__}: {exc}") from exc
    for name in ("build_payload", "validate"):
        if not callable(getattr(module, name, None)):
            raise StepFailed("импорт модели", f"в {MODEL_MODULE} нет функции {name}()")
    return module


def call_build_payload(build, *, live, previous, journal, fast: bool, release_history=None):
    """Зовёт `build_payload` с теми входами, которые она объявляет.

    Предложенный вход, которого ядро не принимает, — провал, а не молчание:
    иначе живые входы или журнал тихо не попали бы в выпуск.
    """
    offered = {"live": live, "previous": previous, "journal": journal, "fast": fast,
               "release_history": release_history}
    params = inspect.signature(build).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return build(**offered)
    kwargs = {name: value for name, value in offered.items() if name in params}
    if "with_slow" in params:
        kwargs["with_slow"] = not fast
    lost = [name for name in ("live", "previous", "journal", "release_history")
            if offered[name] is not None and name not in params]
    if lost:
        raise StepFailed("сборка выпуска", "build_payload() не принимает "
                         + ", ".join(lost) + " — вход не попал бы в выпуск; сверить model/README.md")
    required = [name for name, p in params.items()
                if p.default is inspect.Parameter.empty and name not in kwargs
                and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                               inspect.Parameter.KEYWORD_ONLY)]
    if required:
        raise StepFailed("сборка выпуска", "build_payload() требует "
                         + ", ".join(required) + " — конвейер их не знает; сверить model/README.md")
    return build(**kwargs)


def contract_problems(validate, payload) -> list[str]:
    """`validate(payload)` → список нарушений (терпит list | None | bool | str)."""
    try:
        result = validate(payload)
    except Exception as exc:  # noqa: BLE001 — упавшая проверка = нарушение
        return [f"validate() упал: {type(exc).__name__}: {exc}"]
    if result is None or result is True:
        return []
    if result is False:
        return ["validate() вернул False"]
    if isinstance(result, str):
        return [result] if result else []
    if isinstance(result, (list, tuple, set)):
        return [str(item) for item in result]
    return [f"validate() вернул {type(result).__name__}: {result!r}"[:300]]


def fact_problems(payload) -> list[str]:
    """Закрытые записи журнала и `data/facts/actuals.json` говорят одно.

    Журнал закрывает запись фактом один раз и дальше несёт её как есть: факт,
    исправленный потом в actuals.json, в запись сам не попадёт, а эталоны
    следующих полугодий уже возьмут новое значение. Расхождение — провал;
    исправление — переоткрыть запись (`python ops/publish.py --reopen`).
    """
    journal = payload.get("journal") if isinstance(payload, dict) else None
    entries = (journal.get("entries") if isinstance(journal, dict) else None) or []
    closed = [e for e in entries if isinstance(e, dict) and e.get("actual") is not None]
    if not closed:
        return []
    from model.facts import load_facts
    from model.journal import actuals_of
    actuals = actuals_of(load_facts())
    out = []
    for entry in closed:
        fact = actuals.get((entry.get("target"), entry.get("period")))
        if fact is None or abs(fact - entry["actual"]) > 1e-12:
            now = "факта нет" if fact is None else f"факт {fact:g}"
            out.append(f"запись {entry.get('id')} закрыта фактом {entry['actual']:g}, а в "
                       f"data/facts/actuals.json {now} — исправление факта: "
                       f"python ops/publish.py --reopen {entry.get('id')} --note \"<почему>\" "
                       "(ops/README.md, «Исправить факт»)")
    return out


def serialize(payload) -> str:
    """Компактный строгий JSON; нарушения — `StepFailed`."""
    try:
        compact = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":"))
    except (ValueError, TypeError) as exc:
        raise StepFailed("проверка контракта", f"выпуск не строгий JSON: {exc}") from exc
    size = len(compact.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise StepFailed("проверка контракта",
                         f"выпуск {size} байт при потолке {MAX_PAYLOAD_BYTES}")
    if not isinstance(payload, dict) or not (payload.get("meta") or {}).get("payload_sha256"):
        raise StepFailed("проверка контракта", "нет meta.payload_sha256 — сверка публикации "
                         "через боевую дверь невозможна")
    return compact


def _get(payload: dict, *path, default="—"):
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node or node[key] is None:
            return default
        node = node[key]
    return node


def headline(payload: dict, size: int) -> str:
    return (f"выпуск {payload['meta']['payload_sha256'][:12]} · {size / 1000:.0f} КБ · "
            f"дата оценки {_get(payload, 'meta', 'valuation_date')} · "
            f"цена {_get(payload, 'market', 'price')} ₽ ({_get(payload, 'market', 'price_status')}) · "
            f"медиана {_get(payload, 'headline', 'printed_central')} ₽ · "
            f"полоса {'–'.join(str(x) for x in _get(payload, 'headline', 'printed_band', default=['—']))} ₽ · "
            f"точка {_get(payload, 'fair_value', 'printed', 'central')} ₽ · "
            f"прогонов {_get(payload, 'headline', 'draws')}{' (быстрая)' if _get(payload, 'meta', 'fast') is True else ''}")


def with_header(model, payload):
    """Шапка семейства `family-header-v1` (common/README.md, раздел 2): `common.header.build` из `header_inputs`
    переходника `ops/common_adapter.py` — в корень выпуска сразу за `schema`; затем `model.seal` пересчитывает хэш и
    размер по выпуску с шапкой. Прежние блоки и числа не меняются. Модель без `seal()` шапку не получает (строка в
    журнале сборки)."""
    seal = getattr(model, "seal", None)
    if not callable(seal):
        print("шаг: шапка выпуска — пропущен: модель не даёт seal() (хэш после шапки не пересчитать)")
        return payload
    print("шаг: шапка выпуска")
    from common import header as H
    from common.cli import load_adapter
    try:
        adapter = load_adapter(ROOT)
        if adapter is None or not callable(getattr(adapter, "header_inputs", None)):
            raise LookupError("нет переходника ops/common_adapter.py с header_inputs()")
        head = H.build(adapter.header_inputs(payload))
    except Exception as exc:  # noqa: BLE001 — шапка не собралась = провал шага
        raise StepFailed("шапка выпуска", f"{type(exc).__name__}: {exc}") from exc
    out = {"schema": payload.get("schema"), "header": head, **{k: v for k, v in payload.items() if k != "schema"}}
    out["meta"] = dict(out.get("meta") or {})
    seal(out)
    for line in H.lines(head):
        print(f"  {line}")
    return out


def header_problems(payload) -> list[str]:
    """Шапка, если она есть, — по схеме слоя (`common.header.validate`)."""
    if not isinstance(payload, dict) or "header" not in payload:
        return []
    from common import header as H
    return [f"header: {p}" for p in H.validate(payload["header"])]


def _read(step: str, source: str | None, *, url_ok: bool = True):
    if not source:
        return None
    try:
        return read_json_source(source) if url_ok else strict_json(
            Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError, FetchError) as exc:
        raise StepFailed(step, f"{source}: {exc}") from exc


def build(args) -> str:
    previous = _read("чтение прошлого выпуска", args.previous)
    journal = _read("чтение журнала", args.journal, url_ok=False)
    if journal is not None and not isinstance(journal, (dict, list)):
        raise StepFailed("чтение журнала", f"{args.journal}: ожидался объект или список")
    history = _read("чтение истории выпусков", args.history, url_ok=False)
    if history is not None and not isinstance(history, list):
        raise StepFailed("чтение истории выпусков", f"{args.history}: ожидался список")

    live = None
    if args.live:
        from indicators.live import PriceUnavailable, collect_live, log_live, write_live
        print("шаг: сбор живых входов")
        try:
            live = collect_live(previous)
        except PriceUnavailable as exc:
            raise StepFailed("сбор живых входов", str(exc)) from exc
        log_live(live, indent="  ")
        write_live(live, DEFAULT_LIVE_OUT)
    elif args.live_file:
        live = _read("чтение живых входов", args.live_file, url_ok=False)

    print("шаг: импорт модели")
    model = load_model()
    print("шаг: сборка выпуска")
    try:
        payload = call_build_payload(model.build_payload, live=live, previous=previous,
                                     journal=journal, fast=args.fast, release_history=history)
    except StepFailed:
        raise
    except Exception as exc:  # noqa: BLE001 — любое исключение ядра = провал шага
        raise StepFailed("сборка выпуска", f"{type(exc).__name__}: {exc}") from exc

    payload = with_header(model, payload)
    print("шаг: проверка контракта")
    problems = contract_problems(model.validate, payload) + header_problems(payload)
    for problem in problems:
        print(f"  КОНТРАКТ: {problem}", file=sys.stderr)
    if problems:
        raise StepFailed("проверка контракта", f"нарушений {len(problems)}: {problems[0]}")
    compact = serialize(payload)

    print("шаг: журнал против фактов")
    problems = fact_problems(payload)
    for problem in problems:
        print(f"  ЖУРНАЛ: {problem}", file=sys.stderr)
    if problems:
        raise StepFailed("проверка журнала", f"нарушений {len(problems)}: {problems[0]}")

    print("шаг: запись")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(compact + "\n", encoding="utf-8", newline="\n")
    return f"готово: {headline(payload, len(compact.encode('utf-8')))} · записано {out}"


def check(path: str) -> str:
    print("шаг: чтение выпуска")
    payload = _read("чтение выпуска", path, url_ok=False)
    print("шаг: импорт модели")
    model = load_model()
    print("шаг: проверка контракта")
    problems = contract_problems(model.validate, payload) + header_problems(payload)
    for problem in problems:
        print(f"  КОНТРАКТ: {problem}", file=sys.stderr)
    if problems:
        raise StepFailed("проверка контракта", f"нарушений {len(problems)}: {problems[0]}")
    compact = serialize(payload)
    return f"готово: контракт цел — {headline(payload, len(compact.encode('utf-8')))}"


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Сборка выпуска YDEX")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--live", action="store_true", help="собрать живые входы и собрать выпуск")
    source.add_argument("--live-file", metavar="F", help="живые входы из файла indicators.live")
    source.add_argument("--book", action="store_true", help="без живых входов, на книге")
    source.add_argument("--check", metavar="FILE", help="только проверить готовый выпуск")
    parser.add_argument("--previous", metavar="PATH|URL", help="прошлый выпуск")
    parser.add_argument("--journal", metavar="PATH", help="журнал прогнозов (journal.json)")
    parser.add_argument("--history", metavar="PATH",
                        help="история выпусков ветки data (history.json): книга выпусков журнала")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="куда записать выпуск")
    parser.add_argument("--fast", action="store_true",
                        help="быстрая сборка (не выпуск): малая полоса, обратный DCF только для точки, без цен осей")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):          # «₽» и кириллица — на любой консоли (Windows: cp1251)
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        line = check(args.check) if args.check else build(args)
    except StepFailed as exc:
        report(f"ПРОВАЛ на шаге {exc.step}: {exc.reason}")
        return 1
    report(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
