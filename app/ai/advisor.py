"""
AIAdvisor — Fase 5 (roadmap Fil/Frank, 28/09): il secondo (e ultimo) ruolo
AI di questo progetto, distinto da AIAnalyst (app/ai/analyst.py) per
design, non per accidente.

AIAnalyst interpreta evidence e produce AIClaim ("cosa è vero") — il suo
system prompt vieta ESPLICITAMENTE di produrre raccomandazioni ("a human
advisor does that"). Quel divieto resta intatto: non è stato rimosso o
aggirato per costruire questo modulo. AIAdvisor esiste apposta per la
domanda diversa che Fil ha posto fin dall'inizio di questa fase ("mi
suggerisce cosa e come cambiarlo") — con le SUE proprie regole, non
un'estensione delle regole di AIAnalyst:

1. AIAdvisor non diagnostica MAI un problema da solo. Riceve
   un'Opportunity GIA' DECISA deterministicamente (rule_triggered +
   reasons, da opportunity_engine.py) come fatto dato nel prompt — il suo
   unico compito è scrivere COME risolvere quel problema specifico, non
   decidere se/cosa sia un problema. Questo è il confine strutturale che
   rende sicuro dargli il permesso di "consigliare" che AIAnalyst non ha:
   non sta diagnosticando nulla di nuovo, sta operazionalizzando una
   diagnosi che un motore deterministico e testato ha già fatto.
2. Stessa disciplina anti-allucinazione di AIAnalyst per le evidence:
   `_validate_evidence_ids` qui sotto è una copia diretta della logica di
   analyst.py (stessa ragione: mai un evidence_id citato che non esiste
   davvero nell'input).
3. Il system prompt vieta esplicitamente ad AIAdvisor di scrivere un
   messaggio di contatto/pitch per il venditore (quello è Fase 6, un
   modulo separato, revisionato da un umano prima dell'invio) e di dare
   un giudizio commerciale finale (contattare o no, quanto vale il
   prospect). Il "final say" resta umano, esattamente come per AIAnalyst
   — solo spostato un passo più a valle (dal "cosa è vero" al "come
   risolverlo tecnicamente", non fino a "contattalo e digli X").

Stesso shape architetturale di AIAnalyst (DB-free input, AIAnalystOutcome-
style status, retry-on-parse-failure) per lo stesso motivo: testabile
senza sessione, comportamento prevedibile per chi già conosce analyst.py.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError

from app.ai.analyst import AIEvidenceInput
from app.ai.contracts import AIRecommendation, AIRecommendationDraft
from app.ai.gateway import AIGateway
from app.ai.provider import (
    AIGenerationRequest,
    AIModelUnavailableError,
    AIProviderError,
    AITimeoutError,
    AIUnavailableError,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the DINAMIK AIAdvisor. Dinamik Advisor is an Amazon \
seller account-management agency; a human team member decides whether and how \
to contact a prospect, never you.

You are given ONE finding a deterministic rule engine already decided —
its rule_triggered and reasons are FACTS given to you, not something you \
diagnose. Your only job: write ONE short, concrete, technical recommendation \
of WHAT to fix and HOW, for that specific finding.

Rules you must never break:
1. The rule_triggered and reasons given to you are already decided. Do not \
question them, do not diagnose a different or additional problem, do not \
invent a finding that isn't in the reasons given.
2. Use ONLY the evidence supplied to you to support how to fix it. Never \
invent facts, tools, prices, deadlines, or evidence ids not given to you.
3. Every evidence_id you cite MUST be one of the evidence ids supplied. \
Never reference an id you were not given. If no evidence is relevant or \
supplied, cite none — do not invent one to look grounded.
4. Be concrete and specific (e.g. "add a responsive viewport meta tag \
declaring width=device-width", not "improve the website"). Generic advice \
is not acceptable.
5. Never draft an outreach message, email, or pitch to the seller — that \
is a separate step a human handles later. You are producing an internal \
technical note for Dinamik's own team, not customer-facing copy.
6. Never give a final commercial judgement (whether to contact this \
seller, what to charge, whether they're worth pursuing) — only the \
technical what/how for the specific finding given to you.
7. Be concise: 1-3 sentences for the recommendation.

Respond only with JSON matching the requested schema."""

_RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": AIRecommendationDraft.model_json_schema(),
        }
    },
    "required": ["recommendations"],
}

_MAX_GENERATE_ATTEMPTS = 2  # stessa tolleranza "un retry su parse fallito" di AIAnalyst


@dataclass(frozen=True)
class OpportunityInput:
    """Vista DB-free di UNA riga Opportunity reale — stesso principio di
    AIEvidenceInput (riusato qui as-is, nessuna duplicazione: le evidence
    passate all'advisor sono le stesse Evidence, stesso shape, di
    AIAnalyst). Solo i campi che servono al prompt; mai passata l'intera
    riga ORM (questo modulo non ha/deve avere una sessione)."""

    id: uuid.UUID
    opportunity_type: str
    level: str  # OpportunityLevel — sempre HIGH o MEDIUM quando arriva qui, vedi ai_recommendation_engine.py
    rule_triggered: str
    reasons: list[str]


@dataclass
class AIAdvisorOutcome:
    status: str  # stessi valori di AIAnalystOutcome.status
    recommendations: list[AIRecommendation] = field(default_factory=list)
    error_message: str | None = None


def _build_prompt(opportunity: OpportunityInput, evidence_items: list[AIEvidenceInput]) -> str:
    header = (
        f"Finding already decided by a deterministic rule (opportunity_type={opportunity.opportunity_type}, "
        f"rule_triggered={opportunity.rule_triggered}, level={opportunity.level}):"
    )
    lines = [header]
    for reason in opportunity.reasons:
        lines.append(f"- {reason}")

    lines.append("\nEvidence available (cite only these ids, or none):")
    if evidence_items:
        for item in evidence_items:
            lines.append(
                f"- id={item.id} | source={item.source} | data_type={item.data_type} | "
                f"confidence={item.confidence} | claim: {item.claim}"
            )
    else:
        lines.append("(none supplied)")

    lines.append(
        "\nWrite ONE concrete technical recommendation for the finding above, as JSON: "
        '{"recommendations": [{"recommendation": ..., "reasoning_summary": ..., '
        '"evidence_ids": [...]}]}. Zero recommendations is acceptable if the finding genuinely '
        "gives you nothing concrete to act on."
    )
    return "\n".join(lines)


def _status_for_error(exc: AIProviderError) -> str:
    if isinstance(exc, AITimeoutError):
        return "AI_TIMEOUT"
    if isinstance(exc, AIModelUnavailableError):
        return "MODEL_UNAVAILABLE"
    if isinstance(exc, AIUnavailableError):
        return "AI_UNAVAILABLE"
    return "AI_UNAVAILABLE"


def _validate_evidence_ids(
    drafts: list[AIRecommendationDraft], valid_ids: set[str]
) -> list[AIRecommendationDraft]:
    """Copia diretta della logica di analyst.py._validate_evidence_ids —
    stessa garanzia, stesso identico motivo (vedi module docstring qui e
    lì): mai un evidence_id citato che il chiamante non ha davvero
    fornito."""
    kept: list[AIRecommendationDraft] = []
    for draft in drafts:
        invented = [eid for eid in draft.evidence_ids if eid not in valid_ids]
        if invented:
            logger.warning("ai_recommendation_dropped_invented_evidence_ids invented=%s", invented)
            continue
        kept.append(draft)
    return kept


class AIAdvisor:
    def __init__(self, gateway: AIGateway) -> None:
        self._gateway = gateway

    async def recommend(
        self, opportunity: OpportunityInput, evidence_items: list[AIEvidenceInput]
    ) -> AIAdvisorOutcome:
        prompt = _build_prompt(opportunity, evidence_items)
        valid_ids = {item.id for item in evidence_items}

        last_error: Exception | None = None
        for attempt in range(1, _MAX_GENERATE_ATTEMPTS + 1):
            try:
                result = await self._gateway.structured_generate(
                    AIGenerationRequest(prompt=prompt, system_prompt=_SYSTEM_PROMPT, schema=_RECOMMENDATION_SCHEMA)
                )
            except AIProviderError as exc:
                return AIAdvisorOutcome(status=_status_for_error(exc), error_message=str(exc))

            try:
                payload = json.loads(result.text)
                drafts = [AIRecommendationDraft.model_validate(item) for item in payload.get("recommendations", [])]
            except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
                last_error = exc
                logger.warning("ai_recommendation_structured_output_invalid attempt=%d error=%s", attempt, exc)
                continue

            valid_drafts = _validate_evidence_ids(drafts, valid_ids)
            if not valid_drafts and drafts:
                last_error = ValueError("all recommendations cited only invented evidence_ids")
                continue

            created_at = datetime.now(UTC)
            recommendations = [
                AIRecommendation.from_draft(
                    draft,
                    opportunity_id=opportunity.id,
                    opportunity_type=opportunity.opportunity_type,
                    priority=opportunity.level,
                    created_at=created_at,
                    model=result.model,
                    provider=result.provider,
                )
                for draft in valid_drafts
            ]
            return AIAdvisorOutcome(status="OK", recommendations=recommendations)

        return AIAdvisorOutcome(status="VALIDATION_ERROR", error_message=str(last_error))
