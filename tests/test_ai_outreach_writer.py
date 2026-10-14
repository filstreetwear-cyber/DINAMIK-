"""
Fase 6 (28/09): AIOutreachWriter (app/ai/outreach_writer.py) — mirror di
come test_ai_advisor.py testa AIAdvisor (MockAIProvider, nessun Ollama
reale richiesto).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.ai.outreach_writer import AIOutreachWriter, RecommendationInput
from app.ai.provider import (
    AIGenerationRequest,
    AIGenerationResult,
    AIModelUnavailableError,
    AITimeoutError,
    AIUnavailableError,
)


def _recommendation(**overrides: Any) -> RecommendationInput:
    defaults: dict[str, Any] = {
        "recommendation": "Add a responsive viewport meta tag declaring width=device-width.",
        "opportunity_type": "WEBSITE_OPPORTUNITY",
        "priority": "MEDIUM",
    }
    defaults.update(overrides)
    return RecommendationInput(**defaults)


def _draft_response(subject: str = "Una nota sul vostro sito", body: str = "Corpo della bozza.") -> str:
    return json.dumps({"subject": subject, "body": body})


@pytest.mark.asyncio
async def test_writer_returns_insufficient_recommendations_for_empty_input():
    writer = AIOutreachWriter(AIGateway(MockAIProvider()))

    outcome = await writer.draft("Acme Srl", [])

    assert outcome.status == "INSUFFICIENT_RECOMMENDATIONS"
    assert outcome.subject is None
    assert outcome.body is None


@pytest.mark.asyncio
async def test_writer_returns_ok_with_stamped_provenance_for_valid_output():
    provider = MockAIProvider(structured_response=_draft_response())
    writer = AIOutreachWriter(AIGateway(provider))

    outcome = await writer.draft("Acme Srl", [_recommendation()])

    assert outcome.status == "OK"
    assert outcome.subject == "Una nota sul vostro sito"
    assert outcome.body == "Corpo della bozza."
    assert outcome.model == "mock-model"
    assert outcome.provider == "mock"
    assert outcome.generated_at is not None


@pytest.mark.asyncio
async def test_writer_returns_validation_error_for_malformed_json():
    provider = MockAIProvider(structured_response="not valid json at all")
    writer = AIOutreachWriter(AIGateway(provider))

    outcome = await writer.draft("Acme Srl", [_recommendation()])

    assert outcome.status == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_writer_returns_validation_error_for_missing_required_field():
    provider = MockAIProvider(structured_response=json.dumps({"subject": "Solo oggetto"}))
    writer = AIOutreachWriter(AIGateway(provider))

    outcome = await writer.draft("Acme Srl", [_recommendation()])

    assert outcome.status == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_writer_maps_ai_unavailable_error_to_status():
    provider = MockAIProvider(raise_error=AIUnavailableError("mock", "not reachable"))
    writer = AIOutreachWriter(AIGateway(provider))

    outcome = await writer.draft("Acme Srl", [_recommendation()])

    assert outcome.status == "AI_UNAVAILABLE"


@pytest.mark.asyncio
async def test_writer_maps_model_unavailable_error_to_status():
    provider = MockAIProvider(raise_error=AIModelUnavailableError("mock", "wrong model"))
    writer = AIOutreachWriter(AIGateway(provider))

    outcome = await writer.draft("Acme Srl", [_recommendation()])

    assert outcome.status == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_writer_maps_timeout_error_to_status():
    provider = MockAIProvider(raise_error=AITimeoutError("mock", "too slow"))
    writer = AIOutreachWriter(AIGateway(provider))

    outcome = await writer.draft("Acme Srl", [_recommendation()])

    assert outcome.status == "AI_TIMEOUT"


@pytest.mark.asyncio
async def test_writer_prompt_includes_brand_name_and_recommendation_text():
    captured: dict[str, str] = {}

    class _CapturingProvider(MockAIProvider):
        async def structured_generate(self, request: AIGenerationRequest) -> AIGenerationResult:
            captured["prompt"] = request.prompt
            return await super().structured_generate(request)

    provider = _CapturingProvider(structured_response=_draft_response())
    writer = AIOutreachWriter(AIGateway(provider))

    await writer.draft("Acme Srl", [_recommendation(recommendation="Fix the missing meta description.")])

    assert "Acme Srl" in captured["prompt"]
    assert "Fix the missing meta description." in captured["prompt"]
