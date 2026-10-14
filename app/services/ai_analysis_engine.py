"""
AI Analysis Engine — Milestone 10. The first real, wired-up caller of
`AIAnalyst` (Milestone 9.5, `app/ai/analyst.py`): given a `Brand`'s real
`Evidence` rows, runs them through the AI layer and persists whatever
`AIClaim`s come back as real `ai_claims` rows (`app/models/ai_claim.py`).

Same batch/`AgentRun`/`max_brands`/`truncated` shape as
`opportunity_engine.py`/`scoring_engine.py` on purpose — this is the same
kind of periodic job, meant to run in the same pipeline (a future
scheduler, Milestone 14), not a special case.

**Deliberately does NOT touch `opportunity_engine.py` or
`scoring_engine.py`'s existing deterministic logic.** `Score`'s own model
docstring ("deterministic scoring engine + validated AI signals") and
`config/default.yaml`'s `scoring.confidence.decrease_on: [..., ai_inference]`
both foreshadow a future step where an `AIClaim`'s confidence blends into
a `Score`'s confidence — that wiring is NOT built in this pass. Two
reasons, not one: (1) exactly HOW an AI claim's confidence should combine
with a deterministic rule's confidence was never specified anywhere in
this engagement (not in the original spec sections referenced by
`scoring_engine.py`, not by Fil) — inventing a blending formula here would
be exactly the kind of unspecified-criteria guess `opportunity_engine.py`'s
own docstring already refused to do for `SELLER_FAST_FIT`; (2)
`scoring_engine.py` has 13 passing, precisely-asserted tests
(`test_healthy_brand_scores_none_amazon_opportunity_and_high_business_value`
asserts `confidence == 85.0` exactly) that a silent blending change would
put at risk for no specified gain. This module is additive: it writes
`ai_claims` rows that exist independently and are ready for a future,
explicitly-specified wiring step — documented here as a known next
decision, not silently done or silently skipped.

Similarly NOT built here, for the same "documented gap, not invented
criteria" reason: `AmazonSeller.verified` AI-assisted confirmation
(foreshadowed in `app/models/amazon.py`'s own docstring — "the
BrandResolver, rules first, then AI") and AI-assisted review of
near-duplicate brand candidates (foreshadowed in
`brand_deduplication.py`'s docstring). Both would reuse this exact same
`AIAnalyst` call shape against a differently-scoped evidence set — real
follow-on work, not attempted here without move-the-goalposts scope
creep beyond what "the AI layer, wired to real evidence" concretely
means.

**Evidence text, not just ids, this time**: `scoring_engine.py`'s own
`_evidence_ids_for_brand` returns only `Evidence.id` values (all it
needs); `AIAnalyst.analyze()` needs the actual claim text to reason over.
This module reads full `Evidence` rows through the same `ResearchResult`
chain independently rather than refactoring that shared helper — touching
`scoring_engine.py` for a cosmetic gain (a shared helper) is the same
"preserve working, tested code" call already made for
`opportunity_engine.py` in Milestone 8.5's Data Contracts.

**Evidence is capped per brand** (`_MAX_EVIDENCE_PER_BRAND`, most recent
first by `observed_at`) — `config/default.yaml`'s `ai.local.num_ctx: 2048`
is a real, small context budget for the target 4GB VPS; sending an
unbounded evidence set to the model would either overflow it or crowd out
the guardrail system prompt. This is a real, deliberate limit, not an
oversight.

**A brand with zero Evidence rows is never sent to the AI layer at all**
-- `AIAnalyst.analyze([])` would correctly return `INSUFFICIENT_EVIDENCE`
for it, but calling the gateway just to get that answer wastes a real
generation slot on a 4GB box running one generation at a time
(`max_concurrency: 1`). This module short-circuits before the call,
exactly the same efficiency reasoning `scoring_engine.py`'s own
`"nothing scored yet for this brand"` early return already uses.

**AI layer disabled (`ai.enabled: false`) is a valid, first-class
outcome, not a failure** -- `build_default_ai_gateway()` returning `None`
(Milestone 9.5's own contract) makes this run a documented no-op rather
than crashing or silently skipping without saying so.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.analyst import AIAnalyst, AIEvidenceInput
from app.ai.bootstrap import build_default_ai_gateway
from app.ai.contracts import AIClaim as AIClaimDTO
from app.ai.gateway import AIGateway
from app.core.config import Settings, get_settings
from app.models import AgentEvent, AgentRun, Brand, ErrorLog, Evidence, ResearchResult
from app.models.ai_claim import AIClaim as AIClaimRow
from app.models.enums import ErrorCategory

_MAX_EVIDENCE_PER_BRAND = 20


async def _evidence_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> list[Evidence]:
    """Same ResearchResult.brand_id -> Evidence.entity_id chain as
    scoring_engine.py's _evidence_ids_for_brand, kept as an independent
    read here (returns full rows, not just ids) -- see module docstring."""
    research_result_ids = (
        (await session.execute(select(ResearchResult.id).where(ResearchResult.brand_id == brand_id)))
        .scalars()
        .all()
    )
    if not research_result_ids:
        return []
    entity_ids = [str(rid) for rid in research_result_ids]
    rows = (
        (
            await session.execute(
                select(Evidence)
                .where(Evidence.entity_type == "research_result", Evidence.entity_id.in_(entity_ids))
                .order_by(Evidence.observed_at.desc())
                .limit(_MAX_EVIDENCE_PER_BRAND)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _to_ai_evidence_input(evidence: Evidence) -> AIEvidenceInput:
    return AIEvidenceInput(
        id=str(evidence.id),
        claim=evidence.claim,
        data_type=evidence.data_type,
        source=evidence.source,
        confidence=float(evidence.confidence),
    )


@dataclass
class BrandAnalysisOutcome:
    brand_id: uuid.UUID
    claims_created: list[uuid.UUID] = field(default_factory=list)
    status: str = "SKIPPED_NO_EVIDENCE"  # or the AIAnalystOutcome.status this brand produced


@dataclass
class AIAnalysisEngineSummary:
    agent_run_id: uuid.UUID
    brands_considered: int
    claims_created: int
    truncated: bool = False
    ai_disabled: bool = False
    """True when config/default.yaml's ai.enabled is false -- a valid,
    documented no-op run, not a failure (see module docstring)."""


async def _analyze_brand(
    session: AsyncSession, agent_run_id: uuid.UUID, brand: Brand, analyst: AIAnalyst
) -> BrandAnalysisOutcome:
    outcome = BrandAnalysisOutcome(brand_id=brand.id)

    evidence_rows = await _evidence_for_brand(session, brand.id)
    if not evidence_rows:
        return outcome  # SKIPPED_NO_EVIDENCE default -- no AI call made, see module docstring

    evidence_inputs = [_to_ai_evidence_input(row) for row in evidence_rows]
    result = await analyst.analyze(evidence_inputs)
    outcome.status = result.status

    if result.status != "OK":
        # AI_UNAVAILABLE / MODEL_UNAVAILABLE / AI_TIMEOUT / VALIDATION_ERROR /
        # INSUFFICIENT_EVIDENCE -- all controlled outcomes AIAnalyst itself
        # already turned from an exception into a status (Milestone 9.5).
        # Logged, not raised: one brand's AI failure must never abort the
        # batch for every other brand (section 24's "must not crash
        # unrelated functionality", applied at the batch-loop level here).
        if result.status not in {"INSUFFICIENT_EVIDENCE"}:
            session.add(
                ErrorLog(
                    category=ErrorCategory.AI_ERROR.value,
                    message=f"brand={brand.id} status={result.status} detail={result.error_message}",
                    agent_run_id=agent_run_id,
                )
            )
            await session.flush()
        return outcome

    for claim_dto in result.claims:
        row = _persist_claim(brand.id, claim_dto)
        session.add(row)
        await session.flush()
        outcome.claims_created.append(row.id)

        session.add(
            AgentEvent(
                agent_run_id=agent_run_id,
                event_type="ai_claim_created",
                payload={
                    "brand_id": str(brand.id),
                    "ai_claim_id": str(row.id),
                    "data_type": row.data_type,
                    "confidence": float(row.confidence),
                    "model": row.model,
                    "provider": row.provider,
                },
            )
        )

    return outcome


def _persist_claim(brand_id: uuid.UUID, claim_dto: AIClaimDTO) -> AIClaimRow:
    return AIClaimRow(
        entity_type="brand",
        entity_id=str(brand_id),
        claim=claim_dto.claim,
        data_type=claim_dto.data_type.value,
        confidence=claim_dto.confidence,
        evidence_ids=claim_dto.evidence_ids,
        reasoning_summary=claim_dto.reasoning_summary,
        model=claim_dto.model,
        provider=claim_dto.provider,
        generated_at=claim_dto.created_at,
    )


async def run_ai_analysis_engine(
    session: AsyncSession,
    max_brands: int = 200,
    settings: Settings | None = None,
    gateway: AIGateway | None = None,
) -> AIAnalysisEngineSummary:
    """Analyzes up to `max_brands` ACTIVE Brand rows with real Evidence,
    writing `ai_claims` rows for whatever `AIAnalyst` returns. `gateway`
    is injectable (tests pass a MockAIProvider-backed one); left as
    `None` in production it resolves via `build_default_ai_gateway()`,
    which itself may return `None` if `ai.enabled: false` -- that is a
    valid no-op run, reported via `summary.ai_disabled`, not a failure.
    """
    settings = settings or get_settings()

    agent_run = AgentRun(
        agent_name="ai_analysis_engine",
        input={"max_brands": max_brands},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    claims_created = 0
    brands_considered = 0
    truncated = False
    ai_disabled = False

    try:
        resolved_gateway = gateway if gateway is not None else build_default_ai_gateway(settings)
        if resolved_gateway is None:
            ai_disabled = True
            agent_run.status = "SUCCESS"
            agent_run.output = {"ai_disabled": True, "brands_considered": 0, "claims_created": 0}
            await session.flush()
            return AIAnalysisEngineSummary(
                agent_run_id=agent_run.id, brands_considered=0, claims_created=0, ai_disabled=True
            )

        analyst = AIAnalyst(resolved_gateway)

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
            outcome = await _analyze_brand(session, agent_run.id, brand, analyst)
            claims_created += len(outcome.claims_created)

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": brands_considered,
            "claims_created": claims_created,
            "truncated": truncated,
            "ai_disabled": False,
        }
        await session.flush()

    except Exception as exc:  # noqa: BLE001 -- same agent-run boundary discipline as opportunity_engine.py
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

    return AIAnalysisEngineSummary(
        agent_run_id=agent_run.id,
        brands_considered=brands_considered,
        claims_created=claims_created,
        truncated=truncated,
        ai_disabled=ai_disabled,
    )
