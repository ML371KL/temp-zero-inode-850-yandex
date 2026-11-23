"""Прежние книги 1.0–1.3 — файлами в `tests/fixtures/books/<версия>/assumptions.yaml`, а не из истории git.

Публичный репозиторий кода — один корневой коммит без истории (правило выкладки), поэтому тесты «без ключей —
прежняя книга бит в бит» читают копии книг из своих коммитов прежней истории. Копии — байт в байт: sha256 файла и
хэш блоба git (sha1 от «blob <длина>\\0» + байты) — как у `data/assumptions/assumptions.yaml` в этом коммите;
сверка — `tests/test_book_13.py::test_old_book_fixtures_are_the_blobs_of_their_commits`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

BOOKS = Path(__file__).resolve().parent / "fixtures" / "books"

# Версия книги → (коммит прежней истории, хэш блоба git, sha256 файла).
PINS: dict[str, tuple[str, str, str]] = {
    "1.0": ("913ee3e", "ae7cef51dcdbba28c9912b06e74b0c4ce11cec8f",
            "ce6fb6e43e406b009fd41ac1f15366829be96bbff3c52b631ab6aa02811abedf"),
    "1.1": ("079bad5", "30256933c9cfcf46b066adfc74274096fd915a0d",
            "31ab70b5f6934e000379fe57d7f271f968d8a7006e2663ac410138a46b8ddda3"),
    "1.2": ("c150e62", "983c22f02acd73de69728b8444f6d54caceb1d99",
            "80fd0fd54e7ced29e296e50b3a5c243a6571dedae61c514756819867752aaed3"),
    "1.3": ("469d33d", "5018e23af0ae1546cfed3bf2f51bea5e37b79727",
            "e298a1f9abef31e11116da2120ba34f02c0ae9de67f197f4be44d55e8d63b89f"),
}


def path(version: str) -> Path:
    if version not in PINS:
        raise KeyError(f"нет копии книги {version}: есть {sorted(PINS)}")
    return BOOKS / version / "assumptions.yaml"


def blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def old_book(version: str) -> dict:
    """Книга прежней версии из её копии."""
    return yaml.safe_load(path(version).read_text(encoding="utf-8"))
