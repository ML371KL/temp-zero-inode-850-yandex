"""Вход тактов к сборщикам (`indicators.collect`) на записях живого прогона 23.11.2026, без сети.

Коды `daily` — те, что читает `ops/run.sh`: 0 — собрано; 3 — записано, но тревога (отказ наблюдаемого источника или
запасная цена); 1 — цены нет и заменить нечем; 64 — проба тревоги с чужим источником.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import collect  # noqa: E402
from indicators.http import FetchError, Response  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
TODAY = date(2026, 11, 23)
RECORDED = {("cbr", "key_rate_soap.xml"): "cbr/key_rate_soap_2026-11-23.xml"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("YANDEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("YANDEX_RAW_DIR", raising=False)
    return tmp_path / "state"


@pytest.fixture
def calls(monkeypatch):
    """Подмена сети: ответы — записи живого прогона; `fail` — имена ответов, которые отказывают."""
    class Seen(list):
        fail: dict[str, int]

    seen = Seen()
    seen.fail = fail = {}

    def fetch(url, *, source, name, **kw):
        seen.append(name)
        if fail.get(name, 0):
            fail[name] -= 1
            raise FetchError(url, 503, "Service Unavailable")
        body = (FIX / RECORDED.get((source, name), f"{source}/{name}")).read_bytes()
        return Response(url=url, status=200, body=body, fetched_at="2026-11-23T17:57:38+00:00")

    monkeypatch.setattr(collect.http, "fetch", fetch)
    return seen


def read_live(state: Path) -> dict:
    return json.loads((state / "live" / "live.json").read_text(encoding="utf-8"))


def put_previous(state: Path, value=3291.0, day="2026-11-20") -> None:
    (state / "release").mkdir(parents=True, exist_ok=True)
    release = {"live": {"price": {"last_accepted": {"value": value, "date": day}}}}
    (state / "release" / "latest.json").write_text(json.dumps(release), encoding="utf-8")


def test_daily_on_the_recorded_run_writes_the_live_inputs(state, calls):
    assert collect.cmd_daily(today=TODAY) == 0
    live = read_live(state)
    assert (live["price"]["value"], live["price"]["date"], live["price"]["status"]) == (3281.0, "2026-11-23", "live")
    assert live["curve"]["as_of"] == "2026-11-23" and live["curve"]["nodes"]["10"] == 0.170835
    assert (live["key_rate"]["value"], live["key_rate"]["date"]) == (0.14, "2026-11-23")
    assert len(live["price_history"]) == 252
    assert live["errors"] == {} and live["bonds"] == [], "эмитент облигаций не задан — не опрашивается и не отказ"


def test_a_failed_watched_source_is_an_alarm_after_the_inputs_are_written(state, calls, capsys):
    calls.fail["zcyc.json"] = 1
    assert collect.cmd_daily(today=TODAY) == collect.ALARM_EXIT
    assert read_live(state)["curve"] is None
    assert "ОШИБКА: curve" in capsys.readouterr().out


def test_the_evening_retry_takes_the_better_attempt(state, calls):
    calls.fail["key_rate_soap.xml"] = 1
    calls.fail["key_rate_page.html"] = 1
    pauses = []
    assert collect.cmd_daily(retry=True, today=TODAY, sleep=pauses.append) == 0
    assert pauses == [collect.RETRY_PAUSE_S]
    assert read_live(state)["key_rate"]["value"] == 0.14


def test_the_alarm_probe_does_not_touch_the_source(state, calls):
    assert collect.cmd_daily(simulate=("curve",), today=TODAY) == collect.ALARM_EXIT
    assert "zcyc.json" not in calls
    assert "ПРОБА ТРЕВОГИ" in read_live(state)["errors"]["curve"]


def test_the_alarm_probe_with_a_foreign_source_is_code_64(state, calls):
    assert collect.cmd_daily(simulate=("bonds",), today=TODAY) == 64
    assert calls == []


def test_no_price_with_a_previous_release_is_the_fallback_and_an_alarm(state, calls):
    put_previous(state)
    assert collect.cmd_daily(simulate=("quotes", "price_history"), today=TODAY) == collect.ALARM_EXIT
    price = read_live(state)["price"]
    assert (price["status"], price["value"], price["date"]) == ("fallback", 3291.0, "2026-11-20")


def test_no_price_and_no_previous_release_is_a_failure(state, calls):
    assert collect.cmd_daily(simulate=("quotes", "price_history"), today=TODAY) == 1
    assert not (state / "live" / "live.json").exists()


def test_the_previous_release_is_the_price_reference(state, calls):
    put_previous(state, value=3270.0, day="2026-11-23")
    assert collect.cmd_daily(today=TODAY) == 0
    assert read_live(state)["price"]["reference"]["value"] == 3270.0


def test_the_raw_archive_lives_in_the_state_directory(state, monkeypatch):
    assert collect.raw_dir() == state / "indicators" / "raw"
    monkeypatch.setenv("YANDEX_RAW_DIR", str(state / "elsewhere"))
    assert collect.raw_dir() == state / "elsewhere"


def test_health_follows_freshness_and_price(state, calls):
    assert collect.cmd_health(today=TODAY) == 1, "живых входов нет"
    assert collect.cmd_daily(today=TODAY) == 0
    assert collect.cmd_health(today=TODAY) == 0
    assert collect.cmd_health(today=date(2026, 11, 27)) == 0
    assert collect.cmd_health(today=date(2026, 11, 28)) == 1, "старше 4 дней"
    put_previous(state)
    collect.cmd_daily(simulate=("quotes", "price_history"), today=TODAY)
    assert collect.cmd_health(today=TODAY) == 1, "цена запасная"


def test_prune_removes_only_days_older_than_the_limit(state):
    raw = state / "indicators" / "raw"
    for day in ("2025-10-18", "2025-10-19", "2026-11-23"):
        (raw / "iss" / day).mkdir(parents=True)
        (raw / "iss" / day / "x.json").write_text("{}", encoding="utf-8")
    assert collect.prune_raw(400, today=TODAY) == ["iss/2025-10-18"]
    assert sorted(p.name for p in (raw / "iss").iterdir()) == ["2025-10-19", "2026-11-23"]


def test_status_names_the_archive_and_the_inputs(state, calls, capsys):
    collect.cmd_daily(today=TODAY)
    os.makedirs(state / "indicators" / "raw" / "iss" / "2026-11-23", exist_ok=True)
    assert collect.cmd_status() == 0
    out = capsys.readouterr().out
    assert "сырой архив iss: 1 дн." in out and "цена 3281.0 ₽ от 2026-11-23 (live)" in out
