"""
Fetch delle pagine Amazon.it per l'Anello 1 (2026-10-14).

Cosa fa: apre le pagine con Chromium headless (Playwright, profilo
persistente), aspetta un intervallo casuale tra una richiesta e l'altra,
rispetta un tetto giornaliero, salva l'HTML grezzo di ogni pagina come
evidenza e uno screenshot della parte visibile delle schede prodotto.

Cosa NON fa, per scelta: nessun tentativo di risolvere o aggirare un
CAPTCHA, nessuna rotazione di IP/proxy, nessun retry aggressivo. Un CAPTCHA
salva l'HTML (serve a capire cosa è successo) e solleva
CaptchaDetectedError: il chiamante ferma il batch.

Deve girare dal PC di casa (IP residenziale). Dal server OVH gli IP da
datacenter vengono bloccati molto prima.
"""
from __future__ import annotations

import asyncio
import json
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.tools.amazon_scraping.page_parser import CaptchaDetectedError, is_captcha_page

_DEFAULT_EXECUTABLE_PATH = "/opt/pw-browsers/chromium"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str
    html_path: Path
    screenshot_path: Path | None
    fetched_at: datetime


class DailyLimitReachedError(Exception):
    """Tetto giornaliero di pagine raggiunto: riprendere domani."""


class PageFetcher(ABC):
    """Interfaccia minima usata da scout_runner: i test iniettano un fake."""

    @abstractmethod
    async def fetch(self, url: str, *, kind: str, key: str, screenshot: bool = False) -> FetchedPage: ...

    async def close(self) -> None:
        return None


class _DailyCounter:
    """Contatore pagine/giorno persistito su file, così il tetto vale anche
    tra più esecuzioni nello stesso giorno."""

    def __init__(self, path: Path, limit: int) -> None:
        self._path = path
        self._limit = limit

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
            return {k: int(v) for k, v in data.items()}
        except (ValueError, OSError):
            return {}

    def today(self) -> int:
        return self._load().get(datetime.now(UTC).date().isoformat(), 0)

    def check(self) -> None:
        if self.today() >= self._limit:
            raise DailyLimitReachedError(f"daily page limit {self._limit} reached")

    def increment(self) -> None:
        data = self._load()
        key = datetime.now(UTC).date().isoformat()
        data[key] = data.get(key, 0) + 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))


def evidence_paths(evidence_dir: Path, kind: str, key: str, when: datetime) -> tuple[Path, Path]:
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    folder = evidence_dir / when.strftime("%Y-%m-%d") / kind
    return folder / f"{key}_{stamp}.html", folder / f"{key}_{stamp}.jpg"


class PlaywrightPageFetcher(PageFetcher):
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        cfg = self._settings.scout_config()
        self._min_delay = float(cfg.get("min_delay_seconds", 8))
        self._max_delay = float(cfg.get("max_delay_seconds", 15))
        self._quality = int(cfg.get("screenshot_quality", 70))
        self._evidence_dir = Path(self._settings.scout_evidence_dir)
        self._counter = _DailyCounter(
            self._evidence_dir / "daily_counter.json", int(cfg.get("daily_page_limit", 200))
        )
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._playwright: Any = None
        self._context: Any = None
        self._last_request_at: float | None = None

    async def _ensure_context(self) -> Any:
        if self._context is not None:
            return self._context
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        profile_dir = Path(self._settings.browser_profiles_dir) / "amazon_scout"
        profile_dir.mkdir(parents=True, exist_ok=True)
        executable = self._settings.browser_executable_path or (
            _DEFAULT_EXECUTABLE_PATH if Path(_DEFAULT_EXECUTABLE_PATH).exists() else None
        )
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=executable,
            headless=self._settings.browser_headless,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366, "height": 900},
        )
        return self._context

    async def _wait_turn(self) -> None:
        loop = asyncio.get_running_loop()
        if self._last_request_at is not None:
            target = self._rng.uniform(self._min_delay, self._max_delay)
            elapsed = loop.time() - self._last_request_at
            if elapsed < target:
                await self._sleep(target - elapsed)
        self._last_request_at = loop.time()

    async def fetch(self, url: str, *, kind: str, key: str, screenshot: bool = False) -> FetchedPage:
        self._counter.check()
        await self._wait_turn()
        context = await self._ensure_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            self._counter.increment()
            html = await page.content()
            now = datetime.now(UTC)
            html_path, shot_path = evidence_paths(self._evidence_dir, kind, key, now)
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")
            if is_captcha_page(html):
                raise CaptchaDetectedError(f"captcha on {url} (html saved to {html_path})")
            saved_shot: Path | None = None
            if screenshot:
                await page.screenshot(path=str(shot_path), type="jpeg", quality=self._quality, full_page=False)
                saved_shot = shot_path
            return FetchedPage(url=url, html=html, html_path=html_path, screenshot_path=saved_shot, fetched_at=now)
        finally:
            await page.close()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
