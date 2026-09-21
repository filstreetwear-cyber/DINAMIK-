"""
OllamaProvider — the one concrete `AIProvider` this milestone ships.
Talks to Ollama's own REST API (`POST /api/generate`, `GET /api/tags`) via
`httpx` (already a project dependency, pyproject.toml — no new runtime
dependency needed). Business logic never imports this module directly —
only `app/ai/bootstrap.py` does, per the milestone's own "CRITICAL
PRINCIPLE" (section 4): everything else goes through `AIGateway`.

Memory-safety choices this class makes for the 4GB-RAM target VPS
(section 9/26), not because the Ollama API forces them:

* `asyncio.Semaphore(max_concurrency)` — serializes concurrent generation
  calls (default max_concurrency=1). Ollama on a 4GB box loading a second
  concurrent context is the documented way to OOM it; this makes "one
  generation at a time" a property of THIS provider, not something every
  caller has to remember to respect.
* `num_ctx`/`num_predict` are passed through from the request/config,
  never left to Ollama's own defaults, which are tuned for far larger
  machines.
* `keep_alive` is read from config and passed on every call — controls how
  long Ollama keeps the model resident in RAM between calls; not exposed
  on AIGenerationRequest because it's a deployment/ops concern, not
  something a caller should decide per-call.

Failure mapping (section 27) is the point of this class existing as a
separate file from `provider.py`'s abstract definitions:

* connection refused / DNS failure / any `httpx.ConnectError` -> Ollama
  itself isn't reachable -> `AIUnavailableError`.
* `httpx.TimeoutException` -> `AITimeoutError`.
* HTTP 404 on `/api/generate`, or the model name in the response not
  matching the configured model -> the backend is up but the configured
  model isn't pulled (or a different one got pulled instead, e.g.
  qwen3:4b when the spec mandates qwen3:1.7b) -> `AIModelUnavailableError`.
  This is deliberately NOT the same branch as ConnectError: section 5's "do
  not silently substitute a different model size" needs this distinction
  to be a real, catchable outcome, not just a code comment.
* any other non-2xx status -> `AIUnavailableError` (reachable, but the call
  itself failed in a way this provider doesn't have a more specific bucket
  for -- still a controlled failure, not a crash).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.ai.provider import (
    AIGenerationRequest,
    AIGenerationResult,
    AIModelUnavailableError,
    AIProvider,
    AITimeoutError,
    AIUnavailableError,
)
from app.models.enums import ToolHealthStatus


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_concurrency: int = 1,
        temperature: float = 0.2,
        num_ctx: int = 2048,
        num_predict: int = 512,
        keep_alive: str = "5m",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._keep_alive = keep_alive
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def health_check(self) -> ToolHealthStatus:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(f"{self._base_url}/api/tags")
        except (httpx.ConnectError, httpx.TimeoutException):
            return ToolHealthStatus.UNAVAILABLE
        except httpx.HTTPError:
            return ToolHealthStatus.UNAVAILABLE

        if response.status_code != 200:
            return ToolHealthStatus.UNAVAILABLE

        try:
            payload = response.json()
        except ValueError:
            return ToolHealthStatus.UNAVAILABLE

        model_names = {entry.get("name") for entry in payload.get("models", [])}
        model_names |= {name.split(":")[0] if ":" in name else name for name in model_names if name}
        if self._model not in model_names:
            # Ollama itself is up -- reachable, just not configured with
            # the model this deployment expects. Distinct from "down".
            return ToolHealthStatus.CONFIGURATION_ERROR

        return ToolHealthStatus.AVAILABLE

    async def generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        return await self._call(request, structured=False)

    async def structured_generate(self, request: AIGenerationRequest) -> AIGenerationResult:
        if request.schema is None:
            raise ValueError("structured_generate requires request.schema to be set")
        return await self._call(request, structured=True)

    async def _call(self, request: AIGenerationRequest, *, structured: bool) -> AIGenerationResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": request.prompt,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": request.temperature if request.temperature is not None else self._temperature,
                "num_ctx": request.num_ctx if request.num_ctx is not None else self._num_ctx,
                "num_predict": request.num_predict if request.num_predict is not None else self._num_predict,
            },
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        if structured:
            payload["format"] = request.schema

        started = time.monotonic()
        async with self._semaphore:
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(f"{self._base_url}/api/generate", json=payload)
            except httpx.TimeoutException as exc:
                raise AITimeoutError(self.name, f"generation exceeded {self._timeout_seconds}s") from exc
            except httpx.ConnectError as exc:
                raise AIUnavailableError(self.name, f"could not reach Ollama at {self._base_url}") from exc
            except httpx.HTTPError as exc:
                raise AIUnavailableError(self.name, str(exc)) from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        if response.status_code == 404:
            raise AIModelUnavailableError(self.name, f"model '{self._model}' not found on Ollama backend")
        if response.status_code != 200:
            raise AIUnavailableError(self.name, f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")

        try:
            data = response.json()
        except ValueError as exc:
            raise AIUnavailableError(self.name, "Ollama returned a non-JSON response") from exc

        returned_model = data.get("model", self._model)
        if returned_model and returned_model.split(":")[0] != self._model.split(":")[0]:
            # Backend served a different model than requested -- never
            # silently accept this as a valid result (section 5).
            raise AIModelUnavailableError(
                self.name, f"expected model '{self._model}', Ollama served '{returned_model}'"
            )

        return AIGenerationResult(
            text=data.get("response", ""),
            model=returned_model or self._model,
            provider=self.name,
            duration_ms=duration_ms,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )
