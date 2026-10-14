"""
Scoring engine — Milestone 9.

Deterministic only, no AI (Milestone 10 — "validated AI signals" in
Score's own docstring is a claim about what THAT milestone adds on top of
this one, not something this module does). Consumes `Opportunity` rows
already written by `opportunity_engine.py` (Milestone 8) and converts each
brand's most recent assessment per dimension into a numeric `Score` row —
`score`/`confidence` (0-100), `reasons`, `evidence_ids`, `weight_used` from
`config/default.yaml`'s `scoring.weights` at calc time, per the model's own
section-31/33 docstring.

This does NOT re-derive opportunity signals from `AmazonProduct`/
`AmazonSeller` rows itself — it reads the `Opportunity` rows Milestone 8
already computed and persisted. Re-running the same rating/review logic
here would duplicate `opportunity_engine.py`'s business logic in a second
place, with the two inevitably drifting; reading its output instead keeps
one source of truth for "what did we conclude about this brand" and lets
this module focus on its own job — turning a categorical conclusion into a
weighted, traceable number.

**Only the two `OpportunityType` values `opportunity_engine.py` actually
implements get a Score row — `AMAZON_OPPORTUNITY` and `BUSINESS_VALUE`.**
The other four have no `Opportunity` rows to read (no detector runs for
them — see that module's own docstring), so nothing is scored for them.
This is the same "absence over fabrication" discipline as every prior
milestone: a `Score` row for `SELLER_FAST_FIT` with no real criteria behind
it would be a number that looks authoritative and isn't.

**Level → numeric score mapping is a deliberately coarse, documented
scale, not a calibrated business judgment**: it exists so `Opportunity`'s
categorical `level` (HIGH/MEDIUM/LOW/POSSIBLE/NONE) can be weighted and
compared across dimensions using `config/default.yaml`'s per-dimension
weights, not because "MEDIUM = 60.0" is a claim about real-world
conversion probability or dollar value. Recalibrating it needs real
outcome data (did a HIGH-scored brand actually convert?) that does not
exist yet.

**Confidence is two-tier and honestly limited**: `85.0` when the
underlying `Opportunity` was computed from real rating/review data,
`30.0` when it wasn't (`opportunity_engine.py`'s own
`"insufficient_product_data"` rule name is the signal — see
`config/default.yaml`'s `scoring.confidence.decrease_on:
incomplete_information`, the one factor this module can actually check
deterministically today). It does NOT reflect real-vs-mock data
(`ProviderResult.is_mock` is never persisted onto `AmazonProduct` rows, so
this layer has no way to know — a real gap, noted here rather than
silently assumed away) — every one of `Settings.score_weights().confidence.
increase_on`/`decrease_on`'s other listed factors (`multiple_sources_agree`,
`recent_data`, `ai_inference`, ...) needs either multi-provider
cross-referencing or the AI layer neither of which exist yet (Milestone 10).

**`evidence_ids` traced through `ResearchResult`, not invented**: `Evidence`
rows are written against `entity_type="research_result"`
(`app/agents/amazon_scout/discovery.py`), not against a brand directly.
This module resolves a brand's evidence by finding the `ResearchResult`
rows Milestone 6 resolved onto it (`ResearchResult.brand_id`) and
collecting the `Evidence` rows attached to those — a real, queryable
chain, not a guess.

**No composite/aggregate score is computed or persisted.** The weighted
sum across dimensions is a value a future consumer (CLI/API, Milestone
12/13) can compute on demand from the per-dimension `Score` rows plus
`config/default.yaml`'s weights — both already available — so persisting
a derived number here would be redundant state to keep in sync, not new
information.

Same append-only design as `Opportunity` (no unique constraint on
`(brand_id, score_type)` — confirmed in `app/models/opportunity.py`):
re-running this engine writes new `Score` rows, it does not update
existing ones.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    Brand,
    ErrorLog,
    Evidence,
    Opportunity,
    ResearchResult,
    Score,
)
from app.models.enums import ErrorCategory, OpportunityLevel

_INCOMPLETE_DATA_RULE = "insufficient_product_data"  # opportunity_engine.py's own sentinel rule name
_CONFIDENCE_FULL_DATA = 85.0
_CONFIDENCE_INCOMPLETE_DATA = 30.0

_LEVEL_TO_SCORE: dict[str, float] = {
    OpportunityLevel.NONE.value: 0.0,
    OpportunityLevel.LOW.value: 25.0,
    OpportunityLevel.POSSIBLE.value: 40.0,
    OpportunityLevel.MEDIUM.value: 60.0,
    OpportunityLevel.HIGH.value: 90.0,
}


def _config_key(opportunity_type: str) -> str:
    """OpportunityType values are SHOUTING_SNAKE_CASE
    ("AMAZON_OPPORTUNITY"); config/default.yaml's scoring.weights keys are
    lower_snake_case ("amazon_opportunity") -- opportunity_engine.py
    already relies on this exact lowercase mapping via
    Settings.opportunity_rule_config()'s call sites, this mirrors it for
    scoring.weights instead of opportunity_rules."""
    return opportunity_type.lower()


async def _latest_opportunities_by_type(session: AsyncSession, brand_id: uuid.UUID) -> dict[str, Opportunity]:
    """Opportunity is append-only (Milestone 8) -- this collapses to the
    most recently created row per opportunity_type, ordered so the first
    row seen per type in a DESC scan is the latest."""
    rows = (
        (
            await session.execute(
                select(Opportunity)
                .where(Opportunity.brand_id == brand_id)
                .order_by(Opportunity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[str, Opportunity] = {}
    for row in rows:
        latest.setdefault(row.opportunity_type, row)
    return latest


async def _evidence_ids_for_brand(session: AsyncSession, brand_id: uuid.UUID) -> list[str]:
    """Evidence is written against entity_type="research_result"
    (app/agents/amazon_scout/discovery.py) -- this follows
    ResearchResult.brand_id (set by Milestone 6's brand_resolution.py) to
    find which research_result rows resolved onto this brand, then
    collects the Evidence rows attached to those. A brand resolved from
    zero research results (should not happen in practice, but not
    impossible if data is manipulated directly) legitimately yields []."""
    research_result_ids = (
        (await session.execute(select(ResearchResult.id).where(ResearchResult.brand_id == brand_id)))
        .scalars()
        .all()
    )
    if not research_result_ids:
        return []
    entity_ids = [str(rid) for rid in research_result_ids]
    evidence_ids = (
        (
            await session.execute(
                select(Evidence.id).where(
                    Evidence.entity_type == "research_result", Evidence.entity_id.in_(entity_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    return [str(eid) for eid in evidence_ids]


@dataclass
class BrandScoringOutcome:
    brand_id: uuid.UUID
    scores_created: list[uuid.UUID] = field(default_factory=list)


@dataclass
class ScoringEngineSummary:
    agent_run_id: uuid.UUID
    brands_considered: int
    scores_created: int
    truncated: bool = False
    """Same meaning/pattern as OpportunityEngineSummary.truncated -- true
    when more than max_brands ACTIVE brands existed and only the first
    max_brands (ordered by first_seen_at) were considered."""


async def _score_brand(
    session: AsyncSession, agent_run_id: uuid.UUID, brand: Brand, settings: Settings
) -> BrandScoringOutcome:
    outcome = BrandScoringOutcome(brand_id=brand.id)

    latest_opportunities = await _latest_opportunities_by_type(session, brand.id)
    if not latest_opportunities:
        return outcome  # nothing scored yet for this brand -- nothing to convert into a Score

    evidence_ids = await _evidence_ids_for_brand(session, brand.id)
    weights = settings.score_weights()

    for opportunity_type, opportunity in latest_opportunities.items():
        score_value = _LEVEL_TO_SCORE[opportunity.level]
        confidence = (
            _CONFIDENCE_INCOMPLETE_DATA
            if opportunity.rule_triggered == _INCOMPLETE_DATA_RULE
            else _CONFIDENCE_FULL_DATA
        )
        weight_used = float(weights.get(_config_key(opportunity_type), 0.0))

        score = Score(
            brand_id=brand.id,
            score_type=opportunity_type,
            score=score_value,
            confidence=confidence,
            reasons=[
                *opportunity.reasons,
                (
                    f"derived from opportunity {opportunity.id} (level={opportunity.level}, "
                    f"rule={opportunity.rule_triggered})"
                ),
            ],
            evidence_ids=evidence_ids,
            weight_used=weight_used,
        )
        session.add(score)
        await session.flush()
        outcome.scores_created.append(score.id)

        session.add(
            AgentEvent(
                agent_run_id=agent_run_id,
                event_type="score_created",
                payload={
                    "brand_id": str(brand.id),
                    "score_id": str(score.id),
                    "score_type": opportunity_type,
                    "score": score_value,
                    "confidence": confidence,
                    "weight_used": weight_used,
                },
            )
        )

    return outcome


async def run_scoring_engine(
    session: AsyncSession, max_brands: int = 200, settings: Settings | None = None
) -> ScoringEngineSummary:
    """Scores up to `max_brands` ACTIVE Brand rows (same MERGED-exclusion
    and cap/truncated pattern as run_opportunity_engine) by converting
    their latest Opportunity rows into Score rows. Meant to run after
    run_opportunity_engine in the same job/scheduler tick, but does not
    assume it did -- it reads persisted Opportunity rows fresh, so it is
    safe to call independently or repeatedly (a future scheduler,
    Milestone 14, the same story as every prior engine in this pipeline).
    """
    settings = settings or get_settings()

    agent_run = AgentRun(
        agent_name="scoring_engine",
        input={"max_brands": max_brands},
        status="RUNNING",
    )
    session.add(agent_run)
    await session.flush()

    scores_created = 0
    brands_considered = 0
    truncated = False

    try:
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
            outcome = await _score_brand(session, agent_run.id, brand, settings)
            scores_created += len(outcome.scores_created)

        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": brands_considered,
            "scores_created": scores_created,
            "truncated": truncated,
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

    return ScoringEngineSummary(
        agent_run_id=agent_run.id,
        brands_considered=brands_considered,
        scores_created=scores_created,
        truncated=truncated,
    )
