"""
AIAnalyst — the one AI AGENT this milestone ships (section 12/23's
mandatory DATA-agent/AI-agent split: this module never fetches data
itself, it only interprets Evidence a data agent already collected and
persisted). Input: a list of `AIEvidenceInput` (a DB-free, id-bearing view
of real `Evidence` rows — the caller converts ORM rows before calling this,
so this module stays testable without a session, same reasoning as
app/schemas/contracts.py's own separation from the ORM layer). Output: an
`AIAnalystOutcome` carrying either real `AIClaim`s or one of section 27's
controlled failure statuses — never a bare exception for an expected
failure mode, and never a fabricated claim.

Why `analyze()` catches `AIProviderError` instead of letting it propagate:
section 27 lists AI_UNAVAILABLE/MODEL_UNAVAILABLE/AI_TIMEOUT alongside
VALIDATION_ERROR/INSUFFICIENT_EVIDENCE as outcomes of calling the AI
layer — a caller (a future agent/orchestrator) should be able to branch on
`outcome.status` uniformly for all five, not catch exceptions for three of
them and check a return value for the other two. The underlying
`AIProviderError` is still attached (`outcome.error_message`) for logging,
just not re-raised.

Guardrails against hallucination (section 21) live in the system prompt
below, not as a separate code layer, because an LLM cannot be constrained
by code alone — the two enforceable, code-level guarantees are: (1) every
`evidence_id` a claim references is checked against the real input set
post-hoc (`_validate_evidence_ids`), any claim citing an id that doesn't
exist is dropped, never silently kept; (2) `data_type`/`model`/`provider`/
`id`/`created_at` on the final `AIClaim` are never taken from the model's
own text — `data_type` is schema-validated against the real `DataType`
enum by pydantic, and the provenance fields are stamped by this module
from the real `AIGenerationResult`, exactly as `AIClaim.from_draft`
requires.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError

from app.ai.contracts import AIClaim, AIClaimDraft
from app.ai.gateway import AIGateway
from app.ai.provider import (
    AIGenerationRequest,
    AIModelUnavailableError,
    AIProviderError,
    AITimeoutError,
    AIUnavailableError,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the DINAMIK AIAnalyst. You interpret Amazon-seller \
intelligence evidence. You do not collect data and you do not produce final \
commercial recommendations — a human advisor does that.

Rules you must never break:
1. Use ONLY the evidence supplied to you in this request. Never invent \
values, sources, ASINs, prices, review counts, or evidence ids that were \
not given to you.
2. Every evidence_id you cite in a claim MUST be one of the evidence ids \
supplied to you. Never reference an id you were not given.
3. Never upgrade an INFERENCE or a HYPOTHESIS to a FACT. Classify each \
claim's data_type honestly: FACT only for something directly stated in \
the supplied evidence; ESTIMATE for a derived/approximate number; \
INFERENCE for a conclusion you drew from the evidence; HYPOTHESIS for a \
plausible but unconfirmed explanation.
4. If the supplied evidence is missing or insufficient to say anything \
responsible, say so explicitly rather than guessing.
5. Never claim to have browsed the web, called an API, or used any data \
source. You only reason over the evidence text given to you in this \
prompt.
6. Be concise. Do not produce a final buy/sell/invest recommendation — \
your job is to classify and summarize evidence, not to advise.

Respond only with JSON matching the requested schema."""

_CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": AIClaimDraft.model_json_schema(),
        }
    },
    "required": ["claims"],
}

_MAX_GENERATE_ATTEMPTS = 2  # section 30: "one retry on parse failure"


@dataclass(frozen=True)
class AIEvidenceInput:
    """DB-free view of one Evidence row — see module docstring for why
    AIAnalyst never touches the ORM/a session directly."""

    id: str
    claim: str
    data_type: str
    source: str
    confidence: float


@dataclass
class AIAnalystOutcome:
    status: str  # "OK" | "INSUFFICIENT_EVIDENCE" | "VALIDATION_ERROR" | "AI_UNAVAILABLE" | "MODEL_UNAVAILABLE" | "AI_TIMEOUT"
    claims: list[AIClaim] = field(default_factory=list)
    error_message: str | None = None


def _build_prompt(evidence_items: list[AIEvidenceInput]) -> str:
    lines = ["Evidence supplied for this analysis:"]
    for item in evidence_items:
        lines.append(
            f"- id={item.id} | source={item.source} | data_type={item.data_type} | "
            f"confidence={item.confidence} | claim: {item.claim}"
        )
    lines.append(
        "\nAnalyze this evidence. Produce zero or more claims as JSON: "
        '{"claims": [{"claim": ..., "data_type": ..., "confidence": ..., '
        '"evidence_ids": [...], "reasoning_summary": ...}]}. '
        "Only cite evidence ids from the list above."
    )
    return "\n".join(lines)


def _status_for_error(exc: AIProviderError) -> str:
    if isinstance(exc, AITimeoutError):
        return "AI_TIMEOUT"
    if isinstance(exc, AIModelUnavailableError):
        return "MODEL_UNAVAILABLE"
    if isinstance(exc, AIUnavailableError):
        return "AI_UNAVAILABLE"
    return "AI_UNAVAILABLE"  # any other AIProviderError subclass -- safe default, not a crash


def _validate_evidence_ids(drafts: list[AIClaimDraft], valid_ids: set[str]) -> list[AIClaimDraft]:
    """Drops any draft that cites an evidence_id not present in the real
    input set -- never silently accepted (module docstring, guarantee 1).
    A draft with an empty evidence_ids list is kept: a claim can
    legitimately summarize the evidence set as a whole without pinning to
    one id."""
    kept: list[AIClaimDraft] = []
    for draft in drafts:
        invented = [eid for eid in draft.evidence_ids if eid not in valid_ids]
        if invented:
            logger.warning("ai_claim_dropped_invented_evidence_ids invented=%s", invented)
            continue
        kept.append(draft)
    return kept


class AIAnalyst:
    def __init__(self, gateway: AIGateway) -> None:
        self._gateway = gateway

    async def analyze(self, evidence_items: list[AIEvidenceInput]) -> AIAnalystOutcome:
        if not evidence_items:
            return AIAnalystOutcome(status="INSUFFICIENT_EVIDENCE", error_message="no evidence supplied")

        prompt = _build_prompt(evidence_items)
        valid_ids = {item.id for item in evidence_items}

        last_error: Exception | None = None
        for attempt in range(1, _MAX_GENERATE_ATTEMPTS + 1):
            try:
                result = await self._gateway.structured_generate(
                    AIGenerationRequest(prompt=prompt, system_prompt=_SYSTEM_PROMPT, schema=_CLAIMS_SCHEMA)
                )
            except AIProviderError as exc:
                return AIAnalystOutcome(status=_status_for_error(exc), error_message=str(exc))

            try:
                payload = json.loads(result.text)
                drafts = [AIClaimDraft.model_validate(item) for item in payload.get("claims", [])]
            except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
                last_error = exc
                logger.warning("ai_structured_output_invalid attempt=%d error=%s", attempt, exc)
                continue

            valid_drafts = _validate_evidence_ids(drafts, valid_ids)
            if not valid_drafts and drafts:
                # every draft cited only invented ids -- treat like a
                # validation failure rather than silently returning an
                # empty, misleadingly-clean OK result.
                last_error = ValueError("all claims cited only invented evidence_ids")
                continue

            created_at = datetime.now(UTC)
            claims = [
                AIClaim.from_draft(draft, created_at=created_at, model=result.model, provider=result.provider)
                for draft in valid_drafts
            ]
            return AIAnalystOutcome(status="OK", claims=claims)

        return AIAnalystOutcome(status="VALIDATION_ERROR", error_message=str(last_error))
