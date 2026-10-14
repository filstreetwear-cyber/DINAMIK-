"""
Anello 1: PlaywrightPageFetcher contro un server HTTP locale reale
(127.0.0.1, nessun proxy, nessuna Amazon) col Chromium vero se presente.
Verifica: HTML salvato come evidenza, screenshot, attesa tra richieste,
tetto giornaliero, CAPTCHA -> stop con HTML salvato.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.tools.amazon_scraping.fetcher import (
    DailyLimitReachedError,
    PlaywrightPageFetcher,
    _DailyCounter,
)
from app.tools.amazon_scraping.page_parser import CaptchaDetectedError
from app.tools.browser.engine import default_executable_path_exists
from tests.anello1_html import CAPTCHA_HTML, product_html

_PAGES = {"/dp/B0LOCAL001": product_html(), "/captcha": CAPTCHA_HTML}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _PAGES.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((body or "not found").encode())

    def log_message(self, *args) -> None:
        return


@pytest.fixture
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_daily_counter(tmp_path):
    counter = _DailyCounter(tmp_path / "c.json", limit=2)
    counter.check()
    counter.increment()
    counter.increment()
    assert counter.today() == 2
    with pytest.raises(DailyLimitReachedError):
        counter.check()


@pytest.mark.skipif(not default_executable_path_exists(), reason="Chromium not installed at the default path")
async def test_real_browser_fetch_saves_evidence_waits_and_stops_on_captcha(local_server, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    fetcher = PlaywrightPageFetcher(get_settings(), sleep=fake_sleep)
    try:
        page = await fetcher.fetch(f"{local_server}/dp/B0LOCAL001", kind="product", key="B0LOCAL001", screenshot=True)
        assert "productTitle" in page.html
        assert page.html_path.exists() and "sellerProfileTriggerId" in page.html_path.read_text()
        assert page.screenshot_path is not None and page.screenshot_path.stat().st_size > 0

        with pytest.raises(CaptchaDetectedError):
            await fetcher.fetch(f"{local_server}/captcha", kind="listing", key="cap")
        assert sleeps and 0 < sleeps[0] <= 15  # attesa casuale prima della seconda richiesta
        saved = list(Path(get_settings().scout_evidence_dir).rglob("cap_*.html"))
        assert saved, "l'HTML della pagina CAPTCHA va salvato per capire cosa è successo"
    finally:
        await fetcher.close()
