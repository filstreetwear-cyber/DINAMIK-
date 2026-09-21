"""
MockAIProvider — section 29/30's explicit allowance: unit tests for
AIGateway/AIAnalyst must not require a real Ollama instance. Mirrors
`app/tools/browser/mock_engine.py`'s role for the browser layer — a real
class implementing the real interface, backed by canned/deterministic
output instead of a network call, so the rest of the stack (gateway,
router, analyst, validation) is exercised for real.

Deliberately NOT a generic "always succeeds" stub: it can be configured to
raise any `AIProviderError` subclass, or to return a fixed response, so
tests can exercise every documented failure mode (section 27:
AI_UNAVAILABLE / MODEL_UNAVAILABLE / AI_TIMEOUT) without needing Ollama
down/misconfigured/slow to prove the gateway handles it.

`structured_generate()`'s default canned response is a syntactically valid
`AIClaimDraft` JSON body with an EMPTY `evidence_ids` list — not a fixed
evidence id, because this module has no idea what a given test's real
Evidence rows are, and a hardcoded id would either coincidentally match (
false-positive test) or never match (spurious VALIDATION_ERROR). A test
that needs the mock to cite specific evidence_ids passes its own
`structured_response` instead.
"""
from __future__ import annotations

import json

from app.ai.provider import (
    AIGenerationRequest,
    AIGenerationResult,
    AIProvider,
    AIProviderError,
)
from app.models.enums import ToolHealthStatus

_DEFAULT_STRUCTURED_RESPONSE = json.dumps(
    {
        "claim": "mock claim — no real inference performed",
        "data_type": "HYPOTHESIS",
        "confidence": 50.0,
        "evidence_ids": [],
        "reasoning_summary": "MockAIProvider default canned response — not a real AI inference.",
    }
)


class MockAIProvider(AIProvider):
    name = "mock"

    def __init__(
        self,
        *,
        health: ToolHealthStatus = ToolHealthStatus.AVAILABLE,
        text_response: str = "mock generation — no real inference performed.",
        structured_response: str | None = None,
        raise_error: AIProviderError | None = None,
        model_name: str = "mock-model",
    ) -> None:
        self._health = health
        self._text_response = text_response
        self._structured_response = structured_response or _DEFAULT_STRUCTURED_RESPONSE
        self._raise_error = raise_error
        self._model_name = model_name

    async def health_check(self) -> ToolHealthStatus:
        return self._health

    async def generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        if self._raise_error is not None:
            raise self._raise_error
        return AIGenerationResult(
            text=self._text_response,
            model=self._model_name,
            provider=self.name,
            duration_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
        )

    async def structured_generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        if self._raise_error is not None:
            raise self._raise_error
        return AIGenerationResult(
            text=self._structured_response,
            model=self._model_name,
            provider=self.name,
            duration_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
        )
