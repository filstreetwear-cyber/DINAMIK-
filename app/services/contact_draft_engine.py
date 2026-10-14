"""
Contact Draft Engine — Fase 6 (roadmap Fil/Frank, 28/09). Stesso shape
batch/`AgentRun`/`max_brands`/`truncated`/`ai_disabled` degli altri due
motori AI (`ai_analysis_engine.py`, `ai_recommendation_engine.py`).

COSA fa: per ogni Brand ACTIVE con almeno una riga `AIRecommendation`
(Fase 5) e NESSUNA `ContactDraft` ancora, raccoglie tutte le sue
`AIRecommendation` correnti, le passa ad `AIOutreachWriter`
(`app/ai/outreach_writer.py`), persiste il risultato come riga
`ContactDraft` (`app/models/contact_draft.py`) e scrive
`ProspectStatus(status=NEEDS_REVIEW, changed_by="system")` — il primo
writer reale per quel modello, vedi il docstring di ContactDraft per
perché questo chiude quel gap invece di aggiungerne uno nuovo.

**Deduplica per brand, non per singola AIRecommendation**: una volta che
un brand ha una ContactDraft, non ne viene generata una seconda in una
pass successiva, anche se nel frattempo sono arrivate nuove
AIRecommendation. Scelta deliberata, non una svista: rigenerare bozze ad
ogni nuova raccomandazione produrrebbe spam di bozze per lo stesso brand
che un umano deve poi districare; una bozza esistente è il punto di
revisione umana, non qualcosa da sovrascrivere automaticamente. Se in
futuro serve rigenerare (es. dopo che una bozza è stata REJECTED), è
un'estensione esplicita da costruire quando richiesta, non qui per
scope-guessing.

**Nessuna evidence qui**: a differenza di AIAnalyst/AIAdvisor,
AIOutreachWriter non cita evidence_ids — lavora solo sul testo già
vagliato delle AIRecommendation (che a loro volta erano evidence-grounded
in Fase 5). Ripetere quella validazione qui sarebbe ridondante, non una
garanzia in più.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.bootstrap import build_default_ai_gateway
from app.ai.gateway import AIGateway
from app.ai.outreach_writer import AIOutreachWriter, RecommendationInput
from app.core.config import Settings, get_settings
from app.models import AgentEvent, AgentRun, Brand, ErrorLog, ProspectStatus
from app.models.ai_recommendation import AIRecommendation
from app.models.contact_draft import ContactDraft
from app.models.enums import ErrorCategory, ProspectQualification


async def _recommendations_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> list[AIRecommendation]:
    rows = (
        (
            await session.execute(
                select(AIRecommendation)
                .where(AIRecommendation.entity_type == "brand", AIRecommendation.entity_id == str(brand_id))
                .order_by(AIRecommendation.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _has_existing_draft(session: AsyncSession, brand_id: uuid.UUID) -> bool:
    existing = (
        await session.execute(select(ContactDraft.id).where(ContactDraft.brand_id == brand_id))
    ).scalar_one_or_none()
    return existing is not None


@dataclass
class BrandDraftOutcome:
    brand_id: uuid.UUID
    draft_id: uuid.UUID | None = None


@dataclass
class ContactDraftEngineSummary:
    agent_run_id: uuid.UUID
    brands_considered: int
    drafts_created: int
    truncated: bool = False
    ai_disabled: bool = False


async def _draft_for_brand(
    session: AsyncSession, agent_run_id: uuid.UUID, brand: Brand, writer: AIOutreachWriter
) -> BrandDraftOutcome:
    outcome = BrandDraftOutcome(brand_id=brand.id)

    if await _has_existing_draft(session, brand.id):
        return outcome  # gia' generata in una pass precedente -- vedi module docstring

    recommendations = await _recommendations_for_brand(session, brand.id)
    if not recommendations:
        return outcome  # niente su cui basare una bozza -- nessuna chiamata AI sprecata

    recommendation_inputs = [
        RecommendationInput(recommendation=r.recommendation, opportunity_type=r.opportunity_type, priority=r.priority)
        for r in recommendations
    ]
    result = await writer.draft(brand.name, recommendation_inputs)

    if result.status != "OK":
        if result.status not in {"INSUFFICIENT_RECOMMENDATIONS"}:
            session.add(
                ErrorLog(
                    category=ErrorCategory.AI_ERROR.value,
                    message=f"brand={brand.id} status={result.status} detail={result.error_message}",
                    agent_run_id=agent_run_id,
                )
            )
            await session.flush()
        return outcome

    # Garantito da status == "OK" (vedi AIOutreachWriter.draft): nessuno di
    # questi e' None quando lo status e' OK.
    assert result.subject is not None
    assert result.body is not None
    assert result.model is not None
    assert result.provider is not None
    assert result.generated_at is not None

    draft = ContactDraft(
        brand_id=brand.id,
        subject=result.subject,
        body=result.body,
        recommendation_ids=[str(r.id) for r in recommendations],
        model=result.model,
        provider=result.provider,
        generated_at=result.generated_at,
    )
    session.add(draft)
    await session.flush()
    outcome.draft_id = draft.id

    # Primo writer reale per ProspectStatus (Milestone 6, mai popolato finora
    # -- vedi docstring di ContactDraft): la bozza esiste, un umano deve
    # rivederla prima che qualunque contatto reale avvenga.
    session.add(
        ProspectStatus(
            brand_id=brand.id,
            status=ProspectQualification.NEEDS_REVIEW.value,
            reason=f"contact draft generated (ai_recommendation ids: {[str(r.id) for r in recommendations]})",
            changed_by="system",
        )
    )

    session.add(
        AgentEvent(
            agent_run_id=agent_run_id,
            event_type="contact_draft_created",
            payload={
                "brand_id": str(brand.id),
                "contact_draft_id": str(draft.id),
                "recommendation_ids": draft.recommendation_ids,
                "model": draft.model,
                "provider": draft.provider,
            },
        )
    )
    await session.flush()

    return outcome


async def run_contact_draft_engine(
    session: AsyncSession,
    max_brands: int = 200,
    settings: Settings | None = None,
    gateway: AIGateway | None = None,
) -> ContactDraftEngineSummary:
    """Stesso contratto di run_ai_analysis_engine/run_ai_recommendation_engine
    -- vedi quei moduli per il ragionamento su gateway iniettabile /
    ai_disabled come esito valido / max_brands+truncated."""
    settings = settings or get_settings()

    agent_run = AgentRun(
        agent_name="contact_draft_engine",
        input={"max_brands": max_brands},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    drafts_created = 0
    brands_considered = 0
    truncated = False
    ai_disabled = False

    try:
        resolved_gateway = gateway if gateway is not None else build_default_ai_gateway(settings)
        if resolved_gateway is None:
            ai_disabled = True
            agent_run.status = "SUCCESS"
            agent_run.output = {"ai_disabled": True, "brands_considered": 0, "drafts_created": 0}
            await session.flush()
            return ContactDraftEngineSummary(
                agent_run_id=agent_run.id, brands_considered=0, drafts_created=0, ai_disabled=True
            )

        writer = AIOutreachWriter(resolved_gateway)

        all_active = (
            (
                await session.execute(
                    select(Brand).where(Brand.status == "ACTIVE").order_by(Brand.first_seen_at, Brand.id)
                )
            )
            .scalars()
            .all()
        )
        truncated = len(all_active) > max_brands
        brands = list(all_active[:max_brands])
        brands_considered = len(brands)

        for brand in brands:
            outcome = await _draft_for_brand(session, agent_run.id, brand, writer)
            if outcome.draft_id is not None:
                drafts_created += 1

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": brands_considered,
            "drafts_created": drafts_created,
            "truncated": truncated,
            "ai_disabled": False,
        }
        await session.flush()

    except Exception as exc:  # noqa: BLE001 -- stessa disciplina di agent-run boundary degli altri motori
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(
            ErrorLog(
                category=ErrorCategory.UNKNOWN.value,
                message=str(exc),
                agent_run_id=agent_run.id,
            )
        )
        await session.flush()

    return ContactDraftEngineSummary(
        agent_run_id=agent_run.id,
        brands_considered=brands_considered,
        drafts_created=drafts_created,
        truncated=truncated,
        ai_disabled=ai_disabled,
    )
