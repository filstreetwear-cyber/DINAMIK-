"""
BrowserSessionHandle — a live handle to a persistent browser profile
directory. Not the DB row (app.models.browser.BrowserSession) — that is
the durable record ("this profile exists, here's its status, when it was
last used"); this is what the engine actually opens on disk. The relation
is the same as BrowserTaskSpec/Outcome vs the BrowserTask DB row
(task.py).

Profile directories are resolved under settings.browser_profiles_dir,
namespaced by provider (and, if given, an account_label — so two accounts
for the same provider never share cookies/login state). No provider- or
account-specific code lives here; callers decide those strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class BrowserSessionHandle:
    provider: str
    profile_path: Path
    account_label: str = "default"


def resolve_session_handle(
    provider: str, account_label: str = "default", settings: Settings | None = None
) -> BrowserSessionHandle:
    settings = settings or get_settings()
    base = Path(settings.browser_profiles_dir)
    profile_path = base / provider / account_label
    profile_path.mkdir(parents=True, exist_ok=True)
    return BrowserSessionHandle(provider=provider, profile_path=profile_path, account_label=account_label)
