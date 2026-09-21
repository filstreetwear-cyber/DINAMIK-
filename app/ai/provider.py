"""
AIProvider — Milestone 9.5. The abstraction the milestone's own "CRITICAL
PRINCIPLE" demands: DINAMIK is built around this, not around Ollama.
Mirrors `app/tools/core/provider.py`'s `IntelligenceProvider` shape on
purpose (ABC + typed request/result + a provider-specific error family
the caller catches to decide on fallback/failure) — same architectural
pattern this project already trusts, applied to a different layer.

Deliberately NOT the same class family as `IntelligenceProvider`/
`ProviderError` (app/tools/core/provider.py), even though the shapes
rhyme: section 23 of this milestone's spec makes the DATA-agent vs
AI-agent separation mandatory, and merging the two error hierarchies
would blur exactly that line — a caller catching `ProviderError` should
never accidentally swallow an AI failure, and vice versa.

`health_check()` reuses `ToolHealthStatus` (app/models/enums.py) rather
than inventing a parallel vocabulary — AVAILABLE/UNAVAILABLE/
CONFIGURATION_ERROR already say what's needed (service down vs. reachable
but misconfigured, e.g. the configured model isn't pulled).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.enums import ToolHealthStatus


class AIProviderError(Exception):
    """Base for all "this AI provider could not serve this call right
    now" errors — the AIGateway/AIAnalyst catch this family to return a
    controlled failure (section 27: AI_UNAVAILABLE/MODEL_UNAVAILABLE/
    AI_TIMEOUT), never to silently fall back to a paid cloud API
    (section 24: FAIL SAFE, not fail-over)."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        self.message = message
        super().__init__(f"{provider}: {message}")


class AIUnavailableError(AIProviderError):
    """The AI backend itself is unreachable (e.g. Ollama not running) —
    maps to section 27's AI_UNAVAILABLE."""


class AIModelUnavailableError(AIProviderError):
    """The backend is reachable but the configured model is not present
    (e.g. `qwen3:1.7b` was never pulled, or a different model was pulled
    instead) — maps to section 27's MODEL_UNAVAILABLE. Deliberately
    distinct from AIUnavailableError: one is "nothing is listening", the
    other is "something is listening but not what we asked for" — section
    5's "do NOT silently substitute qwen3:4b/8b/14b" needs this
    distinction to be enforceable, not just documented."""


class AITimeoutError(AIProviderError):
    """Generation did not complete within `timeout_seconds`
    (config/default.yaml's `ai.local.timeout_seconds`) — maps to section
    27's AI_TIMEOUT. Not retried automatically here: a caller (AIAnalyst)
    decides whether a retry makes sense for its own operation, this layer
    just reports what happened."""


class AICloudNotImplementedError(AIProviderError):
    """Raised at wiring time (app/ai/router.py), not call time, when
    config/default.yaml's `ai.provider` is set to "cloud" — this milestone
    ships local-only (section 4/24: zero mandatory API cost, no paid
    fallback). A controlled, explicit error rather than silently routing
    to local anyway (which would hide a real config mistake) or crashing
    with an AttributeError on a provider that was never built."""


@dataclass(frozen=True)
class AIGenerationRequest:
    """What the caller wants generated — provider-agnostic. `schema`,
    when set, asks the provider for output conforming to that JSON
    Schema (Ollama's structured-output `format` parameter,
    OllamaProvider.structured_generate); the caller still validates the
    result itself (section 14: "Validate AI output before accepting it")
    — a provider returning schema-conformant JSON is a hint to the model,
    not a substitute for real validation.
    """

    prompt: str
    system_prompt: str | None = None
    schema: dict | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    num_predict: int | None = None


@dataclass(frozen=True)
class AIGenerationResult:
    text: str
    model: str
    provider: str
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class AIProvider(ABC):
    """One concrete AI backend. `name` is written onto every `AIClaim`
    (app/ai/contracts.py) it produces, via the caller — see that file's
    own docstring on why model/provider provenance matters (section 19:
    "makes future audits possible")."""

    name: str

    @abstractmethod
    async def health_check(self) -> ToolHealthStatus:
        """Cheap check of whether this provider can currently serve
        calls, distinguishing UNAVAILABLE (nothing reachable) from
        CONFIGURATION_ERROR (reachable, wrong/missing model) — must not
        raise."""

    @abstractmethod
    async def generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        """Raises an AIProviderError subclass on failure. Free-form text
        generation — no schema/validation guarantees, callers that need
        structured output use structured_generate()."""

    @abstractmethod
    async def structured_generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        """Like generate(), but `request.schema` must be set — the
        provider asks the backend for schema-conformant output where the
        backend supports it. Still returns raw text: parsing/validating
        against the caller's own pydantic model is the caller's job
        (AIAnalyst), not this layer's — see AIGenerationRequest's own
        docstring."""
