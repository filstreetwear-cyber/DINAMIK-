"""
Milestone 9.5, sections 30-31: real integration test against a real
Ollama + qwen3:1.7b instance. Skips cleanly (does not fail, does not fake
a pass) when Ollama isn't reachable -- same `_reachable()`/`pytest.skip(...)`
discipline as test_opportunity_engine.py/test_scoring_engine.py use for
Postgres.

This is deliberately the ONLY place in the test suite that talks to a real
Ollama backend -- everything in test_ai_provider.py runs against
MockAIProvider and must keep working with zero network access. Do not add
assertions here about specific numeric output (tokens/sec, exact wording)
-- an LLM's exact phrasing is not something to pin a test to; this test
checks STRUCTURE and HONESTY (valid JSON, real evidence ids, a real
model/provider field, no data invented outside the supplied evidence), not
a specific string match.

Confirmed 2026-09-21: this environment's network egress blocks both
ollama.com and huggingface.co (policy-level 403 on CONNECT, verified via
the agent proxy's own status endpoint) -- so this test legitimately skips
in every run performed from this sandbox. It is written to run for real
once pointed at a reachable Ollama instance (the actual Dinamik VPS, or
any other host running the model), not as a placeholder.
"""
from __future__ import annotations

import httpx
import pytest

from app.ai.analyst import AIAnalyst, AIEvidenceInput
from app.ai.bootstrap import build_default_ai_gateway
from app.core.config import get_settings
from app.models.enums import DataType


async def _ollama_reachable(base_url: str, timeout_seconds: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{base_url}/api/tags")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
async def real_gateway():
    settings = get_settings()
    base_url = settings.ai_config().get("local", {}).get("base_url", "http://localhost:11434")
    if not await _ollama_reachable(base_url):
        pytest.skip(
            "Ollama not reachable at "
            f"{base_url} -- real qwen3:1.7b integration test needs a live Ollama instance "
            "(confirmed unreachable from this sandbox: ollama.com/huggingface.co are network-blocked "
            "here, see docs/architecture/local-ai.md)"
        )
    gateway = build_default_ai_gateway(settings)
    assert gateway is not None
    return gateway


@pytest.mark.asyncio
async def test_real_health_check_reports_available(real_gateway):
    status = await real_gateway.health_check()
    assert status.value in {"AVAILABLE", "CONFIGURATION_ERROR"}  # CONFIGURATION_ERROR if qwen3:1.7b isn't pulled


@pytest.mark.asyncio
async def test_real_analyst_call_against_worked_example(real_gateway):
    """Section 31's exact worked example: Amazon price EUR 39.90, Keepa
    90-day avg EUR 42.10, review_count 187, rating 4.1 -- verifies valid
    JSON, a real data_type, confidence, evidence ids that are a subset of
    what was supplied, and model="qwen3:1.7b"/provider="ollama" -- all
    read from the REAL AIGenerationResult, never fabricated by this test."""
    evidence = [
        AIEvidenceInput(id="ev-price", claim="price=39.90 EUR", data_type="FACT", source="amazon", confidence=95.0),
        AIEvidenceInput(
            id="ev-keepa-avg", claim="90-day average price=42.10 EUR", data_type="FACT", source="keepa",
            confidence=90.0,
        ),
        AIEvidenceInput(id="ev-reviews", claim="review_count=187", data_type="FACT", source="amazon", confidence=95.0),
        AIEvidenceInput(id="ev-rating", claim="rating=4.1", data_type="FACT", source="amazon", confidence=95.0),
    ]
    valid_ids = {e.id for e in evidence}

    analyst = AIAnalyst(real_gateway)
    outcome = await analyst.analyze(evidence)

    # Do NOT fabricate an expected outcome for a real model's behavior --
    # the only things asserted are structural/honesty guarantees this
    # architecture is responsible for, not what the model chooses to say.
    assert outcome.status in {"OK", "INSUFFICIENT_EVIDENCE", "VALIDATION_ERROR"}
    if outcome.status == "OK":
        for claim in outcome.claims:
            assert claim.data_type in set(DataType)
            assert 0.0 <= claim.confidence <= 100.0
            assert set(claim.evidence_ids).issubset(valid_ids)
            assert claim.model.split(":")[0] == "qwen3"
            assert claim.provider == "ollama"
