"""Разбор ответов MOEX ISS на сохранённых реальных ответах (YDEX, живой прогон 23.11.2026).

Фикстуры — `tests/fixtures/iss/`, адреса и хэши — `tests/fixtures/live_sources.json`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import iss  # noqa: E402
from indicators.http import Response  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
TODAY = date(2026, 11, 23)
YEAR_AGO = date(2025, 11, 23)


def load(name: str) -> dict:
    return json.loads((FIX / "iss" / name).read_text(encoding="utf-8"))


def manifest() -> dict:
    return json.loads((FIX / "live_sources.json").read_text(encoding="utf-8"))


def fixture_getter(calls: list | None = None, overrides: dict | None = None):
    """Подмена `indicators.http.fetch`: отдаёт фикстуру по имени ответа."""
    def getter(url, *, source, name, **kw):
        if calls is not None:
            calls.append({"url": url, "source": source, "name": name, **kw})
        if overrides and name in overrides:
            value = overrides[name]
            if isinstance(value, Exception):
                raise value
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
        else:
            body = (FIX / source / name).read_bytes()
        return Response(url=url, status=200, body=body, fetched_at="2026-11-23T17:57:38+00:00")
    return getter


# ------------------------------------------------------------------ котировки

def test_quotes_garbage_is_an_error():
    with pytest.raises(iss.IssError):
        iss.parse_quotes({"error": "html instead of json"})


def test_quotes_of_the_live_run_give_the_last_trade_with_its_day():
    """Запись живого прогона 23.11.2026 (20:57 МСК, идут торги): цена последней
    сделки, дата — по срезу SYSTIME, время — время сделки."""
    quotes = iss.fetch_quotes(getter=fixture_getter())
    assert set(quotes) == {"YDEX"}
    q = quotes["YDEX"]
    assert (q["price"], q["date"], q["time"], q["kind"]) == (3281.0, "2026-11-23", "20:42:36", "last")
    assert (q["source"], q["isin"], q["trading_status"]) == ("ISS TQBR", "RU000A107T19", "T")


def test_quotes_out_of_session_give_the_previous_price_and_day():
    payload = load("quotes_tqbr.json")
    cols = payload["marketdata"]["columns"]
    payload["marketdata"]["data"][0][cols.index("LAST")] = None
    q = iss.parse_quotes(payload)["YDEX"]
    assert (q["price"], q["date"], q["time"], q["kind"]) == (3277.5, "2026-11-20", None, "prev")


def test_history_of_the_live_run_is_a_year_of_ydex_closes():
    calls = []
    rows = iss.fetch_price_history("YDEX", start=YEAR_AGO, till=TODAY, getter=fixture_getter(calls))
    assert [c["name"] for c in calls] == ["history_YDEX_0.json", "history_YDEX_100.json",
                                          "history_YDEX_200.json"]
    assert len(rows) == 252
    assert rows[0] == {"date": "2025-11-24", "close": 4090.0}
    assert rows[-1] == {"date": "2026-11-20", "close": 3291.0}
    assert [r["date"] for r in rows] == sorted({r["date"] for r in rows})

def test_fixtures_are_the_recorded_raw_responses():
    """Фикстуры ISS и ЦБ — байт в байт сырые ответы из манифеста (git их не
    нормализует: `tests/fixtures/{iss,cbr}/.gitattributes`)."""
    for name, meta in manifest().items():
        body = (FIX / name).read_bytes()
        assert (len(body), hashlib.sha256(body).hexdigest()) == (meta["bytes"], meta["sha256"]), name


# ------------------------------------------------------------------ история

def test_history_close_is_official_close_then_last_trade():
    page = {"history": {"columns": ["TRADEDATE", "LEGALCLOSEPRICE", "CLOSE", "VOLUME"],
                        "data": [["2026-11-19", 3353, 3355.5, 1],
                                 ["2026-11-20", None, 3291, 1],
                                 ["2026-11-21", None, None, 0]]}}
    rows, cursor = iss.parse_history_page(page)
    assert rows == [{"date": "2026-11-19", "close": 3353.0},
                    {"date": "2026-11-20", "close": 3291.0}]
    assert cursor is None


def test_history_endless_cursor_is_cut():
    endless = {"history": {"columns": ["TRADEDATE", "LEGALCLOSEPRICE", "CLOSE", "VOLUME"],
                           "data": [["2026-11-20", 3291, 3291, 1]]},
               "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"],
                                  "data": [[0, 10 ** 9, 100]]}}

    def getter(url, *, source, name, **kw):
        return Response(url, 200, json.dumps(endless).encode(), "2026-11-23T17:57:38+00:00")

    with pytest.raises(iss.IssError):
        iss.fetch_price_history("YDEX", start=YEAR_AGO, till=TODAY, getter=getter)


# ------------------------------------------------------------------ кривая

def test_zcyc_nodes_are_fractions_on_latest_date():
    curve = iss.fetch_zcyc(getter=fixture_getter())
    assert curve["as_of"] == "2026-11-23"
    assert curve["time"] == "18:54:59"
    assert curve["nodes"] == {"1": 0.141189, "3": 0.15672, "5": 0.164679, "10": 0.170835}
    assert curve["source"] == "ISS zcyc"


def test_zcyc_in_wrong_units_is_refused():
    payload = load("zcyc.json")
    cols = payload["yearyields"]["columns"]
    for row in payload["yearyields"]["data"]:
        row[cols.index("value")] = row[cols.index("value")] / 100  # доли вместо процентов
    with pytest.raises(iss.IssError, match="единицы"):
        iss.parse_zcyc(payload)


def test_zcyc_missing_node_is_refused():
    payload = load("zcyc.json")
    cols = payload["yearyields"]["columns"]
    payload["yearyields"]["data"] = [r for r in payload["yearyields"]["data"]
                                     if r[cols.index("period")] != 10]
    with pytest.raises(iss.IssError, match="10"):
        iss.parse_zcyc(payload)


def test_zcyc_takes_only_the_latest_date():
    payload = load("zcyc.json")
    old = copy.deepcopy(payload["yearyields"]["data"])
    cols = payload["yearyields"]["columns"]
    for row in old:
        row[cols.index("tradedate")] = "2026-11-20"
        row[cols.index("value")] = 20.0
    payload["yearyields"]["data"] = old + payload["yearyields"]["data"]
    assert iss.parse_zcyc(payload)["nodes"]["5"] == 0.164679

