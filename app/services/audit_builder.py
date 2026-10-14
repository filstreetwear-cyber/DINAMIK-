"""
Auditor MVP (2026-10-14): il pacchetto di audit di un prospect.

Mette insieme, per UN brand, quello che lo scout e la website discovery
hanno già osservato e lo traduce in frasi leggibili da un cliente:
problema concreto + perché conta + cosa fare. Testi deterministici
(template), nessuna AI: ogni frase discende da un numero salvato con la
sua evidenza. Le raccomandazioni AI, se esistono, sono allegate a parte e
marcate come tali.

Il risultato viene salvato in AuditorPackage (tabella esistente dalla
Milestone 1, finora mai scritta) e mostrato dall'interfaccia come pagina
stampabile (/ui/prospects/{id}/audit -> Stampa -> PDF).
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import AuditorPackage, ResearchJob, ResearchResult
from app.services.prospect_service import ProspectNotFoundError, get_prospect

_ISSUE_RE = re.compile(r"^(?P<code>[a-z_A-Z+]+)(?:=(?P<value>\d+)(?:<(?P<threshold>\d+))?)?$")

DISCLAIMER = (
    "Analisi basata esclusivamente su dati pubblici osservati sulle pagine Amazon.it e sul sito "
    "del brand alla data indicata. Nessun dato di vendita interno è stato utilizzato."
)


def describe_issue(raw: str) -> dict[str, str]:
    """'immagini=3<6' -> {code, problem, why, action} in italiano."""
    match = _ISSUE_RE.match(raw)
    code = match.group("code") if match else raw
    value = match.group("value") if match else None
    threshold = match.group("threshold") if match else None
    if code == "immagini":
        return {
            "code": code,
            "problem": f"Solo {value} immagini nella galleria (consigliate almeno {threshold}).",
            "why": "Chi compra online non può toccare il prodotto: le immagini sono la prima leva di conversione.",
            "action": "Aggiungere foto lifestyle, infografiche con misure e materiali, dettagli e confezione.",
        }
    if code == "bullet":
        return {
            "code": code,
            "problem": f"Solo {value} punti elenco nella descrizione breve (consigliati {threshold}).",
            "why": "I punti elenco sono ciò che il cliente legge prima di decidere; meno punti, meno argomenti.",
            "action": "Scrivere 5 punti orientati ai benefici, con le parole chiave che i clienti cercano.",
        }
    if code == "titolo_corto":
        return {
            "code": code,
            "problem": f"Titolo di soli {value} caratteri.",
            "why": "Un titolo corto spreca spazio per le parole chiave e riduce la visibilità nella ricerca.",
            "action": "Includere marca, tipo di prodotto, caratteristica principale, misura/quantità.",
        }
    if code == "titolo_lungo":
        return {
            "code": code,
            "problem": f"Titolo di {value} caratteri, oltre il limite consigliato.",
            "why": "Su mobile il titolo viene troncato e Amazon può sopprimere titoli troppo lunghi.",
            "action": "Ridurre a 150-200 caratteri tenendo le parole chiave più importanti all'inizio.",
        }
    if code == "nessun_video":
        return {
            "code": code,
            "problem": "Nessun video nella galleria.",
            "why": "Il video mostra il prodotto in uso e risponde ai dubbi prima che diventino resi.",
            "action": "Caricare un video breve (30-60 s) del prodotto in uso.",
        }
    if code == "nessun_contenuto_A+":
        return {
            "code": code,
            "problem": "Nessun contenuto A+ (descrizione arricchita).",
            "why": "Il contenuto A+ racconta il brand e confronta i prodotti; senza, la scheda sembra anonima.",
            "action": "Creare moduli A+ con storia del brand, tabella comparativa e immagini di dettaglio.",
        }
    return {"code": code, "problem": raw, "why": "", "action": ""}


def _website_issues(profile: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if profile is None:
        return issues
    if profile.mobile_friendly is False:
        issues.append({
            "code": "sito_non_mobile",
            "problem": "Il sito non risulta ottimizzato per smartphone.",
            "why": "La maggior parte del traffico e-commerce arriva da mobile.",
            "action": "Adottare un tema responsive e verificare le pagine prodotto da telefono.",
        })
    if profile.page_speed_score is not None and profile.page_speed_score < 50:
        issues.append({
            "code": "sito_lento",
            "problem": f"Velocità della home bassa (punteggio Lighthouse {profile.page_speed_score}/100).",
            "why": "Ogni secondo di caricamento in più riduce le conversioni.",
            "action": "Comprimere immagini, ridurre script di terze parti, attivare cache.",
        })
    if profile.seo_score is not None and profile.seo_score < 50:
        issues.append({
            "code": "sito_seo",
            "problem": f"SEO tecnica di base insufficiente (punteggio {profile.seo_score}/100).",
            "why": "Il sito fatica a farsi trovare su Google per il nome del brand e dei prodotti.",
            "action": "Sistemare title/description, struttura dei titoli e dati strutturati prodotto.",
        })
    return issues


async def _job_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> ResearchJob:
    job = (
        await session.execute(
            select(ResearchJob)
            .join(ResearchResult, ResearchResult.job_id == ResearchJob.id)
            .where(ResearchResult.brand_id == brand_id)
            .order_by(ResearchJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        raise ProspectNotFoundError(f"nessun job di ricerca collegato al brand {brand_id}")
    return job


async def build_audit_package(
    session: AsyncSession, brand_id: uuid.UUID, settings: Settings | None = None
) -> AuditorPackage:
    settings = settings or get_settings()
    detail = await get_prospect(session, brand_id, settings)
    job = await _job_for_brand(session, brand_id)

    products: list[dict[str, Any]] = []
    fields_seen = fields_total = 0
    for view in detail.products:
        listing = view.listing
        product = view.product
        values = {
            "rating": float(listing.rating) if listing and listing.rating is not None else None,
            "review_count": listing.review_count if listing else None,
            "bsr": listing.bsr if listing else None,
            "image_count": listing.image_count if listing else None,
        }
        fields_total += len(values)
        fields_seen += sum(1 for v in values.values() if v is not None)
        products.append({
            "asin": product.asin,
            "title": product.title,
            "url": product.product_url or f"https://www.amazon.it/dp/{product.asin}",
            **values,
            "bsr_category": listing.bsr_category if listing else None,
            "has_video": bool(listing and listing.video_count),
            "has_a_plus": bool(listing and listing.has_a_plus_content),
            "bullet_count": len(listing.bullet_points or []) if listing else None,
            "title_length": listing.title_length if listing else None,
            "observed_at": listing.observed_at.isoformat() if listing else None,
            "screenshot_id": str(view.screenshot_id) if view.screenshot_id else None,
            "issues": [describe_issue(i) for i in view.issues],
        })

    profile = detail.website_profile
    website = None
    if detail.brand.website or profile is not None:
        website = {
            "url": detail.brand.website or (profile.domain if profile else None),
            "technology": profile.technology if profile else None,
            "mobile_friendly": profile.mobile_friendly if profile else None,
            "page_speed_score": profile.page_speed_score if profile else None,
            "seo_score": profile.seo_score if profile else None,
            "issues": _website_issues(profile),
        }

    company = detail.company
    weak = sum(1 for p in products if p["issues"])
    payload: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "brand": {"id": str(detail.brand.id), "name": detail.brand.name},
        "company": {
            "legal_name": company.legal_name,
            "vat": f"{company.vat_country}{company.vat_number}" if company.vat_number else None,
            "city": company.city,
            "address": company.address,
        } if company else None,
        "summary": {
            "products_analyzed": len(products),
            "products_with_issues": weak,
            "total_issues": sum(len(p["issues"]) for p in products),
            "opportunity_levels": {k: v.level for k, v in detail.opportunities.items()},
            "overall_score": detail.overall_score,
        },
        "products": products,
        "website": website,
        "ai_recommendations": [
            {"text": r.recommendation, "type": r.opportunity_type, "priority": r.priority}
            for r in detail.recommendations
        ],
        "disclaimer": DISCLAIMER,
    }
    confidence = round(100.0 * fields_seen / fields_total, 2) if fields_total else 0.0
    package = AuditorPackage(brand_id=detail.brand.id, job_id=job.id, payload=payload, confidence=confidence)
    session.add(package)
    await session.flush()
    return package


async def latest_audit_package(session: AsyncSession, brand_id: uuid.UUID) -> AuditorPackage | None:
    return (
        await session.execute(
            select(AuditorPackage)
            .where(AuditorPackage.brand_id == brand_id)
            .order_by(AuditorPackage.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
