"""
Fase 5 (28/09): AIAdvisor (app/ai/advisor.py) — mirror di come
test_ai_provider.py testa AIAnalyst (MockAIProvider, nessun Ollama reale
richiesto). Vedi advisor.py per perché è un ruolo AI separato da
AIAnalyst, non un'estensione delle sue regole.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.ai.advisor import AIAdvisor, OpportunityInput
from app.ai.analyst import AIEvidenceInput
from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.ai.provider import (
    AIGenerationRequest,
    AIGenerationResult,
    AIModelUnavailableError,
    AITimeoutError,
    AIUnavailableError,
)


def _opportunity(**overrides: Any) -> OpportunityInput:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "opportunity_type": "WEBSITE_OPPORTUNITY",
        "level": "MEDIUM",
        "rule_triggered": "mobile_and_performance_signals_v1",
        "reasons": ["mobile_friendly=False -- no responsive viewport detected"],
    }
    defaults.update(overrides)
    return OpportunityInput(**defaults)


def _draft_response(*, evidence_ids: list[str]) -> str:
    return json.dumps(
        {
            "recommendations": [
                {
                    "recommendation": "Add a responsive viewport meta tag declaring width=device-width.",
                    "reasoning_summary": "The finding states no responsive viewport was detected.",
                    "evidence_ids": evidence_ids,
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_advisor_returns_ok_with_real_recommendation_for_valid_structured_output():
    evidence = [AIEvidenceInput(id="ev-1", claim="no viewport meta tag", data_type="FACT", source="website", confidence=70.0)]
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=["ev-1"]))
    advisor = AIAdvisor(AIGateway(provider))
    opportunity = _opportunity()

    outcome = await advisor.recommend(opportunity, evidence)

    assert outcome.status == "OK"
    assert len(outcome.recommendations) == 1
    rec = outcome.recommendations[0]
    assert rec.evidence_ids == ["ev-1"]
    assert rec.opportunity_id == opportunity.id
    assert rec.opportunity_type == "WEBSITE_OPPORTUNITY"
    assert rec.priority == "MEDIUM"  # stampato dall'Opportunity reale, mai dal modello
    assert rec.model == "mock-model"
    assert rec.provider == "mock"


@pytest.mark.asyncio
async def test_advisor_accepts_empty_evidence_list():
    """A differenza di AIAnalyst.analyze([]), che ritorna subito
    INSUFFICIENT_EVIDENCE, AIAdvisor.recommend può ricevere zero evidence
    (caso reale oggi per WEBSITE_OPPORTUNITY, vedi ai_recommendation_engine.py)
    -- il rule_triggered/reasons da soli sono un input valido."""
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=[]))
    advisor = AIAdvisor(AIGateway(provider))

    outcome = await advisor.recommend(_opportunity(), [])

    assert outcome.status == "OK"
    assert len(outcome.recommendations) == 1
    assert outcome.recommendations[0].evidence_ids == []


@pytest.mark.asyncio
async def test_advisor_drops_recommendations_citing_invented_evidence_ids():
    evidence = [AIEvidenceInput(id="ev-1", claim="x", data_type="FACT", source="website", confidence=70.0)]
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=["ev-999-does-not-exist"]))
    advisor = AIAdvisor(AIGateway(provider))

    outcome = await advisor.recommend(_opportunity(), evidence)

    assert outcome.status == "VALIDATION_ERROR"
    assert outcome.recommendations == []


@pytest.mark.asyncio
async def test_advisor_returns_validation_error_for_malformed_json():
    provider = MockAIProvider(structured_response="not valid json at all")
    advisor = AIAdvisor(AIGateway(provider))

    outcome = await advisor.recommend(_opportunity(), [])

    assert outcome.status == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_advisor_maps_ai_unavailable_error_to_status():
    provider = MockAIProvider(raise_error=AIUnavailableError("mock", "not reachable"))
    advisor = AIAdvisor(AIGateway(provider))

    outcome = await advisor.recommend(_opportunity(), [])

    assert outcome.status == "AI_UNAVAILABLE"


@pytest.mark.asyncio
async def test_advisor_maps_model_unavailable_error_to_status():
    provider = MockAIProvider(raise_error=AIModelUnavailableError("mock", "wrong model"))
    advisor = AIAdvisor(AIGateway(provider))

    outcome = await advisor.recommend(_opportunity(), [])

    assert outcome.status == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_advisor_maps_timeout_error_to_status():
    provider = MockAIProvider(raise_error=AITimeoutError("mock", "too slow"))
    advisor = AIAdvisor(AIGateway(provider))

    outcome = await advisor.recommend(_opportunity(), [])

    assert outcome.status == "AI_TIMEOUT"


@pytest.mark.asyncio
async def test_advisor_prompt_states_the_finding_as_already_decided():
    """Non testa il testo esatto del prompt (fragile), ma che il
    rule_triggered/reasons dell'Opportunity finiscano davvero nel prompt
    inviato al gateway -- prova che l'advisor non li ignora."""
    captured: dict[str, str] = {}

    class _CapturingProvider(MockAIProvider):
        async def structured_generate(self, request: AIGenerationRequest) -> AIGenerationResult:
            captured["prompt"] = request.prompt
            return await super().structured_generate(request)

    provider = _CapturingProvider(structured_response=_draft_response(evidence_ids=[]))
    advisor = AIAdvisor(AIGateway(provider))
    opportunity = _opportunity(reasons=["page_speed_score=20 is below threshold 50"])

    await advisor.recommend(opportunity, [])

    assert "page_speed_score=20 is below threshold 50" in captured["prompt"]
    assert "mobile_and_performance_signals_v1" in captured["prompt"]
