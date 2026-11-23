# -*- coding: utf-8 -*-
"""Лист фактов слоя на маленьком синтетическом листе (эмитент выдуман, числа — не отчётность)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from common import facts_sheet as F

SHA = "a" * 64
DOCS = (
    "doc_id,title,issuer_url,published,sha256\n"
    f"r1,Отчёт за полугодие,https://example.org/r1.pdf,2030-08-01,{SHA}\n"
    f"x1,Таблицы к отчёту,https://example.org/x1.xlsx,2030-08-01,{'b' * 64}\n"
)
SHEET = (
    "metric,segment,period,value,unit,doc_id,page,note\n"
    "revenue,,2030H1,100.5,bn_rub,r1,12,\n"
    "revenue,alpha,2030H1,60,bn_rub,r1,13,сегмент\n"
    "revenue,beta,2030H1,40.5,bn_rub,x1,Сегменты!C7,\n"
    "revenue,,2030Q1,48,bn_rub,x1,Кварталы!B2,ряд прошлого периода\n"
    "shares,,2030H1,350,mln_shares,r1,40,\n"
)
MAPPING = {
    "anchor.json": {
        "period": "2030H1",
        "revenue": {"group": F.Fact("revenue"), "alpha": F.Fact("revenue", "alpha"), "beta": F.Fact("revenue", "beta")},
        "revenue_q1": F.Fact("revenue", period="2030Q1"),
        "other": lambda get: str(get("revenue") - get("revenue", "alpha") - get("revenue", "beta")),
        "src": {"revenue": F.Ref("revenue")},
    },
    "shares.json": {"shares": F.Fact("shares")},
}
EXPECTED_ANCHOR = (
    '{\n "period": "2030H1",\n "revenue": {\n  "group": 100.5,\n  "alpha": 60,\n  "beta": 40.5\n },\n'
    ' "revenue_q1": 48,\n "other": "0.0",\n "src": {\n  "revenue": {\n   "doc_id": "r1",\n   "page": "12"\n  }\n'
    ' }\n}\n'
)


def write(tmp_path, docs=DOCS, sheet=SHEET, name="2030H1.csv"):
    d = tmp_path / "sheets"
    d.mkdir(exist_ok=True)
    (d / "documents.csv").write_bytes(docs.encode("utf-8"))
    (d / name).write_bytes(sheet.encode("utf-8"))
    return d


def test_a_good_sheet_loads(tmp_path):
    d = write(tmp_path)
    s = F.load_sheet(d, "2030H1")
    assert s.period == "2030H1" and len(s.rows) == 5
    assert s.get("revenue", "beta") == Decimal("40.5")
    assert s.row("revenue", period="2030Q1").page == "Кварталы!B2"
    assert F.check_dir(d) == [] and F.sheet_periods(d) == ["2030H1"]


def test_the_facts_are_built_from_the_sheet_byte_for_byte(tmp_path):
    s = F.load_sheet(write(tmp_path), "2030H1")
    out = F.to_facts(s, MAPPING)
    assert list(out) == ["anchor.json", "shares.json"]
    assert out["anchor.json"] == EXPECTED_ANCHOR
    assert out["shares.json"] == '{\n "shares": 350\n}\n'
    facts = tmp_path / "facts"
    F.write_facts(out, facts)
    assert F.check_built(s, MAPPING, facts) == []
    (facts / "shares.json").write_bytes(b'{\n "shares": 351\n}\n')
    assert F.check_built(s, MAPPING, facts) == ["shares.json: не равен сборке из листа"]
    assert F.to_facts(s, {}) == {}


def test_a_missing_row_is_named(tmp_path):
    s = F.load_sheet(write(tmp_path), "2030H1")
    with pytest.raises(F.SheetError, match="нет строки"):
        F.to_facts(s, {"x.json": {"v": F.Fact("ebitda")}})


@pytest.mark.parametrize("docs, sheet, needle", [
    (DOCS.replace(SHA, "abc"), SHEET, "sha256"),
    (DOCS, SHEET.replace("mln_shares", "mln_pcs"), "единица"),
    (DOCS, SHEET.replace(",x1,Сегменты", ",z9,Сегменты"), "нет в documents.csv"),
    (DOCS, SHEET.replace("2030Q1", "2030Q5"), "не в формате"),
    (DOCS, SHEET.replace("100.5", "100,5"), "полей"),
    (DOCS, SHEET.replace("100.5", "1e2"), "не число"),
    (DOCS, SHEET.replace(",r1,40,", ",r1,,"), "нет страницы"),
    (DOCS, SHEET + "revenue,,2030H1,1,bn_rub,r1,1,\n", "повторяет"),
    (DOCS.replace("2030-08-01", "01.08.2030", 1), SHEET, "ГГГГ-ММ-ДД"),
    (DOCS.replace("https://example.org/r1.pdf", "example.org/r1.pdf"), SHEET, "http"),
    (DOCS, SHEET.replace("metric,segment", "metric,seg"), "первая строка"),
])
def test_a_bad_sheet_is_refused_with_the_reason(tmp_path, docs, sheet, needle):
    d = write(tmp_path, docs, sheet)
    with pytest.raises(F.SheetError) as e:
        F.load_sheet(d, "2030H1")
    assert any(needle in p for p in e.value.problems), e.value.problems


def test_the_sheet_name_is_a_period(tmp_path):
    d = write(tmp_path, name="2030-H1.csv")
    assert any("имя листа" in p for p in F.check_dir(d))


def test_a_bom_is_refused(tmp_path):
    d = write(tmp_path)
    (d / "documents.csv").write_bytes(b"\xef\xbb\xbf" + DOCS.encode("utf-8"))
    assert any("BOM" in p for p in F.check_dir(d))


def test_two_sheets_merge_and_a_duplicate_is_refused(tmp_path):
    d = write(tmp_path)
    (d / "2030Q3.csv").write_bytes(("metric,segment,period,value,unit,doc_id,page,note\n"
                                    "revenue,,2030Q3,52,bn_rub,r1,3,\n").encode("utf-8"))
    a, b = F.load_sheet(d, "2030H1"), F.load_sheet(d, "2030Q3")
    out = F.to_facts([a, b], {"q.json": {"q1": F.Fact("revenue", period="2030Q1"), "q3": F.Fact("revenue", period="2030Q3")}})
    assert out["q.json"] == '{\n "q1": 48,\n "q3": 52\n}\n'
    with pytest.raises(F.SheetError, match="есть в листах"):
        F.merge([a, a])
