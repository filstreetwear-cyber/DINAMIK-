"""
Milestone 9.5: AIProvider / AIGateway / AIRouter / AIClaim / AIAnalyst.

All tests here run against `MockAIProvider` -- no real Ollama required
(section 29/30's explicit allowance). A separate real-integration test
lives in test_ai_ollama_integration.py, skipped unless a real Ollama
instance is reachable -- this file must never depend on that, and never
fabricates a measurement to fake a pass.
"""
from __future__ import annotations

import json

import pytest

from app.ai.analyst import AIAnalyst, AIEvidenceInput
from app.ai.bootstrap import build_default_ai_gateway
from app.ai.contracts import AIClaim, AIClaimDraft
from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.ai.provider import (
    AIGenerationRequest,
    AIModelUnavailableError,
    AITimeoutError,
    AIUnavailableError,
)
from app.ai.router import AIRouter
from app.core.config import get_settings
from app.models.enums import DataType, ToolHealthStatus


def _mock_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    get_settings.cache_clear()
    return get_settings()


# --- AIClaimDraft / AIClaim contracts --------------------------------------


def test_claim_draft_rejects_unknown_data_type():
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError, any invalid-enum failure is fine
        AIClaimDraft(
            claim="x",
            data_type="NOT_A_REAL_TYPE",
            confidence=50.0,
            evidence_ids=[],
            reasoning_summary="y",
        )


def test_claim_draft_rejects_out_of_range_confidence():
    with pytest.raises(Exception):  # noqa: B017
        AIClaimDraft(
            claim="x",
            data_type=DataType.FACT,
            confidence=150.0,
            evidence_ids=[],
            reasoning_summary="y",
        )


def test_ai_claim_from_draft_stamps_provenance_not_supplied_by_model():
    from datetime import UTC, datetime

    draft = AIClaimDraft(
        claim="Rating is high", data_type=DataType.FACT, confidence=90.0, evidence_ids=["e1"],
        reasoning_summary="stated directly in evidence"
    )
    created_at = datetime.now(UTC)

    claim = AIClaim.from_draft(draft, created_at=created_at, model="qwen3:1.7b", provider="ollama")

    assert claim.model == "qwen3:1.7b"
    assert claim.provider == "ollama"
    assert claim.created_at == created_at
    assert claim.id is not None


# --- AIGateway ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_health_check_delegates_to_provider():
    gateway = AIGateway(MockAIProvider(health=ToolHealthStatus.AVAILABLE))
    assert await gateway.health_check() == ToolHealthStatus.AVAILABLE


@pytest.mark.asyncio
async def test_gateway_generate_returns_provider_result():
    gateway = AIGateway(MockAIProvider(text_response="hello"))
    result = await gateway.generate(AIGenerationRequest(prompt="hi"))
    assert result.text == "hello"
    assert result.provider == "mock"


@pytest.mark.asyncio
async def test_gateway_propagates_provider_error_unwrapped():
    gateway = AIGateway(MockAIProvider(raise_error=AIUnavailableError("mock", "down")))
    with pytest.raises(AIUnavailableError):
        await gateway.generate(AIGenerationRequest(prompt="hi"))


# --- AIRouter / bootstrap -----------------------------------------------------


def test_router_resolves_local_ollama_provider_from_config():
    router = AIRouter(
        {
            "provider": "local",
            "local": {"backend": "ollama", "base_url": "http://localhost:11434", "model": "qwen3:1.7b"},
        }
    )
    provider = router.resolve_provider()
    assert provider.name == "ollama"


def test_router_raises_controlled_error_for_cloud_provider():
    from app.ai.provider import AICloudNotImplementedError

    router = AIRouter({"provider": "cloud", "cloud": {"enabled": False}})
    with pytest.raises(AICloudNotImplementedError):
        router.resolve_provider()


def test_router_raises_for_unknown_backend():
    router = AIRouter({"provider": "local", "local": {"backend": "not-a-real-backend"}})
    with pytest.raises(ValueError, match="backend"):
        router.resolve_provider()


def test_build_default_ai_gateway_returns_gateway_when_enabled(monkeypatch: pytest.MonkeyPatch):
    settings = _mock_settings(monkeypatch)
    gateway = build_default_ai_gateway(settings)
    assert gateway is not None


def test_build_default_ai_gateway_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch):
    settings = _mock_settings(monkeypatch)
    settings.yaml_config["ai"] = {**settings.yaml_config.get("ai", {}), "enabled": False}
    gateway = build_default_ai_gateway(settings)
    assert gateway is None


def test_ai_config_defaults_to_qwen3_1_7b(monkeypatch: pytest.MonkeyPatch):
    """Section 5's exact-model requirement -- the config default must name
    qwen3:1.7b, never a larger variant."""
    settings = _mock_settings(monkeypatch)
    assert settings.ai_config()["local"]["model"] == "qwen3:1.7b"


def test_build_default_ai_gateway_honors_ollama_base_url_override(monkeypatch: pytest.MonkeyPatch):
    """2026-09-22 (post Reality Audit §12/§18, Docker->Ollama networking
    fix): OLLAMA_BASE_URL, when set, must override config/default.yaml's
    ai.local.base_url (http://localhost:11434, unusable from inside a
    Docker container) without mutating the shared, @lru_cache'd
    settings.yaml_config for anyone else calling settings.ai_config()."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    settings = _mock_settings(monkeypatch)

    gateway = build_default_ai_gateway(settings)
    assert gateway is not None
    assert gateway._provider._base_url == "http://host.docker.internal:11434"

    # The override must not have leaked into the shared yaml_config dict.
    assert settings.yaml_config["ai"]["local"]["base_url"] == "http://localhost:11434"


def test_build_default_ai_gateway_uses_config_default_when_ollama_base_url_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    settings = _mock_settings(monkeypatch)

    gateway = build_default_ai_gateway(settings)
    assert gateway is not None
    assert gateway._provider._base_url == "http://localhost:11434"


# --- AIAnalyst ----------------------------------------------------------------


def _draft_response(*, evidence_ids: list[str], data_type: str = "FACT") -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "claim": "The product has a 4.5 rating with 300 reviews.",
                    "data_type": data_type,
                    "confidence": 80.0,
                    "evidence_ids": evidence_ids,
                    "reasoning_summary": "Directly stated in supplied evidence.",
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_analyst_returns_insufficient_evidence_for_empty_input():
    analyst = AIAnalyst(AIGateway(MockAIProvider()))

    outcome = await analyst.analyze([])

    assert outcome.status == "INSUFFICIENT_EVIDENCE"
    assert outcome.claims == []


@pytest.mark.asyncio
async def test_analyst_returns_ok_with_real_claim_for_valid_structured_output():
    evidence = [AIEvidenceInput(id="ev-1", claim="rating=4.5", data_type="FACT", source="amazon", confidence=95.0)]
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=["ev-1"]))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "OK"
    assert len(outcome.claims) == 1
    claim = outcome.claims[0]
    assert claim.evidence_ids == ["ev-1"]
    assert claim.data_type == DataType.FACT
    assert claim.model == "mock-model"
    assert claim.provider == "mock"


@pytest.mark.asyncio
async def test_analyst_drops_claims_citing_invented_evidence_ids():
    evidence = [AIEvidenceInput(id="ev-1", claim="rating=4.5", data_type="FACT", source="amazon", confidence=95.0)]
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=["ev-999-does-not-exist"]))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "VALIDATION_ERROR"
    assert outcome.claims == []


@pytest.mark.asyncio
async def test_analyst_keeps_claims_with_no_cited_evidence_ids():
    """A claim summarizing the evidence set as a whole, citing no specific
    id, is legitimate -- not the same failure mode as citing a fake id."""
    evidence = [AIEvidenceInput(id="ev-1", claim="rating=4.5", data_type="FACT", source="amazon", confidence=95.0)]
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=[]))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "OK"
    assert len(outcome.claims) == 1


@pytest.mark.asyncio
async def test_analyst_returns_validation_error_for_malformed_json():
    evidence = [AIEvidenceInput(id="ev-1", claim="x", data_type="FACT", source="amazon", confidence=90.0)]
    provider = MockAIProvider(structured_response="not valid json at all")
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_analyst_maps_ai_unavailable_error_to_status():
    evidence = [AIEvidenceInput(id="ev-1", claim="x", data_type="FACT", source="amazon", confidence=90.0)]
    provider = MockAIProvider(raise_error=AIUnavailableError("mock", "not reachable"))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "AI_UNAVAILABLE"


@pytest.mark.asyncio
async def test_analyst_maps_model_unavailable_error_to_status():
    evidence = [AIEvidenceInput(id="ev-1", claim="x", data_type="FACT", source="amazon", confidence=90.0)]
    provider = MockAIProvider(raise_error=AIModelUnavailableError("mock", "wrong model"))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_analyst_maps_timeout_error_to_status():
    evidence = [AIEvidenceInput(id="ev-1", claim="x", data_type="FACT", source="amazon", confidence=90.0)]
    provider = MockAIProvider(raise_error=AITimeoutError("mock", "too slow"))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "AI_TIMEOUT"


@pytest.mark.asyncio
async def test_analyst_never_upgrades_inference_to_fact():
    """The model itself classifies data_type -- this test proves the
    pipeline passes an HYPOTHESIS/INFERENCE classification through
    unchanged rather than normalizing it upward."""
    evidence = [AIEvidenceInput(id="ev-1", claim="x", data_type="INFERENCE", source="amazon", confidence=60.0)]
    provider = MockAIProvider(structured_response=_draft_response(evidence_ids=["ev-1"], data_type="HYPOTHESIS"))
    analyst = AIAnalyst(AIGateway(provider))

    outcome = await analyst.analyze(evidence)

    assert outcome.status == "OK"
    assert outcome.claims[0].data_type == DataType.HYPOTHESIS
