"""
Amazon Scout discovery — Milestone 5, delivered here as Phase I of the
refactor because Agent 01 didn't exist yet to "refactor" (docs/architecture.md,
21/09 addendum). The requirement carried over from the refactor doc's Phase I
line is literal: this agent talks ONLY to `IntelligenceSourceOrchestrator`
(via `build_default_registry()`), never importing a concrete provider class
(`AmazonProvider`, `KeepaProvider`, `WebsiteIntelligenceProvider`) directly —
check the imports below if that claim ever needs re-verifying.

Scope, deliberately narrow (per "un passo alla volta, senza saltare passi" —
do not fold later milestones in here):

* Produces ONLY raw `ResearchResult` + `Evidence` rows.
* Does NOT create or resolve `Company`/`Brand`/`AmazonSeller`/`AmazonProduct`
  records — that is Milestone 6 (brand resolution) and Milestone 7
  (deduplication). `ResearchResult.product_id`/`brand_id` stay NULL here,
  exactly as `ResearchResult`'s own docstring says ("before brand
  resolution/dedup").
* Does NOT compute opportunities or scores — Milestone 8/9.
* Does NOT call an AI model — Milestone 10. Every claim written here comes
  straight from a ProviderResult with its own data_type; nothing is
  inferred or upgraded in confidence along the way.

Two granularities of observability, matching app/models/agent_run.py's own
design note: one AgentRun row for the whole job, AgentEvent rows for
"search_completed"/"product_enriched" milestones within it, and one ToolCall
+ zero-or-more Evidence rows per orchestrator.fetch() call — so a fallback
decision (Amazon failed SALES_RANK, Keepa served it) is reconstructable from
ToolCall alone, per the same file's docstring.

Failure handling is a deliberate exception to the orchestrator's "let bugs
propagate" rule (see orchestrator.py's own docstring): an agent-run boundary
must not crash a worker mid-job. Any unhandled exception here is caught once,
at the top level, and surfaced as job.status=FAILED + job.error + an
ErrorLog row — not hidden, just not allowed to take the process down.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentEvent,
    AgentRun,
    ErrorLog,
    Evidence,
    ResearchJob,
    ResearchResult,
    ToolCall,
)
from app.models.base import utcnow
from app.models.enums import ErrorCategory, EvidenceSourceType, ResearchJobStatus
from app.tools.core.capability import Capability
from app.tools.core.orchestrator import IntelligenceSourceOrchestrator, NoProviderAvailableError
from app.tools.core.provider import ProviderResult

# SALES_RANK is included deliberately, not just PRODUCT_DATA/SELLER_DATA/
# BRAND_DATA — it's the one capability both AmazonProvider and KeepaProvider
# serve, so a real discovery run actually exercises the orchestrator's
# provider-to-provider fallback, not just the health/confidence machinery
# tested in isolation in tests/test_orchestrator_fallback.py.
_ENRICHMENT_CAPABILITIES: tuple[Capability, ...] = (
    Capability.PRODUCT_DATA,
    Capability.SELLER_DATA,
    Capability.BRAND_DATA,
    Capability.SALES_RANK,
)

# Closed mapping, on purpose: a provider name that shows up here without an
# entry is a bug (evidence with a wrong/guessed source_type is worse than a
# crash), so this is a KeyError waiting to happen rather than a silent
# .get(..., something) fallback.
_SOURCE_TYPE_BY_PROVIDER: dict[str, EvidenceSourceType] = {
    "amazon": EvidenceSourceType.AMAZON,
    "keepa": EvidenceSourceType.KEEPA,
    "website_intelligence": EvidenceSourceType.WEBSITE,
}


def _claim_text(data: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in data.items())


@dataclass
class DiscoveryOutcome:
    agent_run_id: uuid.UUID
    products_found: int
    products_enriched: int
    research_result_ids: list[uuid.UUID]


async def _run_capability_call(
    session: AsyncSession,
    agent_run_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    orchestrator: IntelligenceSourceOrchestrator,
    capability: Capability,
    **params: Any,
) -> list[ProviderResult] | None:
    """Always writes exactly one ToolCall row for this attempt. On success,
    also writes one Evidence row per ProviderResult and returns the results.
    On NoProviderAvailableError (every registered provider for this
    capability failed/was skipped), returns None instead of raising — one
    missing capability must not abort the whole discovery run."""
    started = time.monotonic()
    try:
        outcome = await orchestrator.fetch(capability, **params)
    except NoProviderAvailableError as exc:
        session.add(
            ToolCall(
                agent_run_id=agent_run_id,
                tool_name=capability.value,
                capability=capability.value,
                status="FAILED",
                duration_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
                retry_count=max(len(exc.attempts) - 1, 0),
            )
        )
        await session.flush()
        return None

    duration_ms = int((time.monotonic() - started) * 1000)
    session.add(
        ToolCall(
            agent_run_id=agent_run_id,
            tool_name=capability.value,
            capability=capability.value,
            provider=outcome.provider,
            acquisition_method=(
                outcome.results[0].acquisition_method.value if outcome.results else None
            ),
            status="SUCCESS",
            duration_ms=duration_ms,
            retry_count=max(len(outcome.attempts) - 1, 0),
        )
    )

    source_type = _SOURCE_TYPE_BY_PROVIDER[outcome.provider]
    for result in outcome.results:
        session.add(
            Evidence(
                entity_type=entity_type,
                entity_id=entity_id,
                claim=_claim_text(result.data),
                data_type=result.data_type.value,
                source=outcome.provider,
                source_url=result.source_url,
                source_type=source_type.value,
                raw_value=json.dumps(result.data, default=str),
                confidence=result.confidence,
                observed_at=result.observed_at,
                provider=outcome.provider,
                acquisition_method=result.acquisition_method.value,
            )
        )
    await session.flush()
    return outcome.results


async def run_amazon_scout_discovery(
    session: AsyncSession,
    job: ResearchJob,
    orchestrator: IntelligenceSourceOrchestrator,
    max_products: int = 20,
) -> DiscoveryOutcome:
    """Run one discovery job end-to-end: PRODUCT_SEARCH for candidate ASINs,
    then per-ASIN enrichment, writing ResearchResult/Evidence/AgentRun/
    AgentEvent/ToolCall rows as it goes. Does not commit — the caller owns
    the transaction boundary."""
    job.status = ResearchJobStatus.RUNNING.value
    job.started_at = utcnow()

    agent_run = AgentRun(
        agent_name="amazon_scout",
        job_id=job.id,
        input={"category": job.category, "marketplace": job.marketplace, "keywords": job.keywords},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    research_result_ids: list[uuid.UUID] = []
    products_found = 0
    products_enriched = 0

    try:
        search_results = await _run_capability_call(
            session,
            agent_run.id,
            entity_type="research_job",
            entity_id=str(job.id),
            orchestrator=orchestrator,
            capability=Capability.PRODUCT_SEARCH,
            category=job.category,
            marketplace=job.marketplace,
        )

        asins: list[str] = []
        if search_results:
            asins = list(search_results[0].data.get("asins", []))[:max_products]
        products_found = len(asins)

        session.add(
            AgentEvent(
                agent_run_id=agent_run.id,
                event_type="search_completed",
                payload={"asins_found": products_found},
            )
        )
        await session.flush()

        for asin in asins:
            result = ResearchResult(job_id=job.id, raw_payload={})
            session.add(result)
            await session.flush()
            research_result_ids.append(result.id)

            product_data: dict[str, Any] = {"asin": asin, "marketplace": job.marketplace}
            enriched_any = False
            for capability in _ENRICHMENT_CAPABILITIES:
                capability_results = await _run_capability_call(
                    session,
                    agent_run.id,
                    entity_type="research_result",
                    entity_id=str(result.id),
                    orchestrator=orchestrator,
                    capability=capability,
                    asin=asin,
                )
                if capability_results:
                    enriched_any = True
                    for item in capability_results:
                        product_data.update(item.data)

            result.raw_payload = product_data
            if enriched_any:
                products_enriched += 1

            session.add(
                AgentEvent(
                    agent_run_id=agent_run.id,
                    event_type="product_enriched",
                    payload={"asin": asin, "research_result_id": str(result.id)},
                )
            )
            await session.flush()

        # Vacuous 0 == 0 counts as COMPLETED, not PARTIAL — a search that
        # legitimately found nothing for this category is a completed job
        # with an empty result set, not a partial failure.
        job.status = (
            ResearchJobStatus.COMPLETED.value
            if products_enriched == products_found
            else ResearchJobStatus.PARTIAL.value
        )
        job.completed_at = utcnow()
        agent_run.status = "SUCCESS"
        agent_run.output = {"products_found": products_found, "products_enriched": products_enriched}
        await session.flush()

    except Exception as exc:  # noqa: BLE001 — agent-run boundary, see module docstring
        job.status = ResearchJobStatus.FAILED.value
        job.error = str(exc)
        job.completed_at = utcnow()
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(
            ErrorLog(
                category=ErrorCategory.UNKNOWN.value,
                message=str(exc),
                job_id=job.id,
                agent_run_id=agent_run.id,
            )
        )
        await session.flush()

    return DiscoveryOutcome(
        agent_run_id=agent_run.id,
        products_found=products_found,
        products_enriched=products_enriched,
        research_result_ids=research_result_ids,
    )
