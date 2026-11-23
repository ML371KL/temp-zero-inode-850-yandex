# -*- coding: utf-8 -*-
"""Набор команд модели семейства 850 — одинаковые имена у всех моделей.

    python -m common.cli state                         # что есть: слой, миры, книга, выпуск
    python -m common.cli worlds [--version V] [--dry-run | --branch | --check] [...]   # приём миров
    python -m common.cli adopt-common --source <каталог common/ источника> [--dry-run]  # приём общего слоя
    python -m common.cli report --period П [--dry-run] [--out var/report-П]          # приём отчёта (common.report)
    python -m common.cli rederive | quick | band | server-tests | release [аргументы команды модели]

`state`, `report`, `worlds`, `adopt-common` делает слой (`report` — через `reanchor` и `expected_path` переходника). Остальные команды модель называет в словаре `COMMANDS` переходника
`ops/common_adapter.py`: значение — функция `f(argv) -> код`, список аргументов команды модели (`{python}` —
интерпретатор этого запуска; аргументы после имени команды дописываются в конец; запуск — из корня дерева) или строка
«почему не подключено» (код выхода 2).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from common import layer as L

NOT_CONNECTED = 2
WORLDS_ENGINE_REL = "ops/tools/adopt_worlds.py"
WORLDS_STATE_REL = "data/assumptions/worlds_state.json"


def load_adapter(root: Path = L.ROOT) -> Any:
    """Переходник модели `ops/common_adapter.py` или None, если его нет."""
    p = root / L.ADAPTER_REL
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location("common_adapter", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str:
    return L.sha256_bytes(p.read_bytes())


# ----------------------------------------------------------------------------------------------- state
def _section(say: Callable[[str], None], title: str, fn: Callable[[], str]) -> None:
    try:
        say(f"{title}: {fn()}")
    except Exception as e:  # noqa: BLE001 — состояние печатается всегда: раздел без данных — «нет» с причиной
        say(f"{title}: нет — {e}")


def _model_line(adapter: Any) -> str:
    if adapter is None:
        raise LookupError(f"нет переходника {L.ADAPTER_REL}")
    tickers = ", ".join(getattr(adapter, "TICKERS", ()) or ())
    return f"{adapter.SLUG} ({tickers})"


def _layer_line(root: Path) -> str:
    layer = root / "common"
    st = L.read_state(root)
    head = f"{L.read_version(layer)}, манифест {L.manifest_sha256(layer)[:12]}"
    if st is None:
        return head + f"; состояния {L.STATE_REL} нет — слой не принят командой adopt-common"
    bad = L.verify_state(root)
    src = f"из {st.get('source')} @ {st.get('source_commit')}"
    return head + f" {src}; " + ("сторож: чисто" if not bad else "РАСХОЖДЕНИЯ: " + "; ".join(bad))


def _worlds_line(root: Path) -> str:
    p = root / WORLDS_STATE_REL
    if not p.exists():
        raise LookupError(f"нет {WORLDS_STATE_REL}: миры не приняты")
    st = json.loads(p.read_text(encoding="utf-8"))
    eng = root / WORLDS_ENGINE_REL
    eng_ok = "движок = образцу состояния" if eng.exists() and _sha(eng) == st.get("engine_sha256") else \
        "ДВИЖОК НЕ РАВЕН образцу состояния"
    return (f"{st.get('version')} (запись на {st.get('record_asof')}, кривая {st.get('curve_date')}, "
            f"запись {str(st.get('record_sha256'))[:12]}, источник @ {st.get('source_commit')}); {eng_ok}")


def _release(root: Path, adapter: Any) -> tuple[Path, dict]:
    rel = getattr(adapter, "RELEASE_PATH", None) if adapter is not None else None
    if not rel:
        raise LookupError("переходник не называет файл выпуска (RELEASE_PATH)")
    p = root / rel
    if not p.exists():
        raise LookupError(f"выпуск не собран ({rel} нет)")
    return p, json.loads(p.read_text(encoding="utf-8"))


def _header(root: Path, adapter: Any) -> dict:
    """Шапка из выпуска; у выпуска без шапки — собранная слоем из `header_inputs` переходника."""
    p, payload = _release(root, adapter)
    if isinstance(payload.get("header"), dict):
        return payload["header"]
    from common import header as H
    return H.build(adapter.header_inputs(payload))


def _header_lines(root: Path, adapter: Any) -> str:
    from common import header as H
    p, payload = _release(root, adapter)
    h = _header(root, adapter)
    where = "из выпуска" if isinstance(payload.get("header"), dict) else "выпуск без шапки — собрана из header_inputs"
    bad = H.validate(h)
    return f"{where}; " + ("схема цела" if not bad else "НАРУШЕНИЯ: " + "; ".join(bad[:3])) + "\n" + \
        "\n".join(H.lines(h))


def _release_line(root: Path, adapter: Any) -> str:
    p, payload = _release(root, adapter)
    meta = payload.get("meta") or {}
    return f"{p.relative_to(root).as_posix()}, схема {payload.get('schema')}, собран {meta.get('generated_at')}"


def _book_line(root: Path, adapter: Any) -> str:
    h = _header(root, adapter)
    b, f = h.get("book") or {}, h.get("facts") or {}
    return f"{b.get('version')} от {b.get('date')}, якорь {f.get('anchor_period')}"


def _status_lines(root: Path, adapter: Any) -> str:
    h = _header(root, adapter)
    st = h.get("status") or {}
    parts = [f"{st.get('level')}" + (f" ({'; '.join(st.get('reasons') or [])})" if st.get("reasons") else "")]
    flags = [x.get("title") or x.get("name") for x in h.get("flags") or []]
    parts.append("флаги: " + (", ".join(flags) if flags else "нет"))
    dl = h.get("deadlines") or []
    parts.append("ближний срок: " + (f"{dl[0].get('date')} {dl[0].get('what')}" if dl else "нет"))
    nxt = (h.get("report") or {}).get("next") or {}
    parts.append("следующий отчёт: " + (f"{nxt.get('date')} {nxt.get('title')}" if nxt else "нет"))
    return "; ".join(parts)


def _commands_line(adapter: Any) -> str:
    cmds = getattr(adapter, "COMMANDS", {}) if adapter is not None else {}
    out = []
    for c in L.COMMAND_NAMES:
        if c in L.LAYER_COMMANDS:
            out.append(f"{c} — слой")
        else:
            v = cmds.get(c) if isinstance(cmds, dict) else None
            out.append(f"{c} — " + ("подключена" if v is not None and not isinstance(v, str)
                                    else f"не подключено: {v or 'нет в COMMANDS'}"))
    return "; ".join(out)


def cmd_state(root: Path, adapter: Any, say: Callable[[str], None]) -> int:
    _section(say, "модель", lambda: _model_line(adapter))
    _section(say, "слой", lambda: _layer_line(root))
    _section(say, "миры", lambda: _worlds_line(root))
    _section(say, "выпуск", lambda: _release_line(root, adapter))
    _section(say, "книга", lambda: _book_line(root, adapter))
    _section(say, "шапка", lambda: _header_lines(root, adapter))
    _section(say, "состояние", lambda: _status_lines(root, adapter))
    _section(say, "переходник", lambda: "; ".join(L.adapter_gaps(adapter)) or "полный")
    _section(say, "команды", lambda: _commands_line(adapter))
    return 0


# ----------------------------------------------------------------------------------------------- worlds
def cmd_worlds(root: Path, argv: list[str], say: Callable[[str], None]) -> int:
    """Приём миров движком модели; без --version (и не --check) — последняя версия индекса источника."""
    engine = root / WORLDS_ENGINE_REL
    if not engine.exists():
        say(f"не подключено: нет движка миров {WORLDS_ENGINE_REL}")
        return NOT_CONNECTED
    args = list(argv)
    if "--version" not in args and "--check" not in args:
        spec = importlib.util.spec_from_file_location("adopt_worlds_engine", engine)
        eng = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(eng)
        src = args[args.index("--source") + 1] if "--source" in args else None
        try:
            latest = eng.Source(eng.find_source(src)).index["latest"]
        except Exception as e:  # noqa: BLE001
            say(f"ОТКАЗ: версия миров не названа (--version), а индекс источника не читается: {e}")
            return 1
        say(f"версия миров не названа: последняя по индексу источника — {latest}")
        args = ["--version", str(latest), *args]
    return subprocess.run([sys.executable, "-B", str(engine), *args], cwd=root).returncode


# ----------------------------------------------------------------------------------------------- команды модели
def run_model_command(name: str, argv: list[str], root: Path, adapter: Any, say: Callable[[str], None]) -> int:
    cmds = getattr(adapter, "COMMANDS", None) if adapter is not None else None
    if not isinstance(cmds, dict):
        say(f"не подключено: нет переходника {L.ADAPTER_REL} или в нём нет COMMANDS")
        return NOT_CONNECTED
    v = cmds.get(name)
    if v is None:
        say(f"не подключено: команды `{name}` нет в COMMANDS переходника")
        return NOT_CONNECTED
    if isinstance(v, str):
        say(f"не подключено: {v}")
        return NOT_CONNECTED
    if callable(v):
        return int(v(list(argv)) or 0)
    if isinstance(v, (list, tuple)):
        cmd = [sys.executable if a == "{python}" else str(a) for a in v] + list(argv)
        return subprocess.run(cmd, cwd=root).returncode
    say(f"не подключено: значение COMMANDS[{name!r}] — не функция, не список и не строка")
    return NOT_CONNECTED


# ----------------------------------------------------------------------------------------------- запуск
USAGE = "python -m common.cli {" + ",".join(L.COMMAND_NAMES) + "} [аргументы]"


def main(argv: list[str] | None = None, root: Path = L.ROOT, adapter: Any = "load",
         say: Callable[[str], None] | None = None) -> int:
    if say is None:
        sys.stdout.reconfigure(encoding="utf-8")
        say = lambda line="": print(line, flush=True)  # noqa: E731
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        say(USAGE)
        say(__doc__.strip())
        return 0 if argv else 2
    name, rest = argv[0], argv[1:]
    if name not in L.COMMAND_NAMES:
        say(f"неизвестная команда `{name}`; {USAGE}")
        return 2
    if adapter == "load":
        try:
            adapter = load_adapter(root)
        except Exception as e:  # noqa: BLE001 — сломанный переходник не роняет state
            say(f"переходник {L.ADAPTER_REL} не загружается: {e}")
            adapter = None
    if name == "state":
        return cmd_state(root, adapter, say)
    if name == "worlds":
        return cmd_worlds(root, rest, say)
    if name == "report":
        from common import report as R
        return R.main(rest, root, adapter, say)
    if name == "adopt-common":
        import argparse
        ap = argparse.ArgumentParser(prog="python -m common.cli adopt-common")
        ap.add_argument("--source", required=True, help="каталог common/ источника слоя")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(rest)
        return L.adopt(Path(a.source), root, dry_run=a.dry_run, adapter=adapter, say=say)
    return run_model_command(name, rest, root, adapter, say)


if __name__ == "__main__":
    sys.exit(main())
