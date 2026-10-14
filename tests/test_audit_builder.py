"""Auditor MVP: pacchetto di audit da dati dello scout. Postgres reale o skip."""
from __future__ import annotations

from app.services.audit_builder import build_audit_package, describe_issue, latest_audit_package
from tests.test_prospect_service import _seed


def test_describe_issue_templates():
    d = describe_issue("immagini=3<6")
    assert d["problem"] == "Solo 3 immagini nella galleria (consigliate almeno 6)."
    assert d["action"]
    assert describe_issue("nessun_contenuto_A+")["code"] == "nessun_contenuto_A+"
    assert describe_issue("titolo_corto=30")["problem"] == "Titolo di soli 30 caratteri."
    assert describe_issue("qualcosa")["problem"] == "qualcosa"


async def test_build_audit_package(session, tmp_path):
    brand_id = await _seed(session, tmp_path)
    package = await build_audit_package(session, brand_id)
    payload = package.payload
    assert payload["company"]["vat"] == "IT01234567890"
    assert payload["summary"]["products_analyzed"] == 2
    assert payload["summary"]["products_with_issues"] == 2  # anche B0ITSELL02: titolo corto
    weak = next(p for p in payload["products"] if p["asin"] == "B0ITSELL01")
    problems = [i["problem"] for i in weak["issues"]]
    assert "Nessun video nella galleria." in problems
    assert weak["screenshot_id"] is not None
    assert float(package.confidence) == 100.0
    assert (await latest_audit_package(session, brand_id)).id == package.id
