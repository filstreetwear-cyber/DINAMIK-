"""
Interfaccia web /ui end-to-end: app FastAPI vera, Postgres vero (o skip),
dati seminati con lo scout (fetcher finto) e committati, poi rimossi.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.database.session import engine as app_engine
from app.main import app
from app.models import (
    AgentRun,
    AmazonProduct,
    AmazonSeller,
    AuditorPackage,
    Brand,
    Company,
    Evidence,
    Opportunity,
    ResearchJob,
    ResearchResult,
    Score,
)
from app.services.opportunity_engine import run_opportunity_engine
from app.services.scoring_engine import run_scoring_engine
from app.services.scout_runner import run_scout
from tests.conftest import _db_reachable
from tests.test_scout_runner import FakeFetcher, _pages, _vies

_ASINS = ["B0ITSELL01", "B0ITSELL02", "B0CNSELL01"]


async def _cleanup() -> None:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        product_ids = [str(i) for i in (await s.execute(select(AmazonProduct.id).where(AmazonProduct.asin.in_(_ASINS)))).scalars()]
        seller_rows = (await s.execute(select(AmazonSeller.id, AmazonSeller.brand_id).where(
            AmazonSeller.seller_id.in_(["A1ITSELLER", "A9CNSELLER"])))).all()
        brand_ids = [b for _, b in seller_rows if b]
        company_ids = [str(i) for i in (await s.execute(select(Company.id).where(Company.vat_number == "01234567890"))).scalars()]
        job_ids = list((await s.execute(select(ResearchJob.id).where(ResearchJob.name == "ui-seed"))).scalars())
        ids = product_ids + [str(i) for i, _ in seller_rows] + [str(b) for b in brand_ids] + company_ids
        if ids:
            await s.execute(delete(Evidence).where(Evidence.entity_id.in_(ids)))
        if brand_ids:
            await s.execute(delete(AuditorPackage).where(AuditorPackage.brand_id.in_(brand_ids)))
            await s.execute(delete(Score).where(Score.brand_id.in_(brand_ids)))
            await s.execute(delete(Opportunity).where(Opportunity.brand_id.in_(brand_ids)))
        if job_ids:
            await s.execute(delete(AgentRun).where(AgentRun.job_id.in_(job_ids)))
            await s.execute(delete(ResearchResult).where(ResearchResult.job_id.in_(job_ids)))
            await s.execute(delete(ResearchJob).where(ResearchJob.id.in_(job_ids)))
        await s.execute(delete(AmazonProduct).where(AmazonProduct.asin.in_(_ASINS)))
        await s.execute(delete(AmazonSeller).where(AmazonSeller.seller_id.in_(["A1ITSELLER", "A9CNSELLER"])))
        if brand_ids:
            await s.execute(delete(Brand).where(Brand.id.in_(brand_ids)))
        await s.execute(delete(Brand).where(or_(Brand.name == "CasaBella", Brand.name == "FooHome")))
        await s.execute(delete(Company).where(Company.vat_number == "01234567890"))
        await s.commit()
    await engine.dispose()


async def _seed(tmp: Path) -> None:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        vies = _vies({"01234567890": {"name": "CASABELLA SRL", "address": "VIA ROMA 1\n20121 MILANO MI"}})
        await run_scout(s, fetcher=FakeFetcher(_pages(), tmp), vies=vies, asins=_ASINS, name="ui-seed", commit=True)
        await run_opportunity_engine(s, max_brands=5000)
        await run_scoring_engine(s, max_brands=5000)
        await s.commit()
    await engine.dispose()


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> Iterator[TestClient]:
    # Module-scoped come in tests/test_main.py: il pool di connessioni
    # dell'app è legato all'event loop del primo TestClient.
    if not asyncio.run(_db_reachable()):
        pytest.skip("PostgreSQL not reachable")
    # Fixture di modulo: gira prima dell'isolamento per-test di conftest,
    # quindi isola qui le cartelle generate (report, evidenze).
    mp = pytest.MonkeyPatch()
    mp.setenv("SCOUT_REPORTS_DIR", str(tmp_path_factory.mktemp("reports")))
    mp.setenv("SCOUT_EVIDENCE_DIR", str(tmp_path_factory.mktemp("evidence")))
    get_settings.cache_clear()
    asyncio.run(_cleanup())
    asyncio.run(_seed(tmp_path_factory.mktemp("ev")))
    mp.undo()
    get_settings.cache_clear()
    # Un altro modulo (tests/test_main.py) può aver lasciato il pool dell'app
    # legato al suo event loop: lo si abbandona senza chiudere (close=False).
    asyncio.run(app_engine.dispose(close=False))
    with TestClient(app) as c:
        yield c
    asyncio.run(app_engine.dispose(close=False))
    asyncio.run(_cleanup())


def _brand_id(client: TestClient) -> str:
    rows = client.get("/prospects", params={"q": "CasaBella"}).json()
    return next(r["brand_id"] for r in rows if r["brand_name"] == "CasaBella")


def test_prospect_flow_end_to_end(client: TestClient):
    page = client.get("/ui")
    assert page.status_code == 200 and "Panoramica" in page.text

    listing = client.get("/ui/prospects", params={"q": "CasaBella"})
    assert listing.status_code == 200 and "CASABELLA SRL" in listing.text
    assert "FooHome" not in client.get("/ui/prospects").text  # non target nascosto di default

    brand_id = _brand_id(client)
    detail = client.get(f"/ui/prospects/{brand_id}")
    assert detail.status_code == 200
    assert "IT01234567890" in detail.text and "Nessun video nella galleria." in detail.text
    shot_src = detail.text.split('src="/ui/files/')[1].split('"')[0]
    shot = client.get(f"/ui/files/{shot_src}")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"

    client.cookies.set("dinamik_user", "frank")
    r = client.post(f"/ui/prospects/{brand_id}/status", data={"status": "APPROVED", "reason": "ok"},
                    follow_redirects=False)
    assert r.status_code == 303
    detail = client.get(f"/ui/prospects/{brand_id}")
    assert "Approvato" in detail.text and "frank" in detail.text

    r = client.post(f"/ui/prospects/{brand_id}/draft", data={"subject": "Oggetto prova", "body": "Testo prova"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "Oggetto prova" in client.get(f"/ui/prospects/{brand_id}").text

    r = client.post(f"/ui/prospects/{brand_id}/audit", follow_redirects=True)
    assert r.status_code == 200 and "Nessun video nella galleria." in r.text and "Stampa" in r.text

    assert client.get("/ui/runs").status_code == 200
    assert client.get("/ui/static/style.css").status_code == 200


def test_login_required_when_key_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "segreta")
    get_settings.cache_clear()
    client.cookies.clear()
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/login"
    assert client.post("/ui/login", data={"name": "frank", "key": "sbagliata"}).status_code == 401
    r = client.post("/ui/login", data={"name": "frank", "key": "segreta"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/ui").status_code == 200
    assert client.get("/ui/logout", follow_redirects=False).status_code == 303
