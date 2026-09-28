"""
AI Recommendation Engine — Fase 5 (roadmap Fil/Frank, 28/09). Stesso
shape batch/`AgentRun`/`max_brands`/`truncated` di `ai_analysis_engine.py`/
`opportunity_engine.py` per lo stesso motivo: un job periodico dello
stesso tipo, non un caso speciale.

COSA fa: per ogni Brand ACTIVE, prende la riga `Opportunity` più recente
per ciascuno dei due tipi diagnostici (`AMAZON_OPPORTUNITY`,
`WEBSITE_OPPORTUNITY` — vedi sotto perché solo questi due), la passa ad
`AIAdvisor` (`app/ai/advisor.py`) insieme all'Evidence reale del brand, e
persiste ciò che torna come righe `ai_recommendations`
(`app/models/ai_recommendation.py`).

**Perché solo AMAZON_OPPORTUNITY e WEBSITE_OPPORTUNITY, mai
BUSINESS_VALUE**: le prime due sono segnali diagnostici — "qualcosa non
va, ecco cosa" (rating basso, sito non mobile-friendly, ecc.), un
`reasons` che descrive un difetto reale da correggere.
`BUSINESS_VALUE` è un segnale di TARGETING — "quanto è grande/rilevante
questo brand" (numero di prodotti, volume di recensioni) — un livello
HIGH lì è una buona notizia (vale la pena contattarli), non un difetto.
Chiedere ad AIAdvisor "come risolvo un BUSINESS_VALUE=HIGH" non avrebbe
senso: non c'è nulla da riparare. Costruire una raccomandazione per quel
tipo sarebbe esattamente il tipo di "regola senza fondamento" che
`opportunity_engine.py` già rifiuta di fare per `SELLER_FAST_FIT`.

**Solo l'Opportunity più recente per (brand, tipo)**: `Opportunity` è
append-only (nessun vincolo unique, vedi `opportunity_engine.py`) — una
query naive su "tutte le righe HIGH/MEDIUM" rigenererebbe una
raccomandazione per ogni esecuzione storica dell'opportunity engine,
inclusi risultati ormai superati. Qui si guarda solo all'ultima
valutazione per tipo, non alla storia.

**Deduplicazione per opportunity_id, non per brand**: se un consiglio
esiste già per QUELLA riga Opportunity esatta, non viene rigenerato (una
chiamata AI sprecata + un consiglio duplicato in ai_recommendations). Una
NUOVA riga Opportunity (da una futura riesecuzione dell'opportunity
engine, anche con lo stesso rule_triggered) è invece un nuovo
accertamento e riceve un nuovo tentativo — comportamento corretto, non
un bug di deduplicazione troppo aggressiva.

**Evidence, AI disabilitata, brand senza evidence**: stesso trattamento
di `ai_analysis_engine.py` — riusa qui direttamente le sue funzioni
`_evidence_for_brand`/`_to_ai_evidence_input`/`_MAX_EVIDENCE_PER_BRAND`
(stessa query ResearchResult -> Evidence, nessuna logica duplicata) e lo
stesso "AI disabilitata è un no-op valido, non un fallimento".
Diversamente da `_analyze_brand`, un'Opportunity SENZA evidence non viene
scartata prima della chiamata (AIAdvisor accetta esplicitamente una
lista di evidence vuota — il prompt lo dichiara, vedi il suo
docstring) — è un caso reale oggi: le Opportunity WEBSITE non hanno
ancora evidence propria (il gap di ingestion descritto in
opportunity_engine.py, Addendum 24), ma il rule_triggered/reasons da
soli possono già bastare per un consiglio tecnico honesto.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.advisor import AIAdvisor, OpportunityInput
from app.ai.bootstrap import build_default_ai_gateway
from app.ai.contracts import AIRecommendation as AIRecommendationDTO
from app.ai.gateway import AIGateway
from app.core.config import Settings, get_settings
from app.models import AgentEvent, AgentRun, Brand, ErrorLog, Opportunity
from app.models.ai_recommendation import AIRecommendation as AIRecommendationRow
from app.models.enums import ErrorCategory, OpportunityLevel, OpportunityType
from app.services.ai_analysis_engine import _evidence_for_brand, _to_ai_evidence_input

_ACTIONABLE_OPPORTUNITY_TYPES = frozenset({OpportunityType.AMAZON.value, OpportunityType.WEBSITE.value})
_ACTIONABLE_LEVELS = frozenset({OpportunityLevel.HIGH.value, OpportunityLevel.MEDIUM.value})


async def _latest_actionable_opportunities(session: AsyncSession, brand_id: uuid.UUID) -> list[Opportunity]:
    """La riga Opportunity più recente per ciascuno dei due tipi
    diagnostici, filtrata a HIGH/MEDIUM -- vedi module docstring."""
    rows = (
        (
            await session.execute(
                select(Opportunity)
                .where(
                    Opportunity.brand_id == brand_id,
                    Opportunity.opportunity_type.in_(_ACTIONABLE_OPPORTUNITY_TYPES),
                )
                .order_by(Opportunity.opportunity_type, Opportunity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_per_type: dict[str, Opportunity] = {}
    for row in rows:
        latest_per_type.setdefault(row.opportunity_type, row)  # righe già ordinate per created_at desc
    return [o for o in latest_per_type.values() if o.level in _ACTIONABLE_LEVELS]


async def _already_recommended(session: AsyncSession, opportunity_id: uuid.UUID) -> bool:
    existing = (
        await session.execute(
            select(AIRecommendationRow.id).where(AIRecommendationRow.opportunity_id == opportunity_id)
        )
    ).scalar_one_or_none()
    return existing is not None


@dataclass
class BrandRecommendationOutcome:
    brand_id: uuid.UUID
    recommendations_created: list[uuid.UUID] = field(default_factory=list)


@dataclass
class AIRecommendationEngineSummary:
    agent_run_id: uuid.UUID
    brands_considered: int
    recommendations_created: int
    truncated: bool = False
    ai_disabled: bool = False


def _persist_recommendation(brand_id: uuid.UUID, dto: AIRecommendationDTO) -> AIRecommendationRow:
    return AIRecommendationRow(
        entity_type="brand",
        entity_id=str(brand_id),
        opportunity_id=dto.opportunity_id,
        opportunity_type=dto.opportunity_type,
        priority=dto.priority,
        recommendation=dto.recommendation,
        reasoning_summary=dto.reasoning_summary,
        evidence_ids=dto.evidence_ids,
        model=dto.model,
        provider=dto.provider,
        generated_at=dto.created_at,
    )


async def _recommend_for_brand(
    session: AsyncSession, agent_run_id: uuid.UUID, brand: Brand, advisor: AIAdvisor
) -> BrandRecommendationOutcome:
    outcome = BrandRecommendationOutcome(brand_id=brand.id)

    opportunities = await _latest_actionable_opportunities(session, brand.id)
    if not opportunities:
        return outcome

    evidence_rows = await _evidence_for_brand(session, brand.id)
    evidence_inputs = [_to_ai_evidence_input(row) for row in evidence_rows]

    for opportunity in opportunities:
        if await _already_recommended(session, opportunity.id):
            continue  # stessa Opportunity già consigliata in una pass precedente -- vedi module docstring

        opportunity_input = OpportunityInput(
            id=opportunity.id,
            opportunity_type=opportunity.opportunity_type,
            level=opportunity.level,
            rule_triggered=opportunity.rule_triggered,
            reasons=list(opportunity.reasons),
        )
        result = await advisor.recommend(opportunity_input, evidence_inputs)

        if result.status != "OK":
            if result.status not in {"INSUFFICIENT_EVIDENCE"}:
                session.add(
                    ErrorLog(
                        category=ErrorCategory.AI_ERROR.value,
                        message=(
                            f"brand={brand.id} opportunity={opportunity.id} "
                            f"status={result.status} detail={result.error_message}"
                        ),
                        agent_run_id=agent_run_id,
                    )
                )
                await session.flush()
            continue

        for dto in result.recommendations:
            row = _persist_recommendation(brand.id, dto)
            session.add(row)
            await session.flush()
            outcome.recommendations_created.append(row.id)

            session.add(
                AgentEvent(
                    agent_run_id=agent_run_id,
                    event_type="ai_recommendation_created",
                    payload={
                        "brand_id": str(brand.id),
                        "opportunity_id": str(opportunity.id),
                        "ai_recommendation_id": str(row.id),
                        "opportunity_type": row.opportunity_type,
                        "priority": row.priority,
                        "model": row.model,
                        "provider": row.provider,
                    },
                )
            )

    return outcome


async def run_ai_recommendation_engine(
    session: AsyncSession,
    max_brands: int = 200,
    settings: Settings | None = None,
    gateway: AIGateway | None = None,
) -> AIRecommendationEngineSummary:
    """Stesso contratto di run_ai_analysis_engine — vedi quel modulo per
    il ragionamento su gateway iniettabile / ai_disabled come esito
    valido / max_brands+truncated."""
    settings = settings or get_settings()

    agent_run = AgentRun(
        agent_name="ai_recommendation_engine",
        input={"max_brands": max_brands},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    recommendations_created = 0
    brands_considered = 0
    truncated = False
    ai_disabled = False

    try:
        resolved_gateway = gateway if gateway is not None else build_default_ai_gateway(settings)
        if resolved_gateway is None:
            ai_disabled = True
            agent_run.status = "SUCCESS"
            agent_run.output = {"ai_disabled": True, "brands_considered": 0, "recommendations_created": 0}
            await session.flush()
            return AIRecommendationEngineSummary(
                agent_run_id=agent_run.id, brands_considered=0, recommendations_created=0, ai_disabled=True
            )

        advisor = AIAdvisor(resolved_gateway)

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
            outcome = await _recommend_for_brand(session, agent_run.id, brand, advisor)
            recommendations_created += len(outcome.recommendations_created)

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": brands_considered,
            "recommendations_created": recommendations_created,
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

    return AIRecommendationEngineSummary(
        agent_run_id=agent_run.id,
        brands_considered=brands_considered,
        recommendations_created=recommendations_created,
        truncated=truncated,
        ai_disabled=ai_disabled,
    )
