"""
API request/response schemas — Milestone 13. Deliberately thin: every
field here mirrors something `app/cli.py` (Milestone 12) already prints to
stdout for the same operation, not a reinvention of what the API "should"
expose. The CLI is the closest existing precedent for this project's real
API surface (see app/main.py's own docstring, which has named
"research jobs, prospects, brands, agent runs" as Milestone 13's scope
since Milestone 1) — `health`, `pipeline run`, `jobs list`, `brands list`
map onto GET /health, POST /jobs, GET /jobs, GET /brands respectively.

Flattened, not the raw dataclasses: `PipelineSummary` and its six stage
summaries (app/services/pipeline_orchestrator.py) carry `agent_run_id`
lists of UUIDs the CLI never prints either — this file exposes exactly the
scalar counts the CLI's own `typer.echo(...)` lines already surface,
nothing invented beyond that.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Mirrors `dinamik health`'s three checks exactly — config, real DB
    connectivity, real AI gateway status. The old bare `/health` (config
    only) is superseded by this, not kept alongside it: it always reported
    "healthy" without checking anything that could actually be unhealthy,
    which is worse than no health check at all once DB/AI status exist to
    check."""

    status: str
    environment: str
    mock_mode: bool
    database: str
    ai: str


class JobCreateRequest(BaseModel):
    """Same parameters as `dinamik pipeline run`'s CLI options, same
    defaults."""

    name: str
    marketplace: str = "IT"
    category: str | None = None
    subcategory: str | None = None
    max_products: int = Field(default=20, gt=0)
    max_batch: int = Field(default=200, gt=0)


class DiscoveryStageResult(BaseModel):
    products_found: int
    products_enriched: int


class BrandResolutionStageResult(BaseModel):
    processed: int
    resolved: int
    unresolved: int
    conflicts: int


class BrandDeduplicationStageResult(BaseModel):
    merged_pairs: int
    candidates_flagged: int


class OpportunityEngineStageResult(BaseModel):
    opportunities_created: int


class ScoringEngineStageResult(BaseModel):
    scores_created: int


class AIAnalysisStageResult(BaseModel):
    ai_disabled: bool
    claims_created: int


class JobRunResponse(BaseModel):
    """Response for POST /jobs — the ResearchJob was created AND the full
    Milestone 11 pipeline already ran against it synchronously by the time
    this returns (same contract as `dinamik pipeline run`; there is no
    background worker yet — that's Milestone 14, not this one)."""

    job_id: uuid.UUID
    agent_run_id: uuid.UUID
    discovery: DiscoveryStageResult
    brand_resolution: BrandResolutionStageResult
    brand_deduplication: BrandDeduplicationStageResult
    opportunity_engine: OpportunityEngineStageResult
    scoring_engine: ScoringEngineStageResult
    ai_analysis_engine: AIAnalysisStageResult


class JobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    status: str
    marketplace: str
    name: str


class BrandListItem(BaseModel):
    id: uuid.UUID
    name: str
    scores: dict[str, float]


# --- 2026-09-22: ops dashboard requirement -----------------------------
#
# The four schemas/endpoints below (Opportunity, AIClaim, WeeklyScan x2)
# are genuinely NEW read surface, unlike everything above this line --
# this file's own module docstring is explicit that Milestone 13's original
# four endpoints mirror an existing CLI command 1:1, and this project has
# deliberately avoided inventing API surface beyond that precedent before
# now (see app/main.py's docstring on why no "prospects"/"agent runs"
# endpoints exist). WeeklyScan is the one exception that stays close to
# that precedent -- `dinamik scan status`/`scan history` already print
# every field below, this just mirrors them over HTTP the same way
# GET /brands mirrors `dinamik brands list`. Opportunity and AIClaim have
# NO CLI precedent at all: nothing today lists opportunities or AI claims
# standalone, only nested inside a job's synchronous POST /jobs response
# (counts only) or a brand's `reasons`/`evidence_ids` JSON. These two are
# added because Fil explicitly asked for an ops dashboard that shows
# "opportunities" and "AI analyses" as first-class, browsable things --
# the same kind of explicit, stated requirement that justified adding the
# Weekly Scan feature out-of-sequence on 2026-09-21, not scope invented on
# this session's own initiative.


class OpportunityListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    brand_id: uuid.UUID
    opportunity_type: str
    level: str
    rule_triggered: str
    reasons: list


class OpportunityListItemWithBrand(OpportunityListItem):
    brand_name: str


class AIClaimListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    entity_type: str
    entity_id: str
    claim: str
    data_type: str
    confidence: float
    reasoning_summary: str
    evidence_ids: list
    model: str
    provider: str
    generated_at: datetime


class WeeklyScanItem(BaseModel):
    """Same field set `dinamik scan status`/`scan history` already print
    (app/cli.py) -- used for both the list and the single-scan response,
    same "one shape, two endpoints" pattern JobListItem already uses for
    GET /jobs and GET /jobs/{job_id}."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_start: datetime
    period_end: datetime
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    research_job_id: uuid.UUID | None
    universe_size: int
    evidence_created: int
    changes_detected: int
    changes_significant: int
    opportunities_new: int
    opportunities_improving: int
    opportunities_deteriorating: int
    opportunities_resolved: int
    opportunities_continuing: int
    errors: list
    metrics: dict
    report_path: str | None
