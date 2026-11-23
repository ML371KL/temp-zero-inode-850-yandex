# -*- coding: utf-8 -*-
"""Манифест общего слоя, его сверка и приём слоя в модель.

    python -m common.layer manifest [--check]     # переписать (или сверить) common/MANIFEST.sha256 — у источника слоя

Манифест — строки `<sha256>  <путь от common/>` по всем файлам слоя, кроме самого манифеста и кэшей Python, по
алфавиту путей, концы строк LF. Состояние модели — `data/common_state.json` (`version`, `source`, `source_commit`,
`manifest_sha256`); его пишет только приём слоя (`python -m common.cli adopt-common`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

LAYER = Path(__file__).resolve().parent                 # каталог common/ этого дерева
ROOT = LAYER.parent                                     # корень дерева модели
MANIFEST_NAME = "MANIFEST.sha256"
VERSION_NAME = "VERSION"
STATE_REL = "data/common_state.json"
GITATTR_LINE = "common/** -text"
IGNORED_DIRS = {"__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}

# Что слой берёт у модели из переходника ops/common_adapter.py: имя → нужно ли вызывать
ADAPTER_REL = "ops/common_adapter.py"
ADAPTER_REQUIRED: dict[str, bool] = {
    "SLUG": False, "TICKERS": False, "COMMANDS": False, "facts_mapping": False,
    "header_inputs": True, "reanchor": True, "expected_path": True,
}
COMMAND_NAMES = ("state", "rederive", "quick", "band", "server-tests", "release", "report", "worlds", "adopt-common")
LAYER_COMMANDS = ("state", "report", "worlds", "adopt-common")   # их делает сам слой; остальные — COMMANDS переходника


class LayerError(Exception):
    """Отказ приёма или сверки слоя: текст — причина."""


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def layer_files(layer: Path) -> list[str]:
    """Файлы слоя (пути от common/, через «/»), без манифеста и кэшей Python, по алфавиту."""
    out = []
    for p in layer.rglob("*"):
        rel = p.relative_to(layer)
        if any(part in IGNORED_DIRS for part in rel.parts) or not p.is_file() or p.suffix in IGNORED_SUFFIXES:
            continue
        if rel.as_posix() == MANIFEST_NAME:
            continue
        out.append(rel.as_posix())
    return sorted(out)


def build_manifest(layer: Path) -> str:
    return "".join(f"{sha256_bytes((layer / f).read_bytes())}  {f}\n" for f in layer_files(layer))


def read_manifest(layer: Path) -> dict[str, str]:
    """Манифест слоя: путь → sha256."""
    p = layer / MANIFEST_NAME
    if not p.exists():
        raise LayerError(f"нет {MANIFEST_NAME} в {layer}")
    out: dict[str, str] = {}
    for n, line in enumerate(p.read_bytes().decode("utf-8").split("\n"), 1):
        if not line:
            continue
        sha, sep, rel = line.partition("  ")
        if not sep or len(sha) != 64 or not rel:
            raise LayerError(f"{MANIFEST_NAME}, строка {n}: не «<sha256>  <путь>»")
        out[rel] = sha
    return out


def manifest_sha256(layer: Path) -> str:
    return sha256_bytes((layer / MANIFEST_NAME).read_bytes())


def read_version(layer: Path) -> str:
    return (layer / VERSION_NAME).read_text(encoding="utf-8").strip()


def verify(layer: Path) -> list[str]:
    """Расхождения каталога слоя с его манифестом: файл изменён, файла нет, лишний файл."""
    try:
        man = read_manifest(layer)
    except LayerError as e:
        return [str(e)]
    bad = []
    for rel, sha in man.items():
        p = layer / rel
        if not p.is_file():
            bad.append(f"{rel}: назван в манифесте, файла нет")
        elif sha256_bytes(p.read_bytes()) != sha:
            bad.append(f"{rel}: sha256 не равен манифесту (файл слоя правили на месте?)")
    for rel in layer_files(layer):
        if rel not in man:
            bad.append(f"{rel}: лишний файл — его нет в манифесте")
    return bad


def read_state(root: Path) -> dict | None:
    p = root / STATE_REL
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def verify_state(root: Path) -> list[str]:
    """Слой модели стоит на своём состоянии: манифест = состоянию, версия = состоянию, файлы = манифесту."""
    layer = root / "common"
    st = read_state(root)
    if st is None:
        return [f"нет {STATE_REL}: слой не принят командой adopt-common"]
    bad = []
    if (layer / MANIFEST_NAME).exists() and manifest_sha256(layer) != st.get("manifest_sha256"):
        bad.append(f"sha256 {MANIFEST_NAME} не равен {STATE_REL} → manifest_sha256")
    if (layer / VERSION_NAME).exists() and read_version(layer) != st.get("version"):
        bad.append(f"{VERSION_NAME} {read_version(layer)} ≠ версии состояния {st.get('version')}")
    return bad + verify(layer)


# ----------------------------------------------------------------------------------------------- переходник модели
def adapter_gaps(adapter: Any) -> list[str]:
    """Чего не хватает переходнику модели: отсутствующие имена, невызываемые функции, команды без строки COMMANDS."""
    if adapter is None:
        return [f"нет переходника {ADAPTER_REL}: нужны {', '.join(ADAPTER_REQUIRED)}"]
    gaps = []
    for name, must_call in ADAPTER_REQUIRED.items():
        if not hasattr(adapter, name):
            gaps.append(f"{ADAPTER_REL}: нет `{name}`")
        elif must_call and not callable(getattr(adapter, name)):
            gaps.append(f"{ADAPTER_REL}: `{name}` должна быть функцией")
    commands = getattr(adapter, "COMMANDS", None)
    if isinstance(commands, dict):
        missing = [c for c in COMMAND_NAMES if c not in LAYER_COMMANDS and c not in commands]
        if missing:
            gaps.append(f"{ADAPTER_REL}: в COMMANDS нет команд {', '.join(missing)}")
        unknown = sorted(set(commands) - set(COMMAND_NAMES))
        if unknown:
            gaps.append(f"{ADAPTER_REL}: в COMMANDS неизвестные команды {', '.join(unknown)}")
    elif commands is not None:
        gaps.append(f"{ADAPTER_REL}: COMMANDS должен быть словарём «команда → вызов»")
    return gaps


def gitattributes_gap(root: Path) -> list[str]:
    p = root / ".gitattributes"
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    return [] if GITATTR_LINE in (ln.strip() for ln in lines) else [f".gitattributes: нет строки `{GITATTR_LINE}`"]


# ----------------------------------------------------------------------------------------------- приём слоя
def _git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def source_identity(src: Path) -> tuple[str, str | None, bool]:
    """Источник слоя: имя (адрес origin, иначе имя каталога репозитория), коммит, есть ли незакоммиченные правки."""
    top = _git(src, "rev-parse", "--show-toplevel")
    if not top:
        return src.parent.name, None, False
    name = _git(src, "remote", "get-url", "origin") or Path(top).name
    commit = _git(src, "rev-parse", "HEAD")
    dirty = bool(_git(src, "status", "--porcelain", "--", "."))
    return name, (commit[:12] if commit else None), dirty


def adopt(source: Path, root: Path, dry_run: bool = False, adapter: Any = None,
          say: Callable[[str], None] = print) -> int:
    """Приём слоя: каталог common/ источника → common/ модели побайтово, сверка манифеста, состояние модели.
    0 — принято (или сухой прогон без отказа), 1 — отказ."""
    source = source.resolve()
    target = (root / "common").resolve()
    try:
        bad = verify(source)
        if bad:
            raise LayerError("источник не равен своему манифесту:\n  " + "\n  ".join(bad))
        man = read_manifest(source)
        version = read_version(source)
        name, commit, dirty = source_identity(source)
        if dirty and not dry_run:
            raise LayerError(f"в источнике {source} есть незакоммиченные правки слоя — приём только с коммита")
    except (LayerError, OSError) as e:
        say(f"ОТКАЗ: {e}")
        return 1
    same = source == target
    have = {} if not target.exists() else {f: sha256_bytes((target / f).read_bytes()) for f in layer_files(target)}
    add = sorted(f for f in man if f not in have)
    change = sorted(f for f in man if f in have and have[f] != man[f])
    drop = sorted(f for f in have if f not in man)
    old_manifest = (target / MANIFEST_NAME).read_bytes() if (target / MANIFEST_NAME).exists() else None
    new_manifest = (source / MANIFEST_NAME).read_bytes()
    state = {"version": version, "source": name, "source_commit": commit,
             "manifest_sha256": sha256_bytes(new_manifest)}
    prev = read_state(root)
    if prev and {k: prev.get(k) for k in ("version", "manifest_sha256")} == \
            {k: state[k] for k in ("version", "manifest_sha256")}:
        state = prev                    # тот же слой — источник и коммит прежние (повтор приёма — неподвижная точка)
    mode ="сухой прогон: ничего не пишется" if dry_run else "приём"
    say(f"слой {version} из {name}" + (f" @ {commit}" if commit else " (не git)") + f" — {mode}")
    if dirty:
        say("  ВНИМАНИЕ: в источнике есть незакоммиченные правки слоя")
    if same:
        say("  источник — каталог common/ этого дерева: файлы не копируются")
    say(f"  файлы: новых {len(add)}, изменённых {len(change)}, лишних (удалить) {len(drop)}"
        + (", манифест меняется" if old_manifest != new_manifest else ", манифест прежний"))
    for tag, files in (("+", add), ("~", change), ("-", drop)):
        for f in files:
            say(f"    {tag} {f}")
    say(f"  состояние {STATE_REL}: " + ("прежнее" if prev == state else json.dumps(state, ensure_ascii=False)))
    if not dry_run:
        if not same:
            target.mkdir(parents=True, exist_ok=True)
            for f in add + change:
                (target / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / f, target / f)
            for f in drop:
                (target / f).unlink()
            shutil.copyfile(source / MANIFEST_NAME, target / MANIFEST_NAME)
        bad = verify(target)
        if bad:
            say("ОТКАЗ: после копирования слой не равен манифесту:\n  " + "\n  ".join(bad))
            return 1
        p = root / STATE_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        if prev != state:
            p.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        say(f"  принято: слой {version}, манифест {state['manifest_sha256'][:12]}")
    gaps = adapter_gaps(adapter) + gitattributes_gap(root)
    say("переходнику модели поправить: " + ("ничего" if not gaps else ""))
    for g in gaps:
        say(f"  - {g}")
    return 0


# ----------------------------------------------------------------------------------------------- запуск
def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="манифест общего слоя")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest", help="переписать common/MANIFEST.sha256 (у источника слоя)")
    m.add_argument("--check", action="store_true", help="только сверить")
    a = ap.parse_args(argv)
    text = build_manifest(LAYER)
    p = LAYER / MANIFEST_NAME
    if a.check:
        ok = p.exists() and p.read_bytes() == text.encode("utf-8")
        print("манифест = файлам слоя" if ok else "манифест НЕ равен файлам слоя")
        return 0 if ok else 1
    p.write_bytes(text.encode("utf-8"))
    print(f"{MANIFEST_NAME}: {len(text.splitlines())} файлов, sha256 {sha256_bytes(text.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
