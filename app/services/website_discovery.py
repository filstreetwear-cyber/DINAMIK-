"""
Website discovery (2026-10-14): ragione sociale/brand -> sito ufficiale,
senza Google Maps né motori di ricerca.

Il trucco è un obbligo di legge italiano: il sito di un'azienda deve
mostrare la Partita IVA (di solito nel footer della home). Quindi:
1. dal nome del brand e dalla ragione sociale si generano pochi domini
   candidati (casabella.it, casa-bella.it, casabella.com, ...);
2. si scarica la home di ciascuno;
3. se nella pagina compare la P.IVA verificata su VIES -> sito CONFERMATO
   (Brand.website valorizzato, poi WebsiteProfile via
   WebsiteIntelligenceProvider: tecnologia, mobile, chatbot, Lighthouse);
4. se compare solo il nome del brand nel <title> -> sito POSSIBILE:
   salvato come Evidence a confidenza bassa, Brand.website resta vuoto
   finché un umano non lo conferma dall'interfaccia.

Gira dal server OVH: non tocca Amazon.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, Brand, Company, ErrorLog, Evidence, WebsiteProfile
from app.models.enums import AcquisitionMethod, DataType, ErrorCategory, EvidenceSourceType
from app.services.brand_deduplication import normalize_brand_name
from app.tools.core.capability import Capability
from app.tools.core.provider import IntelligenceProvider, ProviderError
from app.tools.website.http_client import FetchedPage, WebsiteFetchError, fetch_page

CONFIRMED = "vat_on_homepage"
POSSIBLE = "brand_in_title"
_TLDS = (".it", ".com", ".eu", ".shop")
_MAX_CANDIDATES = 10

PageFetch = Callable[[str], Awaitable[FetchedPage]]


@dataclass(frozen=True)
class WebsiteMatch:
    url: str
    method: str  # CONFIRMED | POSSIBLE
    confidence: float


def domain_candidates(names: list[str]) -> list[str]:
    """Pochi domini plausibili, in ordine di probabilità (.it prima)."""
    slugs: list[str] = []
    for name in names:
        if not name:
            continue
        norm = normalize_brand_name(name)  # minuscolo, senza accenti, senza srl/spa/...
        words = [w for w in norm.split() if w]
        if not words:
            continue
        for slug in ("".join(words), "-".join(words)):
            if len(slug) >= 3 and slug not in slugs:
                slugs.append(slug)
    candidates: list[str] = []
    for tld in _TLDS:
        for slug in slugs:
            candidates.append(f"{slug}{tld}")
    return candidates[:_MAX_CANDIDATES]


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def page_contains_vat(html: str, vat_number: str) -> bool:
    """P.IVA presente nel testo visibile, tollerando spazi/punti/prefisso IT."""
    if not vat_number:
        return False
    text = BeautifulSoup(html, "lxml").get_text(" ")
    target = _digits(vat_number)
    if len(target) < 8:
        return False
    # Confronto su sequenze di cifre "raggruppate" (es. "IT 012 345 678 90").
    for chunk in re.findall(r"(?:IT\s?)?[\d][\d .]{7,20}\d", text, flags=re.IGNORECASE):
        if _digits(chunk).endswith(target):
            return True
    return target in _digits(text)


def page_title_mentions(html: str, name: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ") if soup.title else ""
    norm_title = normalize_brand_name(title)
    norm_name = normalize_brand_name(name)
    return bool(norm_name) and norm_name in norm_title


async def discover_website(
    brand_name: str,
    company_name: str | None,
    vat_number: str | None,
    *,
    fetch: PageFetch = fetch_page,
) -> WebsiteMatch | None:
    possible: WebsiteMatch | None = None
    for domain in domain_candidates([brand_name, company_name or ""]):
        url = f"https://www.{domain}"
        try:
            page = await fetch(url)
        except WebsiteFetchError:
            continue
        if vat_number and page_contains_vat(page.html, vat_number):
            return WebsiteMatch(url=page.url, method=CONFIRMED, confidence=95.0)
        if possible is None and page_title_mentions(page.html, brand_name):
            possible = WebsiteMatch(url=page.url, method=POSSIBLE, confidence=60.0)
    return possible


def _merge_results(results: list[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for r in results:
        merged.update(r.data)
    return merged


async def apply_website(
    session: AsyncSession,
    brand: Brand,
    url: str,
    *,
    provider: IntelligenceProvider | None,
    include_performance_audit: bool = False,
) -> WebsiteProfile | None:
    """Collega il sito confermato e, se c'è un provider, crea/aggiorna il
    WebsiteProfile. Usato sia dalla discovery sia dalla conferma manuale
    dall'interfaccia."""
    brand.website = url
    if provider is None:
        await session.flush()
        return None
    try:
        results = await provider.fetch(
            Capability.WEBSITE_DATA, url=url, include_performance_audit=include_performance_audit
        )
    except ProviderError:
        await session.flush()
        return None
    data = _merge_results(results)
    profile = (
        await session.execute(select(WebsiteProfile).where(WebsiteProfile.brand_id == brand.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = WebsiteProfile(brand_id=brand.id, domain=url)
        session.add(profile)
    profile.domain = url
    technology = data.get("technology")
    profile.technology = getattr(technology, "value", technology) or "UNKNOWN"
    profile.has_chatbot = data.get("has_chatbot")
    profile.mobile_friendly = data.get("mobile_friendly")
    if data.get("page_speed_score") is not None:
        profile.page_speed_score = data["page_speed_score"]
    if data.get("seo_score") is not None:
        profile.seo_score = data["seo_score"]
    profile.last_crawled_at = datetime.now(UTC)
    for r in results:
        session.add(
            Evidence(
                entity_type="brand",
                entity_id=str(brand.id),
                claim=f"Sito {url}: {r.data}"[:1000],
                data_type=r.data_type.value,
                source=f"website_intelligence:{url}"[:255],
                source_url=r.source_url,
                source_type=EvidenceSourceType.WEBSITE.value,
                raw_value=str(r.data),
                confidence=r.confidence,
                observed_at=r.observed_at,
                provider="website_intelligence",
                acquisition_method=r.acquisition_method.value,
            )
        )
    await session.flush()
    return profile


@dataclass
class WebsiteDiscoverySummary:
    agent_run_id: uuid.UUID
    brands_considered: int = 0
    confirmed: int = 0
    possible: int = 0
    not_found: int = 0
    details: list[dict[str, str]] = field(default_factory=list)


async def run_website_discovery(
    session: AsyncSession,
    *,
    max_brands: int = 50,
    fetch: PageFetch = fetch_page,
    provider: IntelligenceProvider | None = None,
    include_performance_audit: bool = False,
) -> WebsiteDiscoverySummary:
    """Brand ACTIVE senza sito ma con azienda e P.IVA nota (cioè passati per
    VIES): sono gli unici per cui la conferma via P.IVA è possibile."""
    agent_run = AgentRun(agent_name="website_discovery", input={"max_brands": max_brands}, status="RUNNING")
    session.add(agent_run)
    await session.flush()
    summary = WebsiteDiscoverySummary(agent_run_id=agent_run.id)
    try:
        rows = (
            await session.execute(
                select(Brand, Company)
                .join(Company, Brand.company_id == Company.id)
                .where(Brand.status == "ACTIVE", Brand.website.is_(None), Company.vat_number.is_not(None))
                .order_by(Brand.first_seen_at)
                .limit(max_brands)
            )
        ).all()
        for brand, company in rows:
            summary.brands_considered += 1
            match = await discover_website(brand.name, company.legal_name, company.vat_number, fetch=fetch)
            if match is None:
                summary.not_found += 1
                summary.details.append({"brand": brand.name, "result": "not_found"})
                continue
            session.add(
                Evidence(
                    entity_type="brand",
                    entity_id=str(brand.id),
                    claim=(
                        f"Sito {'confermato (P.IVA in pagina)' if match.method == CONFIRMED else 'possibile (nome nel titolo)'}"
                        f": {match.url}"
                    ),
                    data_type=(DataType.FACT if match.method == CONFIRMED else DataType.INFERENCE).value,
                    source=f"website_discovery:{match.method}",
                    source_url=match.url,
                    source_type=EvidenceSourceType.WEBSITE.value,
                    raw_value=match.url,
                    confidence=match.confidence,
                    observed_at=datetime.now(UTC),
                    provider="website_discovery",
                    acquisition_method=AcquisitionMethod.HTTP.value,
                )
            )
            if match.method == CONFIRMED:
                summary.confirmed += 1
                if company.domain is None:
                    company.domain = match.url
                await apply_website(
                    session, brand, match.url, provider=provider, include_performance_audit=include_performance_audit
                )
            else:
                summary.possible += 1
            summary.details.append({"brand": brand.name, "result": match.method, "url": match.url})
            await session.flush()
        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": summary.brands_considered,
            "confirmed": summary.confirmed,
            "possible": summary.possible,
            "not_found": summary.not_found,
        }
        await session.flush()
    except Exception as exc:  # noqa: BLE001 -- stessa disciplina agent-run degli altri motori
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(ErrorLog(category=ErrorCategory.UNKNOWN.value, message=str(exc), agent_run_id=agent_run.id))
        await session.flush()
    return summary
