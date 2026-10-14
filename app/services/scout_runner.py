"""
Scout live Anello 1 (2026-10-14): Amazon.it reale -> DB -> prospect qualificati.

Flusso di un run:
1. pagine elenco (bestseller/novità/ricerca) -> ASIN, fino a max_products;
2. per ogni ASIN: scheda prodotto (HTML + screenshot) -> parse -> ingestion;
3. per ogni nuovo venditore non-Amazon: pagina /sp?seller= -> ragione
   sociale, indirizzo, P.IVA -> VIES -> Company deduplicata per P.IVA;
4. qualificazione: target se la sede è in scout.target_countries (o nei
   secondary). Gli screenshot dei non-target vengono cancellati;
5. report markdown di validazione in reports/anello1/.

Un CAPTCHA o il tetto giornaliero fermano il run in modo pulito: quello che
è stato già raccolto resta (commit dopo ogni prodotto quando commit=True),
il job finisce PARTIAL.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import AgentRun, AmazonListing, AmazonSeller, Company, ResearchJob
from app.models.enums import ResearchJobStatus
from app.services.amazon_ingestion import (
    apply_seller_profile,
    apply_vies_result,
    ingest_product_page,
)
from app.tools.amazon_scraping.fetcher import DailyLimitReachedError, PageFetcher
from app.tools.amazon_scraping.page_parser import (
    CaptchaDetectedError,
    extract_asins_from_listing,
    normalize_asin,
    parse_product_page,
    parse_seller_page,
)
from app.tools.vies.client import DEFAULT_BASE_URL as DEFAULT_VIES_BASE_URL
from app.tools.vies.client import ViesClient

PROFILE_REFRESH_DAYS = 30


@dataclass
class ReportRow:
    asin: str
    title: str | None
    seller_name: str | None
    seller_id: str | None
    seller_is_amazon: bool
    brand: str | None
    rating: float | None
    review_count: int | None
    bsr: int | None
    image_count: int
    has_video: bool
    has_a_plus: bool
    business_name: str | None = None
    country: str | None = None
    vat: str | None = None
    vies: str | None = None
    target: bool | None = None


@dataclass
class ScoutRunSummary:
    job_id: uuid.UUID
    agent_run_id: uuid.UUID
    asins_requested: int = 0
    pages_fetched: int = 0
    products_ingested: int = 0
    sellers_profiled: int = 0
    vies_checked: int = 0
    targets: int = 0
    non_targets: int = 0
    stopped_reason: str | None = None  # "captcha" | "daily_limit" | None
    errors: list[str] = field(default_factory=list)
    rows: list[ReportRow] = field(default_factory=list)
    report_path: Path | None = None

    @property
    def seller_extraction_rate(self) -> float:
        if not self.rows:
            return 0.0
        found = sum(1 for r in self.rows if r.seller_name)
        return found / len(self.rows)


def qualify_country(country: str | None, settings: Settings) -> tuple[bool, str]:
    cfg = settings.scout_config()
    primary = {c.upper() for c in cfg.get("target_countries", ["IT"])}
    secondary = {c.upper() for c in cfg.get("secondary_target_countries", [])}
    if not country:
        return False, "sede_sconosciuta"
    code = country.upper()
    if code in primary:
        return True, f"sede_{code}"
    if code in secondary:
        return True, f"sede_{code}_secondaria"
    return False, f"sede_{code}_fuori_target"


def _needs_profile(seller: AmazonSeller, now: datetime) -> bool:
    if seller.is_amazon:
        return False
    if seller.profile_checked_at is None:
        return True
    return now - seller.profile_checked_at > timedelta(days=PROFILE_REFRESH_DAYS)


async def run_scout(
    session: AsyncSession,
    *,
    fetcher: PageFetcher,
    vies: ViesClient | None = None,
    asins: list[str] | None = None,
    listing_urls: list[str] | None = None,
    name: str = "anello1",
    category: str | None = None,
    max_products: int = 30,
    commit: bool = False,
    settings: Settings | None = None,
) -> ScoutRunSummary:
    settings = settings or get_settings()
    cfg = settings.scout_config()
    base_url = str(cfg.get("base_url", "https://www.amazon.it")).rstrip("/")
    marketplace = settings.amazon_marketplace
    vies = vies or ViesClient(base_url=str(cfg.get("vies_base_url", DEFAULT_VIES_BASE_URL)))

    job = ResearchJob(
        name=name,
        marketplace=marketplace,
        category=category,
        keywords=listing_urls or None,
        status=ResearchJobStatus.RUNNING.value,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    agent_run = AgentRun(
        agent_name="amazon_scout_live",
        job_id=job.id,
        input={"asins": asins or [], "listing_urls": listing_urls or [], "max_products": max_products},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()
    summary = ScoutRunSummary(job_id=job.id, agent_run_id=agent_run.id)

    async def _commit() -> None:
        if commit:
            await session.commit()

    await _commit()

    queue: list[str] = []
    for raw in asins or []:
        asin = normalize_asin(raw)
        if asin and asin not in queue:
            queue.append(asin)

    listing_by_asin: dict[str, uuid.UUID] = {}
    sellers_to_profile: list[uuid.UUID] = []
    row_by_asin: dict[str, ReportRow] = {}

    try:
        for index, url in enumerate(listing_urls or []):
            if len(queue) >= max_products:
                break
            page = await fetcher.fetch(url, kind="listing", key=f"{job.id.hex[:8]}_{index}")
            summary.pages_fetched += 1
            for asin in extract_asins_from_listing(page.html):
                if asin not in queue:
                    queue.append(asin)
        queue = queue[:max_products]
        summary.asins_requested = len(queue)

        for asin in queue:
            url = f"{base_url}/dp/{asin}"
            fetched = await fetcher.fetch(url, kind="product", key=asin, screenshot=True)
            summary.pages_fetched += 1
            try:
                parsed = parse_product_page(fetched.html, asin, marketplace)
            except CaptchaDetectedError:
                raise
            except Exception as exc:  # noqa: BLE001 -- una pagina rotta non ferma il batch
                summary.errors.append(f"{asin}: parse failed: {exc}")
                continue
            outcome = await ingest_product_page(
                session,
                job,
                parsed,
                source_url=url,
                html_path=fetched.html_path,
                screenshot_path=fetched.screenshot_path,
                observed_at=fetched.fetched_at,
            )
            summary.products_ingested += 1
            listing_by_asin[asin] = outcome.listing_id
            row_by_asin[asin] = ReportRow(
                asin=asin,
                title=parsed.title,
                seller_name=parsed.seller_name,
                seller_id=parsed.seller_id,
                seller_is_amazon=parsed.seller_is_amazon,
                brand=parsed.brand,
                rating=parsed.rating,
                review_count=parsed.review_count,
                bsr=parsed.bsr,
                image_count=len(parsed.image_urls),
                has_video=parsed.has_video,
                has_a_plus=parsed.has_a_plus_content,
            )
            if outcome.seller_row_id is not None and outcome.seller_row_id not in sellers_to_profile:
                sellers_to_profile.append(outcome.seller_row_id)
            await _commit()

        now = datetime.now(UTC)
        for seller_row_id in sellers_to_profile:
            seller = await session.get(AmazonSeller, seller_row_id)
            if seller is None or not _needs_profile(seller, now):
                continue
            url = f"{base_url}/sp?seller={seller.seller_id}"
            fetched = await fetcher.fetch(url, kind="seller", key=seller.seller_id)
            summary.pages_fetched += 1
            profile = parse_seller_page(fetched.html, seller.seller_id, marketplace)
            await apply_seller_profile(
                session, seller, profile, source_url=url, html_path=fetched.html_path, observed_at=fetched.fetched_at
            )
            summary.sellers_profiled += 1
            if profile.vat_number:
                vat_country = profile.vat_country or profile.business_country
                if vat_country:
                    result = await vies.check(vat_country, profile.vat_number)
                    summary.vies_checked += 1
                    await apply_vies_result(session, seller, result)
            await _commit()

    except CaptchaDetectedError as exc:
        summary.stopped_reason = "captcha"
        summary.errors.append(str(exc))
    except DailyLimitReachedError as exc:
        summary.stopped_reason = "daily_limit"
        summary.errors.append(str(exc))

    await _qualify_and_prune(session, summary, row_by_asin, listing_by_asin, settings)

    job.status = (
        ResearchJobStatus.PARTIAL.value if summary.stopped_reason else ResearchJobStatus.COMPLETED.value
    )
    job.completed_at = datetime.now(UTC)
    agent_run.status = "SUCCESS" if not summary.stopped_reason else "PARTIAL"
    agent_run.output = {
        "asins_requested": summary.asins_requested,
        "pages_fetched": summary.pages_fetched,
        "products_ingested": summary.products_ingested,
        "sellers_profiled": summary.sellers_profiled,
        "vies_checked": summary.vies_checked,
        "targets": summary.targets,
        "non_targets": summary.non_targets,
        "seller_extraction_rate": round(summary.seller_extraction_rate, 3),
        "stopped_reason": summary.stopped_reason,
        "errors": summary.errors[:50],
    }
    summary.rows = [row_by_asin[a] for a in queue if a in row_by_asin]
    summary.report_path = write_validation_report(summary, settings, job_name=name)
    await session.flush()
    await _commit()
    return summary


async def _qualify_and_prune(
    session: AsyncSession,
    summary: ScoutRunSummary,
    row_by_asin: dict[str, ReportRow],
    listing_by_asin: dict[str, uuid.UUID],
    settings: Settings,
) -> None:
    """Decide target/non-target per ogni seller visto in questo run e
    tiene gli screenshot solo dei target."""
    seller_ids = {r.seller_id for r in row_by_asin.values() if r.seller_id}
    sellers: dict[str, AmazonSeller] = {}
    if seller_ids:
        rows = (
            await session.execute(
                select(AmazonSeller).where(
                    AmazonSeller.seller_id.in_(seller_ids),
                    AmazonSeller.marketplace == settings.amazon_marketplace,
                )
            )
        ).scalars()
        sellers = {s.seller_id: s for s in rows}

    counted: set[str] = set()
    for asin, row in row_by_asin.items():
        seller = sellers.get(row.seller_id) if row.seller_id else None
        if seller is not None:
            company = await session.get(Company, seller.company_id) if seller.company_id else None
            country = seller.business_country or seller.vat_country
            if company is not None and company.vies_valid:
                country = country or company.country
            if seller.is_amazon:
                is_target, reason = False, "venduto_da_amazon"
            else:
                is_target, reason = qualify_country(country, settings)
            seller.is_target = is_target
            seller.target_reason = reason
            row.business_name = (company.legal_name if company else None) or seller.business_name
            row.country = country
            row.vat = f"{seller.vat_country or ''}{seller.vat_number}" if seller.vat_number else None
            if company is not None:
                row.vies = "valida" if company.vies_valid else "non valida"
            row.target = is_target
            if seller.seller_id not in counted:
                counted.add(seller.seller_id)
                if is_target:
                    summary.targets += 1
                else:
                    summary.non_targets += 1
        else:
            row.target = False

        if not row.target:
            listing_id = listing_by_asin.get(asin)
            listing = await session.get(AmazonListing, listing_id) if listing_id else None
            if listing is not None and listing.screenshot_path:
                Path(listing.screenshot_path).unlink(missing_ok=True)
                listing.screenshot_path = None
    await session.flush()


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sì" if value else "no"
    return str(value).replace("|", "/")


def write_validation_report(summary: ScoutRunSummary, settings: Settings, *, job_name: str) -> Path:
    reports_dir = Path(settings.scout_reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    path = reports_dir / f"{now.strftime('%Y-%m-%d_%H%M')}_{summary.job_id.hex[:8]}.md"
    total = len(summary.rows)
    with_seller = sum(1 for r in summary.rows if r.seller_name)
    lines = [
        f"# Validazione Scout — {job_name} ({now.strftime('%Y-%m-%d %H:%M')} UTC)",
        "",
        f"- Job: `{summary.job_id}`",
        f"- ASIN richiesti: {summary.asins_requested} · schede salvate: {summary.products_ingested}",
        f"- Venditore estratto: {with_seller}/{total} ({summary.seller_extraction_rate:.0%}) — soglia gate: 80%",
        f"- Profili venditore letti: {summary.sellers_profiled} · verifiche VIES: {summary.vies_checked}",
        f"- Venditori target: {summary.targets} · non target: {summary.non_targets}",
        f"- Stop anticipato: {summary.stopped_reason or 'no'}",
        "",
        (
            "Controllo manuale: aprire almeno 10 righe su amazon.it e segnare nella colonna "
            "**OK?** se venditore e brand corrispondono."
        ),
        "",
        (
            "| ASIN | Venditore | ID venditore | Brand | Ragione sociale | Paese | P.IVA | VIES | Target "
            "| Voto | Recensioni | BSR | Immagini | Video | A+ | OK? |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summary.rows:
        seller = "Amazon" if r.seller_is_amazon else r.seller_name
        lines.append(
            "| "
            + " | ".join(
                _fmt(v)
                for v in (
                    r.asin, seller, r.seller_id, r.brand, r.business_name, r.country, r.vat, r.vies,
                    r.target, r.rating, r.review_count, r.bsr, r.image_count, r.has_video, r.has_a_plus,
                )
            )
            + " |  |"
        )
    if summary.errors:
        lines += ["", "## Errori", ""] + [f"- {e}" for e in summary.errors]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
