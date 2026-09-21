"""
FastAPI application entrypoint.

The full API surface (research jobs, prospects, brands, agent runs — see
docs/architecture.md section 5 of the original spec) lands in later
milestones (Milestone 13). For Milestone 1 this proves only that the
application boots and configuration loads correctly.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Dinamik Advisor — Amazon Intelligence Platform",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "environment": settings.environment,
        "mock_mode": settings.mock_mode,
    }
