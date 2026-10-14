"""
AIRouter — resolves `config/default.yaml`'s `ai:` section into a concrete
`AIProvider`. A genuinely thin pass-through today (only `provider: local`
is implemented), kept as its own class rather than inlined into
bootstrap.py because section 4's architecture diagram names it as its own
layer, and because the *shape* of a two-way (soon N-way) decision should
exist even when only one branch is real — the alternative is that adding
cloud support later means restructuring bootstrap.py's control flow
instead of filling in an already-reserved branch here.

`provider: cloud` raises `AICloudNotImplementedError` rather than falling
back to local silently — section 24 (FAIL SAFE, not fail-over) and section
5 ("no API keys required for this milestone") both mean a config that asks
for cloud must be told clearly that cloud isn't built yet, not quietly
redirected to a different backend than what was configured.
"""
from __future__ import annotations

from typing import Any

from app.ai.ollama_provider import OllamaProvider
from app.ai.provider import AICloudNotImplementedError, AIProvider


class AIRouter:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def resolve_provider(self) -> AIProvider:
        provider_choice = self._config.get("provider", "local")

        if provider_choice == "cloud":
            raise AICloudNotImplementedError(
                "router", "ai.provider=cloud is configured but no cloud AIProvider is implemented in this milestone"
            )

        if provider_choice != "local":
            raise ValueError(f"unknown ai.provider value: {provider_choice!r} (expected 'local' or 'cloud')")

        local_config = self._config.get("local", {})
        backend = local_config.get("backend", "ollama")
        if backend != "ollama":
            raise ValueError(f"unknown ai.local.backend value: {backend!r} (only 'ollama' is implemented)")

        return OllamaProvider(
            base_url=local_config.get("base_url", "http://localhost:11434"),
            model=local_config.get("model", "qwen3:1.7b"),
            timeout_seconds=float(local_config.get("timeout_seconds", 60)),
            max_concurrency=int(local_config.get("max_concurrency", 1)),
            temperature=float(local_config.get("temperature", 0.2)),
            num_ctx=int(local_config.get("num_ctx", 2048)),
            num_predict=int(local_config.get("num_predict", 512)),
            keep_alive=str(local_config.get("keep_alive", "5m")),
        )
