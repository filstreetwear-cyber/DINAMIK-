"""
PerformanceAuditor — Fase 3/4 (roadmap Fil/Frank, 28/09): page_speed_score
e seo_score reali per WebsiteProfile, tramite la CLI di Lighthouse (Google,
open source, MIT — nessun costo, nessuna chiave).

PERCHE' UN ADAPTER SEPARATO E OPZIONALE, non semplicemente aggiunto al
fetch() esistente di WebsiteIntelligenceProvider:
- E' un ordine di grandezza piu' costoso della pipeline HTTP/browser gia'
  esistente: un audit Lighthouse vero lancia un Chromium headless e
  naviga la pagina per ~10-30s, contro i millisecondi di un fetch HTTP.
  Farlo scattare di default su OGNI chiamata WEBSITE_DATA della pipeline
  sarebbe lo stesso errore di design che il commento su "il browser tier
  non e' mai il default" in provider.py gia' evita per il fallback JS —
  qui e' ancora piu' vero perche' non e' nemmeno necessario per ottenere
  gli altri segnali (technology/chatbot/title/mobile_friendly restano
  derivabili dal solo HTML). Per questo e' dietro il parametro esplicito
  `include_performance_audit=True` passato a fetch(), non sempre attivo.
- E' un binario esterno (npm, non pip) — una dipendenza diversa da tutto
  il resto del progetto (vedi pyproject.toml, dove amzpy e' invece una
  dipendenza Python normale). Se il binario manca (VPS senza Node, o
  ambiente CI senza `npm install -g lighthouse`), audit() ritorna None:
  stesso principio "niente FACT/valore inventato se l'osservazione non e'
  riuscita" gia' seguito da AmzPyAdapter.get_product() e
  _try_browser_fallback() qui accanto in website/provider.py.

VERIFICATO IL 28/09, non assunto — a differenza del benchmark amzpy
(bloccato da questo ambiente contro amazon.it vero), QUESTO e' stato
testato dal vivo, end-to-end, proprio da questa sandbox:
1. `npm install -g lighthouse` (registry.npmjs.org e' nell'allowlist del
   proxy di questo ambiente — non bloccato come amazon.it/example.com).
2. Lanciato un vero server HTTP locale (loopback, 127.0.0.1 — mai
   attraversa il proxy dell'organizzazione, quindi non e' soggetto al
   blocco che invece impedisce di testare contro un sito esterno reale).
3. Eseguito `lighthouse http://127.0.0.1:PORT/ --output=json ...` con il
   Chromium gia' preinstallato in questa sandbox
   (/opt/pw-browsers/chromium-*/chrome-linux/chrome via CHROME_PATH) e
   letto il vero report.json prodotto — non la documentazione di
   Lighthouse, l'output reale — per costruire il parsing sotto:
     report["categories"]["performance"]["score"]  -> float 0.0-1.0 o None
     report["categories"]["seo"]["score"]           -> float 0.0-1.0 o None
     report["runtimeError"]                          -> dict con "code" se
                                                          il load e' fallito
                                                          (es. CHROME_INTERSTITIAL_ERROR
                                                          su un URL irraggiungibile),
                                                          None se ok
   Testato anche il percorso di fallimento (URL su una porta chiusa):
   runtimeError valorizzato, categories[*]["score"] = None in quel caso —
   e' il segnale usato sotto per distinguere "misurato" da "fallito".

Quello che NON e' ancora verificato: un audit contro un sito reale esterno
(bloccato allo stesso modo di amazon.it da questo ambiente) e contro un
sito con performance davvero scarse (il test locale sopra e' una pagina
statica minuscola, punteggio 1.0 su entrambe le categorie — un caso
"tutto ok", non un caso limite). Il codice sotto non assume nulla su
QUEI risultati specifici, solo sulla FORMA del report (chiavi presenti,
range 0.0-1.0, runtimeError come segnale di fallimento) — quella e'
verificata.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import Settings, get_settings

_DEFAULT_TIMEOUT_SECONDS = 60.0
_LIGHTHOUSE_BIN_NAME = "lighthouse"
# Same stable symlink app/tools/browser/engine.py's _DEFAULT_EXECUTABLE_PATH
# points at (this sandbox's pre-installed Chromium) — Lighthouse needs a
# Chrome/Chromium binary exactly like the browser-tier fallback does, so it
# reuses the same resolution order: explicit param, then
# settings.browser_executable_path (BROWSER_EXECUTABLE_PATH env var — real
# deployments override it here), then this hardcoded sandbox default.
_DEFAULT_CHROME_PATH = "/opt/pw-browsers/chromium"


@dataclass(frozen=True)
class PerformanceSignals:
    """0-100 (non 0.0-1.0 di Lighthouse — convertito qui una volta sola,
    cosi' website_opportunity.py e WebsiteProfile.page_speed_score/seo_score
    (Integer, app/models/website.py) non devono sapere nulla della scala
    nativa di Lighthouse)."""

    page_speed_score: int
    seo_score: int


class PerformanceAuditor(ABC):
    """Nome stabile per lo stesso motivo di ScrapingAdapter.name /
    IntelligenceProvider.name — finisce nella provenance del dato."""

    name: str

    @abstractmethod
    async def audit(self, url: str) -> PerformanceSignals | None:
        """None se l'audit non produce un risultato utilizzabile (binario
        assente, timeout, pagina irraggiungibile/interstitial, JSON
        malformato) — mai un punteggio indovinato."""


def is_lighthouse_available(lighthouse_bin: str | None = None) -> bool:
    """Utile all'orchestrator/ai chiamanti per decidere se vale la pena
    passare include_performance_audit=True, senza dover prima fare un
    audit fallito solo per scoprirlo."""
    return shutil.which(lighthouse_bin or _LIGHTHOUSE_BIN_NAME) is not None


class LighthousePerformanceAuditor(PerformanceAuditor):
    name = "lighthouse_cli"

    def __init__(
        self,
        lighthouse_bin: str | None = None,
        chrome_path: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        settings: Settings | None = None,
    ) -> None:
        # Risolti pigri (non al primo import) cosi' costruire il provider
        # non richiede che lighthouse/chrome siano gia' installati — solo
        # chiamare audit() lo richiede, stesso principio del lazy `from
        # amzpy import AmazonScraper` in AmzPyAdapter.
        self._lighthouse_bin = lighthouse_bin or _LIGHTHOUSE_BIN_NAME
        self._timeout_seconds = timeout_seconds
        if chrome_path:
            self._chrome_path = chrome_path
        else:
            resolved_settings = settings or get_settings()
            self._chrome_path = os.environ.get("CHROME_PATH") or resolved_settings.browser_executable_path or _DEFAULT_CHROME_PATH

    async def audit(self, url: str) -> PerformanceSignals | None:
        if shutil.which(self._lighthouse_bin) is None:
            return None  # binario non installato su questa macchina — feature disattivata, non un errore

        fd, output_path = tempfile.mkstemp(prefix="lighthouse_", suffix=".json")
        os.close(fd)
        try:
            env = dict(os.environ)
            env["CHROME_PATH"] = self._chrome_path

            proc = await asyncio.create_subprocess_exec(
                self._lighthouse_bin,
                url,
                "--output=json",
                f"--output-path={output_path}",
                "--chrome-flags=--headless=new --no-sandbox --disable-gpu",
                "--only-categories=performance,seo",
                "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=self._timeout_seconds)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return None

            return self._parse_report(output_path)
        except (OSError, ValueError):
            # OSError: binario sparito tra il which() e l'exec, permessi, ecc.
            # ValueError: mkstemp/percorso invalido — nessuno di questi e' un
            # dato da propagare come punteggio, sono un audit non riuscito.
            return None
        finally:
            try:
                os.remove(output_path)
            except OSError:
                pass  # file gia' assente/non scritto — nessun report da ripulire

    @staticmethod
    def _parse_report(output_path: str) -> PerformanceSignals | None:
        try:
            with open(output_path, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        if report.get("runtimeError"):
            return None  # verificato dal vivo (CHROME_INTERSTITIAL_ERROR su porta chiusa): il load e' fallito

        categories = report.get("categories", {})
        perf_score = categories.get("performance", {}).get("score")
        seo_score = categories.get("seo", {}).get("score")
        if perf_score is None or seo_score is None:
            return None  # stessa forma osservata nel run di fallimento: score None quando la pagina non carica

        return PerformanceSignals(
            page_speed_score=round(perf_score * 100),
            seo_score=round(seo_score * 100),
        )
