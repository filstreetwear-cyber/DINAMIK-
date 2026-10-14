"""Website discovery: P.IVA in home page = sito confermato. Nessuna rete reale."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Brand, Company, Evidence, WebsiteProfile
from app.models.enums import AcquisitionMethod, DataType
from app.services.website_discovery import (
    CONFIRMED,
    POSSIBLE,
    discover_website,
    domain_candidates,
    page_contains_vat,
    run_website_discovery,
)
from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider, ProviderResult
from app.tools.website.http_client import FetchedPage, WebsiteFetchError

HOME_WITH_VAT = "<html><head><title>CasaBella - Organizzazione casa</title></head><body><footer>CasaBella S.r.l. - P.IVA IT 012 345 678 90</footer></body></html>"
HOME_TITLE_ONLY = "<html><head><title>CasaBella | Shop</title></head><body>shop</body></html>"


def _fetcher(pages: dict[str, str]):
    calls: list[str] = []

    async def fetch(url: str) -> FetchedPage:
        calls.append(url)
        if url not in pages:
            raise WebsiteFetchError(f"no {url}")
        return FetchedPage(url=url, status_code=200, html=pages[url])

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_domain_candidates_strip_legal_suffix_and_prefer_it():
    candidates = domain_candidates(["Casa Bella", "CASA BELLA S.R.L."])
    assert candidates[:2] == ["casabella.it", "casa-bella.it"]
    assert "casabella.com" in candidates
    assert all("srl" not in c for c in candidates)


def test_page_contains_vat_tolerates_formatting():
    assert page_contains_vat(HOME_WITH_VAT, "01234567890")
    assert not page_contains_vat(HOME_TITLE_ONLY, "01234567890")
    assert not page_contains_vat(HOME_WITH_VAT, "")


async def test_discover_confirmed_beats_possible():
    fetch = _fetcher({"https://www.casabella.com": HOME_WITH_VAT, "https://www.casabella.it": HOME_TITLE_ONLY})
    match = await discover_website("CasaBella", "CASABELLA S.R.L.", "01234567890", fetch=fetch)
    assert match is not None and match.method == CONFIRMED and match.url == "https://www.casabella.com"


async def test_discover_possible_when_only_title_matches():
    fetch = _fetcher({"https://www.casabella.it": HOME_TITLE_ONLY})
    match = await discover_website("CasaBella", None, "01234567890", fetch=fetch)
    assert match is not None and match.method == POSSIBLE


async def test_discover_none():
    assert await discover_website("CasaBella", None, "01234567890", fetch=_fetcher({})) is None


class _FakeWebsiteProvider(IntelligenceProvider):
    name = "website_intelligence"
    supported_capabilities = frozenset({Capability.WEBSITE_DATA})

    async def health(self):  # pragma: no cover
        return None

    async def fetch(self, capability, **params):
        now = datetime.now(UTC)
        return [
            ProviderResult(capability=capability, data={"technology": "SHOPIFY", "has_chatbot": False},
                           data_type=DataType.INFERENCE, confidence=70.0,
                           acquisition_method=AcquisitionMethod.HTTP, observed_at=now, source_url=params["url"]),
            ProviderResult(capability=capability, data={"mobile_friendly": False},
                           data_type=DataType.INFERENCE, confidence=70.0,
                           acquisition_method=AcquisitionMethod.HTTP, observed_at=now, source_url=params["url"]),
        ]


async def _db_reachable() -> bool:
    try:
        engine = create_async_engine(get_settings().database_url)
        async with engine.connect() as conn:
            await conn.run_sync(lambda c: None)
        await engine.dispose()
        return True
    except (OperationalError, OSError):
        return False


@pytest.fixture
async def session():
    if not await _db_reachable():
        pytest.skip("PostgreSQL not reachable")
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def test_run_website_discovery_sets_website_and_profile(session):
    company = Company(legal_name="CASABELLA SRL", vat_country="IT", vat_number="01234567890", country="IT")
    session.add(company)
    await session.flush()
    brand = Brand(name="CasaBellaWDTest", company_id=company.id)
    session.add(brand)
    await session.flush()

    fetch = _fetcher({"https://www.casabellawdtest.it": HOME_WITH_VAT})
    summary = await run_website_discovery(session, max_brands=500, fetch=fetch, provider=_FakeWebsiteProvider())

    assert summary.confirmed >= 1
    await session.refresh(brand)
    assert brand.website == "https://www.casabellawdtest.it"
    profile = (await session.execute(select(WebsiteProfile).where(WebsiteProfile.brand_id == brand.id))).scalar_one()
    assert profile.technology == "SHOPIFY" and profile.mobile_friendly is False
    claims = (await session.execute(select(Evidence.claim).where(Evidence.entity_id == str(brand.id)))).scalars().all()
    assert any("confermato (P.IVA in pagina)" in c for c in claims)
