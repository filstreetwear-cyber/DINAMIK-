"""
Anello 1: scout live end-to-end contro Postgres reale (o skip), con un
fetcher finto che serve HTML sintetico e VIES via httpx.MockTransport.
Nessuna rete verso Amazon o la Commissione UE.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import (
    AgentRun,
    AmazonListing,
    AmazonProduct,
    AmazonSeller,
    Brand,
    Company,
    Evidence,
    EvidenceFile,
    Opportunity,
    ResearchJob,
)
from app.models.enums import OpportunityType, ResearchJobStatus
from app.services.opportunity_engine import (
    evaluate_listing_opportunity,
    listing_issues,
    run_opportunity_engine,
)
from app.services.scout_runner import qualify_country, run_scout
from app.tools.amazon_scraping.fetcher import FetchedPage, PageFetcher
from app.tools.vies.client import ViesClient
from tests.anello1_html import CAPTCHA_HTML, listing_html, product_html, seller_html

# ---------------------------------------------------------------------------
# Pure rule tests (no DB)
# ---------------------------------------------------------------------------


def _listing(**kw) -> AmazonListing:
    base = {
        "image_count": 7, "video_count": 1, "has_a_plus_content": True, "bullet_points": ["a"] * 5,
        "title_length": 120, "observed_at": datetime.now(UTC),
    }
    base.update(kw)
    return AmazonListing(**base)


def test_listing_issues_flags_each_concrete_problem():
    issues = listing_issues(_listing(image_count=3, video_count=0, has_a_plus_content=False,
                                     bullet_points=["a"], title_length=30), {})
    assert issues == ["immagini=3<6", "bullet=1<5", "titolo_corto=30", "nessun_video", "nessun_contenuto_A+"]
    assert listing_issues(_listing(), {}) == []


def test_listing_opportunity_levels():
    settings = get_settings()
    assert evaluate_listing_opportunity([], settings) is None
    good = ("B0GOOD0001", _listing())
    weak = ("B0WEAK0001", _listing(image_count=2, video_count=0))
    assert evaluate_listing_opportunity([good], settings).level.value == "NONE"
    medium = evaluate_listing_opportunity([good, good, good, weak], settings)
    assert medium.level.value == "MEDIUM"
    assert medium.reasons[0] == "schede_deboli=1/4"
    assert medium.reasons[1].startswith("B0WEAK0001: immagini=2<6")
    assert evaluate_listing_opportunity([good, weak], settings).level.value == "HIGH"


def test_qualify_country():
    settings = get_settings()
    assert qualify_country("IT", settings) == (True, "sede_IT")
    assert qualify_country("CN", settings) == (False, "sede_CN_fuori_target")
    assert qualify_country(None, settings) == (False, "sede_sconosciuta")


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


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
        pytest.skip("PostgreSQL not reachable — scout runner tests need a live DB")
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


class FakeFetcher(PageFetcher):
    """Serve HTML per (kind, key); salva file veri su disco come il fetcher reale."""

    def __init__(self, pages: dict[tuple[str, str], str], root: Path) -> None:
        self.pages = pages
        self.root = root
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, url: str, *, kind: str, key: str, screenshot: bool = False) -> FetchedPage:
        self.calls.append((kind, key))
        lookup = key if kind != "listing" else "0"
        html = self.pages[(kind, lookup)]
        self.root.mkdir(parents=True, exist_ok=True)
        html_path = self.root / f"{kind}_{key}_{len(self.calls)}.html"
        html_path.write_text(html)
        shot = None
        if screenshot:
            shot = self.root / f"{kind}_{key}_{len(self.calls)}.jpg"
            shot.write_bytes(b"jpg")
        from app.tools.amazon_scraping.page_parser import CaptchaDetectedError, is_captcha_page

        if is_captcha_page(html):
            raise CaptchaDetectedError(f"captcha on {url}")
        return FetchedPage(url=url, html=html, html_path=html_path, screenshot_path=shot, fetched_at=datetime.now(UTC))


def _vies(valid_vats: dict[str, dict]) -> ViesClient:
    def handler(request: httpx.Request) -> httpx.Response:
        number = request.url.path.rsplit("/", 1)[-1]
        data = valid_vats.get(number)
        if data is None:
            return httpx.Response(200, json={"isValid": False, "userError": "INVALID"})
        return httpx.Response(200, json={"isValid": True, "userError": "VALID", **data})

    return ViesClient(base_url="https://vies.test/rest-api", transport=httpx.MockTransport(handler))


def _pages() -> dict[tuple[str, str], str]:
    return {
        ("listing", "0"): listing_html(["B0ITSELL01", "B0ITSELL02", "B0CNSELL01", "B0AMAZON01"]),
        ("product", "B0ITSELL01"): product_html(seller_id="A1ITSELLER", images=3),
        ("product", "B0ITSELL02"): product_html(
            seller_id="A1ITSELLER", title="Portaspezie girevole 16 barattoli vetro", images=8, video=True,
            a_plus=True, bullets=5,
        ),
        ("product", "B0CNSELL01"): product_html(
            seller_id="A9CNSELLER", seller_name="FooHome", brand_byline="Marca: FooHome",
        ),
        ("product", "B0AMAZON01"): product_html(seller_mode="amazon", brand_byline="Marca: BigBrand"),
        ("seller", "A1ITSELLER"): seller_html(),
        ("seller", "A9CNSELLER"): seller_html(
            display_name="FooHome", business_name="Shenzhen Foo Trading Co., Ltd.", vat=None,
            address_lines=("Bao'an", "Shenzhen", "518000", "CN"),
        ),
    }


async def test_scout_run_end_to_end(session, tmp_path):
    fetcher = FakeFetcher(_pages(), tmp_path / "ev")
    vies = _vies({"01234567890": {"name": "CASABELLA SRL", "address": "VIA ROMA 1\n20121 MILANO MI"}})

    summary = await run_scout(
        session, fetcher=fetcher, vies=vies, listing_urls=["https://www.amazon.it/gp/bestsellers/x"],
        name="test-casa", category="Casa e cucina", max_products=10,
    )

    assert summary.stopped_reason is None
    assert summary.asins_requested == 4
    assert summary.products_ingested == 4
    assert summary.seller_extraction_rate == 1.0
    assert summary.sellers_profiled == 2  # Amazon non viene profilato
    assert summary.vies_checked == 1  # il seller CN non ha P.IVA
    assert summary.targets == 1 and summary.non_targets == 1  # conta venditori; il prodotto Amazon non ha riga seller

    it_seller = (await session.execute(select(AmazonSeller).where(AmazonSeller.seller_id == "A1ITSELLER"))).scalar_one()
    assert it_seller.business_name == "CASABELLA S.R.L."
    assert it_seller.vat_number == "01234567890"
    assert it_seller.is_target is True and it_seller.target_reason == "sede_IT"
    company = await session.get(Company, it_seller.company_id)
    assert company is not None and company.legal_name == "CASABELLA SRL"
    assert company.vies_valid is True and company.city == "Milano"
    brand = await session.get(Brand, it_seller.brand_id)
    assert brand is not None and brand.name == "CasaBella" and brand.company_id == company.id

    cn_seller = (await session.execute(select(AmazonSeller).where(AmazonSeller.seller_id == "A9CNSELLER"))).scalar_one()
    assert cn_seller.is_target is False and cn_seller.business_country == "CN"
    assert cn_seller.company_id is None

    # Screenshot tenuti solo per i target.
    listings = (
        await session.execute(
            select(AmazonProduct.asin, AmazonListing).join(AmazonListing, AmazonListing.product_id == AmazonProduct.id)
            .where(AmazonListing.html_path.like(f"{tmp_path}%"))
        )
    ).all()
    shots = {asin: listing.screenshot_path for asin, listing in listings}
    assert shots["B0ITSELL01"] is not None and Path(shots["B0ITSELL01"]).exists()
    assert shots["B0CNSELL01"] is None and shots["B0AMAZON01"] is None
    assert not list((tmp_path / "ev").glob("product_B0CNSELL01_*.jpg"))
    stored = (await session.execute(select(EvidenceFile).where(EvidenceFile.listing_id.in_(
        [listing.id for _, listing in listings]
    )))).scalars().all()
    assert len(stored) == 2 and stored[0].data == b"jpg"  # le 2 schede del seller IT and stored[0].content_type == "image/jpeg"

    # Ogni campo con la sua evidenza.
    product = (await session.execute(select(AmazonProduct).where(AmazonProduct.asin == "B0ITSELL01"))).scalar_one()
    claims = (await session.execute(select(Evidence.claim).where(Evidence.entity_id == str(product.id)))).scalars().all()
    assert any(c.startswith("Venditore della scheda B0ITSELL01") for c in claims)
    assert any(c.startswith("Immagini nella galleria B0ITSELL01: 3") for c in claims)

    job = await session.get(ResearchJob, summary.job_id)
    assert job is not None and job.status == ResearchJobStatus.COMPLETED.value
    run = await session.get(AgentRun, summary.agent_run_id)
    assert run is not None and run.output["seller_extraction_rate"] == 1.0  # regressione: era salvato 0

    assert summary.report_path is not None and summary.report_path.exists()
    report = summary.report_path.read_text()
    assert "| B0ITSELL01 | CasaBella Srl | A1ITSELLER | CasaBella | CASABELLA SRL | IT | IT01234567890 | valida | sì |" in report

    # La regola schede vede i dati veri dello scout.
    await run_opportunity_engine(session)
    opp = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.brand_id == brand.id, Opportunity.opportunity_type == OpportunityType.LISTING.value
            )
        )
    ).scalar_one()
    assert opp.level == "HIGH"  # 1 scheda debole su 2 -> quota 0.5
    assert any(r.startswith("B0ITSELL01:") for r in opp.reasons)


async def test_scout_rerun_is_idempotent_on_entities_and_appends_history(session, tmp_path):
    vies = _vies({"01234567890": {"name": "CASABELLA SRL", "address": "VIA ROMA 1\n20121 MILANO MI"}})
    pages = _pages()
    await run_scout(session, fetcher=FakeFetcher(pages, tmp_path / "a"), vies=vies, asins=["B0ITSELL01"], name="r1")
    second = FakeFetcher(pages, tmp_path / "b")
    await run_scout(session, fetcher=second, vies=vies, asins=["B0ITSELL01"], name="r2")

    assert ("seller", "A1ITSELLER") not in second.calls  # profilo appena letto: non si richiede di nuovo
    products = (await session.execute(select(func.count()).select_from(AmazonProduct).where(AmazonProduct.asin == "B0ITSELL01"))).scalar_one()
    sellers = (await session.execute(select(func.count()).select_from(AmazonSeller).where(AmazonSeller.seller_id == "A1ITSELLER"))).scalar_one()
    companies = (await session.execute(select(func.count()).select_from(Company).where(Company.vat_number == "01234567890"))).scalar_one()
    assert (products, sellers, companies) == (1, 1, 1)
    product = (await session.execute(select(AmazonProduct).where(AmazonProduct.asin == "B0ITSELL01"))).scalar_one()
    snapshots = (await session.execute(select(func.count()).select_from(AmazonListing).where(AmazonListing.product_id == product.id))).scalar_one()
    assert snapshots == 2


async def test_captcha_stops_run_and_keeps_what_was_collected(session, tmp_path):
    pages = _pages()
    pages[("product", "B0CNSELL01")] = CAPTCHA_HTML
    fetcher = FakeFetcher(pages, tmp_path / "ev")
    summary = await run_scout(
        session, fetcher=fetcher, vies=_vies({}), asins=["B0ITSELL01", "B0CNSELL01", "B0AMAZON01"], name="captcha"
    )
    assert summary.stopped_reason == "captcha"
    assert summary.products_ingested == 1
    assert ("product", "B0AMAZON01") not in fetcher.calls  # nessuna richiesta dopo il CAPTCHA
    assert ("seller", "A1ITSELLER") not in fetcher.calls
    job = await session.get(ResearchJob, summary.job_id)
    assert job is not None and job.status == ResearchJobStatus.PARTIAL.value
    assert "Stop anticipato: captcha" in summary.report_path.read_text()


async def test_seller_without_brand_is_still_created_and_profiled(session, tmp_path):
    pages = _pages()
    pages[("product", "B0NOBRAND1")] = product_html(seller_id="A1ITSELLER", brand_byline="")
    fetcher = FakeFetcher(pages, tmp_path / "ev")
    vies = _vies({"01234567890": {"name": "CASABELLA SRL", "address": "VIA ROMA 1\n20121 MILANO MI"}})
    summary = await run_scout(session, fetcher=fetcher, vies=vies, asins=["B0NOBRAND1"], name="nobrand")
    assert summary.sellers_profiled == 1
    product = (await session.execute(select(AmazonProduct).where(AmazonProduct.asin == "B0NOBRAND1"))).scalar_one()
    assert product.brand_id is None and product.seller_id is not None
    seller = await session.get(AmazonSeller, product.seller_id)
    assert seller is not None and seller.is_target is True and seller.company_id is not None


async def test_target_asins_for_refresh(session, tmp_path):
    from datetime import timedelta

    from app.services.scout_runner import target_asins_for_refresh

    vies = _vies({"01234567890": {"name": "CASABELLA SRL", "address": "VIA ROMA 1\n20121 MILANO MI"}})
    await run_scout(session, fetcher=FakeFetcher(_pages(), tmp_path / "ev"), vies=vies,
                    asins=["B0ITSELL01", "B0CNSELL01"], name="refresh-seed")
    assert "B0ITSELL01" not in await target_asins_for_refresh(session, limit=1000)  # appena osservata
    product = (await session.execute(select(AmazonProduct).where(AmazonProduct.asin == "B0ITSELL01"))).scalar_one()
    for listing in (await session.execute(select(AmazonListing).where(AmazonListing.product_id == product.id))).scalars():
        listing.observed_at = listing.observed_at - timedelta(days=10)
    await session.flush()
    due = await target_asins_for_refresh(session, limit=1000)
    assert "B0ITSELL01" in due and "B0CNSELL01" not in due  # il seller CN non è target
