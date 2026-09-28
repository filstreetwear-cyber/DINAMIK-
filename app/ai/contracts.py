"""
AIClaim — Milestone 9.5, section 11/19. Lives in `app/ai/`, not
`app/schemas/contracts.py`: that file's own docstring scopes it to Data
Contracts for acquisition (a normalized view of something a DATA provider
observed — Amazon/Keepa/Website). An `AIClaim` is a different kind of
object entirely — an AI AGENT's *interpretation* of evidence, never a
direct observation — and section 23's mandatory DATA-agent/AI-agent
separation is exactly the line that would blur if both lived in one file
under one import path.

Two classes, not one, because the boundary between "what the model is
allowed to produce" and "what the system asserts" must be enforceable, not
just documented:

* `AIClaimDraft` — the ONLY shape handed to the LLM as its structured-output
  target (AIProvider.structured_generate's `schema`). Every field here is
  something the model itself decides: the claim text, its data_type
  classification, its own confidence, which evidence_ids it drew on, a short
  reasoning summary. Nothing here is provenance metadata, because an LLM
  cannot be trusted to correctly self-report which model/provider it is —
  it should never be asked to.
* `AIClaim` — draft + system-assigned provenance (`id`, `created_at`,
  `model`, `provider`) the CALLER (AIAnalyst) stamps on afterwards, from the
  real AIGenerationResult the provider returned, never from anything the
  model wrote in its own output. This is what section 19 means by "makes
  future audits possible": model/provider provenance on an AIClaim is a
  guarantee about who is answerable for it, so it cannot be self-reported.

`data_type` reuses `DataType` (app/models/enums.py) — the same enum
`Evidence` rows already use. Section 15's rule ("never silently promote an
ESTIMATE to a FACT") is enforced by NOT giving the model a separate,
AI-specific vocabulary to invent meanings for; it must classify into the
same four buckets every other layer of this project already respects.

`evidence_ids` are opaque strings here (not validated as real UUIDs
against the DB at this layer) on purpose: this module has no session/DB
access, and shouldn't need one to define a contract shape. AIAnalyst
(app/ai/analyst.py) is the one place that cross-checks every id here
against the real input evidence set and rejects/flags anything invented —
see that module's own docstring on why that check belongs there, not here.

Fase 5 (28/09) adds `AIRecommendationDraft`/`AIRecommendation`, the same
draft-vs-stamped shape for a DIFFERENT AI role (`AIAdvisor`,
app/ai/advisor.py) that produces an operative "what to fix and how"
recommendation instead of an evidence interpretation. Kept as separate
classes rather than new fields on AIClaim/AIClaimDraft — see
app/models/ai_recommendation.py's docstring for why blurring that line
would have weakened AIAnalyst's own explicit "never produce a
recommendation" guardrail instead of adding a new, independently-guarded
one.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DataType

# Model-facing outcome the AIAnalyst may produce in place of (or alongside
# zero) AIClaimDraft rows -- section 15: "if evidence is missing or
# insufficient, the AI must explicitly say INSUFFICIENT_EVIDENCE rather
# than guess." Not a DataType value on purpose: it is not a classification
# of a claim's certainty, it is a statement that no claim could responsibly
# be made at all.
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class AIClaimDraft(BaseModel):
    """The structured-output schema handed to the LLM. Every field is
    model-authored; nothing here is trusted provenance — see module
    docstring."""

    model_config = ConfigDict(frozen=True)

    claim: str = Field(min_length=1, max_length=1000)
    data_type: DataType
    confidence: float = Field(ge=0.0, le=100.0)
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1, max_length=2000)


class AIClaim(BaseModel):
    """Draft + system-assigned provenance. Only ever constructed by
    AIAnalyst, from a validated AIClaimDraft plus the real
    AIGenerationResult that produced it -- never directly from raw model
    output, and never with `id`/`created_at`/`model`/`provider` supplied by
    anything other than the caller itself."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    claim: str
    data_type: DataType
    confidence: float
    evidence_ids: list[str]
    reasoning_summary: str
    created_at: datetime
    model: str
    provider: str

    @classmethod
    def from_draft(cls, draft: AIClaimDraft, *, created_at: datetime, model: str, provider: str) -> AIClaim:
        return cls(
            claim=draft.claim,
            data_type=draft.data_type,
            confidence=draft.confidence,
            evidence_ids=draft.evidence_ids,
            reasoning_summary=draft.reasoning_summary,
            created_at=created_at,
            model=model,
            provider=provider,
        )


class AIRecommendationDraft(BaseModel):
    """Fase 5 (28/09) — lo shape di structured-output per AIAdvisor
    (`app/ai/advisor.py`), il ruolo AI separato che produce un consiglio
    operativo, non un'interpretazione di evidence come AIClaimDraft. Solo
    ciò che il modello decide davvero: il testo del consiglio, quali
    evidence lo sostengono, un breve motivo. Deliberatamente SENZA
    `data_type`/`confidence` (quelli sono per una CLASSIFICAZIONE di
    verità, qui non stiamo classificando un fatto) e SENZA `priority`/
    `opportunity_id` (quelli sono decisi deterministicamente
    dall'Opportunity reale, mai dal modello — vedi
    app/models/ai_recommendation.py)."""

    model_config = ConfigDict(frozen=True)

    recommendation: str = Field(min_length=1, max_length=1000)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)


class AIRecommendation(BaseModel):
    """Draft + provenance stampata dal chiamante (AIAdvisor) + linkage
    deterministica alla Opportunity reale che ha originato la richiesta
    (mai autoriportata dal modello — stesso principio di AIClaim.from_draft,
    esteso qui a due campi in più: opportunity_id/opportunity_type/priority
    vengono TUTTI dalla riga Opportunity vera, non dall'output del
    modello)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    opportunity_id: uuid.UUID
    opportunity_type: str
    priority: str  # OpportunityLevel.value della Opportunity reale — HIGH/MEDIUM, mai NONE/LOW (vedi advisor engine)
    recommendation: str
    reasoning_summary: str
    evidence_ids: list[str]
    created_at: datetime
    model: str
    provider: str

    @classmethod
    def from_draft(
        cls,
        draft: AIRecommendationDraft,
        *,
        opportunity_id: uuid.UUID,
        opportunity_type: str,
        priority: str,
        created_at: datetime,
        model: str,
        provider: str,
    ) -> AIRecommendation:
        return cls(
            opportunity_id=opportunity_id,
            opportunity_type=opportunity_type,
            priority=priority,
            recommendation=draft.recommendation,
            reasoning_summary=draft.reasoning_summary,
            evidence_ids=draft.evidence_ids,
            created_at=created_at,
            model=model,
            provider=provider,
        )
