"""
AIGateway — section 4's "the only thing runtime agents should call".
Wraps exactly one `AIProvider` (this milestone: local Ollama only, no
routing decision to make yet -- see bootstrap.py for why AIRouter is a
thin pass-through today, not a dead abstraction).

Why a gateway exists at all, distinct from calling `AIProvider` directly:
it is the single choke point where "never silently fall back to a paid
cloud API" (section 24, FAIL SAFE not fail-over) is enforced structurally.
An `AIGateway` constructed with only a local provider has no cloud
provider reference to fall back to even if some future call site tried —
the safety property is architectural, not a runtime check that could be
forgotten in a new call site.

Catches every `AIProviderError` subclass at this boundary and does NOT
re-raise it bare -- callers (AIAnalyst) are expected to catch
`AIProviderError` themselves for the specific outcomes they need (section
27's AI_UNAVAILABLE/MODEL_UNAVAILABLE/AI_TIMEOUT), so this class re-raises
unchanged rather than wrapping it in yet another exception type. What it
adds beyond a direct provider call: a place to log the call bookended by
duration (section 28: "record provider/model/operation/duration/
success-failure"), without every caller having to remember to log it
itself.
"""
from __future__ import annotations

import logging
import time

from app.ai.provider import AIGenerationRequest, AIGenerationResult, AIProvider, AIProviderError
from app.models.enums import ToolHealthStatus

logger = logging.getLogger(__name__)


class AIGateway:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def health_check(self) -> ToolHealthStatus:
        return await self._provider.health_check()

    async def generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        return await self._run("generate", request)

    async def structured_generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        return await self._run("structured_generate", request)

    async def _run(self, operation: str, request: AIGenerationRequest) -> AIGenerationResult:
        started = time.monotonic()
        try:
            if operation == "generate":
                result = await self._provider.generate(request)
            else:
                result = await self._provider.structured_generate(request)
        except AIProviderError:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "ai_call_failed provider=%s operation=%s duration_ms=%d",
                self._provider.name,
                operation,
                duration_ms,
            )
            raise
        else:
            # Never log request.prompt/system_prompt -- may contain
            # business data; never log anything credential-shaped either
            # (this provider family carries none, but the rule is a
            # property of this log line, not of what OllamaProvider
            # happens to hold today).
            logger.info(
                "ai_call_succeeded provider=%s model=%s operation=%s duration_ms=%d "
                "prompt_tokens=%s completion_tokens=%s",
                result.provider,
                result.model,
                operation,
                result.duration_ms,
                result.prompt_tokens,
                result.completion_tokens,
            )
            return result
