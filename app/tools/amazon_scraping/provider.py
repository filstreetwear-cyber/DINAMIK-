"""
AmazonScrapingProvider — Fase 2 (roadmap Fil/Frank, 28/09): il provider REALE
(niente fixture) per PRODUCT_SEARCH/PRODUCT_DATA su Amazon.it, costruito
come proposto esplicitamente da Fil: un'interfaccia stabile con adapter
intercambiabili sotto, cosi' se uno smette di funzionare non si rifa'
DINAMIK, si cambia l'adapter.

Perche' e' un file NUOVO e non una modifica di app/tools/amazon/provider.py
(AmazonProvider, mock-only): quel provider ha gia' 13+ test che lo
assumono deterministico/mock (vedi tests/test_mock_providers.py). Toccarlo
per aggiungere scraping vero avrebbe rischiato quella suite per un
guadagno cosmetico — stessa logica "preserva il codice funzionante e
testato" gia' applicata altrove in questo progetto (vedi
opportunity_engine.py). I due provider convivono: AmazonProvider resta la
via mock per i test, AmazonScrapingProvider e' la via reale per la
pipeline quando MOCK_MODE=false.

STATO VERIFICATO IL 28/09, non assunto:
- AmzPyAdapter usa amzpy (libreria open source, gratuita, nessuna licenza
  a pagamento) - letto il suo codice sorgente vero (non la doc) installato
  qui: amzpy.parser NON estrae in nessun punto nome/ID/URL del venditore.
  Cerca solo il badge "Best Seller", niente altro con seller/merchant/sold.
  Quindi AmzPyAdapter dichiara supporto SOLO a PRODUCT_SEARCH/PRODUCT_DATA,
  MAI a SELLER_DATA - dichiararlo comunque e ritornare un campo vuoto
  sarebbe esattamente il tipo di "provider inventa un FACT che non ha
  osservato" che provider.py vieta esplicitamente nel suo docstring.
- Il test dal vivo contro amazon.it NON e' stato eseguibile da questo
  ambiente: il proxy dell'organizzazione blocca esplicitamente
  www.amazon.it (403 di policy, verificato con una richiesta diretta),
  e nessun device dell'utente era collegato alla sessione. Lo script di
  benchmark (phase0_benchmark_amazon_it.py, consegnato il turno prima di
  questo) resta il modo per validarlo su una macchina con rete vera.
  Le funzioni di parsing sotto sono percio' testate con HTML sintetico
  (tests/test_amazon_scraping_provider.py), MAI contro Amazon vero -
  questo e' dichiarato esplicitamente, non nascosto.

Il seller (nome/ID/URL) resta MISSING finche' un secondo adapter
(BrowserActAdapter, o un FutureAdapter) non lo fornisce davvero - vedi
Adapter.supports_seller_data qui sotto, che l'orchestrator puo' controllare
prima di aspettarsi quel campo.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.models.enums import AcquisitionMethod, DataType, ToolHealthStatus
from app.tools.core.capability import Capability
from app.tools.core.provider import (
    IntelligenceProvider,
    ProviderResult,
    ProviderUnavailableError,
)

_SUPPORTED = frozenset({Capability.PRODUCT_SEARCH, Capability.PRODUCT_DATA})
# SELLER_DATA deliberatamente FUORI da questo frozenset finche' nessun
# adapter registrato lo supporta davvero - vedi ScrapingAdapter.supports_seller_data.


@dataclass(frozen=True)
class ScrapedProduct:
    """Forma comune a cui ogni adapter deve tradurre il suo output grezzo,
    cosi' AmazonScrapingProvider non deve sapere nulla della libreria
    sotto. Campi None quando l'adapter non li ha estratti (mai inventati)."""

    asin: str | None
    title: str | None
    price: float | None
    currency: str | None
    brand: str | None
    rating: float | None
    review_count: int | None
    image_url: str | None
    product_url: str
    seller_name: str | None = None
    seller_id: str | None = None
    seller_url: str | None = None


class ScrapingAdapter(ABC):
    """Un motore di scraping concreto (amzpy, browser-act, ...). Nome
    stabile per lo stesso motivo di IntelligenceProvider.name: finisce
    scritto nella provenance dei dati."""

    name: str

    @property
    @abstractmethod
    def supports_seller_data(self) -> bool:
        """True solo se QUESTO adapter, verificato, estrae seller
        name/id/url. Falso di default per amzpy (vedi docstring del
        modulo) - un adapter futuro lo alza a True solo dopo essere stato
        verificato allo stesso modo, non per intenzione dichiarata."""

    @abstractmethod
    def search(self, query: str, max_pages: int = 1) -> list[str]:
        """Ritorna URL di prodotto grezzi (non ancora ScrapedProduct - la
        pagina di ricerca su Amazon porta meno campi della scheda
        prodotto, vedi amzpy.parser.parse_search_page)."""

    @abstractmethod
    def get_product(self, url: str) -> ScrapedProduct | None:
        """None se il fetch/parsing fallisce (CAPTCHA, blocco, pagina
        cambiata) - mai un ScrapedProduct con campi indovinati."""


class AmzPyAdapter(ScrapingAdapter):
    name = "amzpy"
    supports_seller_data = False  # verificato leggendo amzpy/parser.py il 28/09 - vedi docstring modulo

    def __init__(self, country_code: str = "it", scraper: Any | None = None) -> None:
        # `scraper` iniettabile per i test (stub con la stessa interfaccia di
        # amzpy.AmazonScraper, niente rete) - stesso principio del `gateway`
        # iniettabile in run_ai_analysis_engine. Import di amzpy qui, non in
        # cima al file: non deve essere una dipendenza obbligatoria di tutto
        # il progetto solo perche' un adapter la usa, stesso motivo per cui
        # AmazonProvider non importa mai amzpy.
        if scraper is not None:
            self._scraper = scraper
        else:
            from amzpy import AmazonScraper  # type: ignore[import-untyped]

            self._scraper = AmazonScraper(country_code=country_code)

    def search(self, query: str, max_pages: int = 1) -> list[str]:
        products = self._scraper.search_products(query=query, max_pages=max_pages)
        return [p["url"] for p in products if p.get("url")]

    def get_product(self, url: str) -> ScrapedProduct | None:
        data = self._scraper.get_product_details(url)
        if not data:
            return None
        return ScrapedProduct(
            asin=data.get("asin"),
            title=data.get("title"),
            price=data.get("price"),
            currency=data.get("currency"),
            brand=data.get("brand"),
            rating=data.get("rating"),
            review_count=data.get("review_count"),
            image_url=data.get("img_url") or data.get("image_url"),
            product_url=url,
            # seller_* MAI valorizzati qui - supports_seller_data=False lo dichiara,
            # questi restano None per costruzione, non per dimenticanza.
        )


class AmazonScrapingProvider(IntelligenceProvider):
    """Front-door stabile verso il Capability/Orchestrator layer. Non sa
    nulla di amzpy/browser-act: parla solo ScrapingAdapter. Cambiare
    motore di scraping = cambiare l'adapter passato al costruttore, zero
    modifiche qui o a monte (orchestrator/registry)."""

    name = "amazon_scraping"

    def __init__(self, adapter: ScrapingAdapter, settings: Settings | None = None) -> None:
        self._adapter = adapter
        self._settings = settings or get_settings()
        # supported_capabilities e' per-istanza, non per-classe: dipende da
        # cosa l'adapter iniettato dichiara di saper fare davvero.
        caps = set(_SUPPORTED)
        if adapter.supports_seller_data:
            caps.add(Capability.SELLER_DATA)
        self.supported_capabilities = frozenset(caps)

    async def health(self) -> ToolHealthStatus:
        # Nessuna chiamata di rete qui apposta (contratto di health():
        # "cheap, side-effect-free") - riporta solo se l'adapter e' pronto
        # a essere usato, non se Amazon e' raggiungibile in questo momento.
        return ToolHealthStatus.AVAILABLE

    async def fetch(self, capability: Capability, **params: Any) -> list[ProviderResult]:
        if not self.supports(capability):
            raise ProviderUnavailableError(
                self.name,
                f"does not support {capability.value} (adapter={self._adapter.name}, "
                f"supports_seller_data={self._adapter.supports_seller_data})",
            )

        observed_at = datetime.now(UTC)

        if capability is Capability.PRODUCT_SEARCH:
            query = params.get("query")
            if not query:
                raise ProviderUnavailableError(self.name, "fetch() requires a 'query' parameter")
            max_pages = params.get("max_pages", 1)
            urls = self._adapter.search(query=query, max_pages=max_pages)
            return [
                ProviderResult(
                    capability=Capability.PRODUCT_SEARCH,
                    data={"product_urls": urls},
                    # FACT: la lista di URL ritornati dalla ricerca e' un dato osservato
                    # direttamente (la pagina li conteneva), non una stima.
                    data_type=DataType.FACT,
                    confidence=95.0,
                    acquisition_method=AcquisitionMethod.HTML,
                    observed_at=observed_at,
                    is_mock=False,
                )
            ]

        # PRODUCT_DATA
        url = params.get("url")
        if not url:
            raise ProviderUnavailableError(self.name, "fetch() requires a 'url' parameter")
        product = self._adapter.get_product(url)
        if product is None:
            return []  # fetch riuscito, nessun dato estraibile (CAPTCHA/blocco/pagina cambiata) - non un'eccezione

        return [
            ProviderResult(
                capability=Capability.PRODUCT_DATA,
                data={
                    "asin": product.asin,
                    "title": product.title,
                    "price": product.price,
                    "currency": product.currency,
                    "brand": product.brand,
                    "rating": product.rating,
                    "review_count": product.review_count,
                    "image_url": product.image_url,
                    "seller_name": product.seller_name,
                    "seller_id": product.seller_id,
                    "seller_url": product.seller_url,
                },
                # FACT sui campi letti direttamente dal markup (title, price cosi' come mostrato);
                # non separiamo qui campo per campo come fa WebsiteIntelligenceProvider perche'
                # amzpy stesso non distingue "letto" da "dedotto" nel suo output - approssimazione
                # dichiarata, da rivedere se/quando un adapter distingue i due casi.
                data_type=DataType.FACT,
                confidence=90.0,
                acquisition_method=AcquisitionMethod.HTML,
                observed_at=observed_at,
                source_url=url,
                is_mock=False,
            )
        ]
