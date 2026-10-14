"""
AmazonScrapingProvider / AmzPyAdapter — nessun test qui tocca amazon.it vero
(bloccato da questo ambiente, vedi docstring di app/tools/amazon_scraping/provider.py).
Due livelli:
1. AmzPyAdapter con uno stub che imita l'interfaccia di amzpy.AmazonScraper —
   verifica che il mapping verso ScrapedProduct sia corretto e che i campi
   seller_* restino sempre None (mai inventati).
2. Il parser VERO di amzpy (non uno stub) contro un HTML sintetico costruito
   sugli stessi selettori che amzpy.parser usa davvero (letti dal sorgente
   installato) — prova concreta, non solo dichiarata, che oggi non estrae
   alcun dato sul venditore da una pagina prodotto.
3. AmazonScrapingProvider con un adapter finto — verifica capability gating
   (SELLER_DATA presente solo se l'adapter la dichiara) e mapping a
   ProviderResult.
"""
from __future__ import annotations

import pytest

from app.models.enums import DataType, ToolHealthStatus
from app.tools.amazon_scraping.provider import (
    AmazonScrapingProvider,
    AmzPyAdapter,
    ScrapedProduct,
    ScrapingAdapter,
)
from app.tools.core.capability import Capability
from app.tools.core.provider import ProviderUnavailableError


class _StubAmzpyScraper:
    """Stessa interfaccia di amzpy.AmazonScraper (search_products,
    get_product_details) ma senza alcuna rete - dati fissi, noti."""

    def __init__(self, product_details: dict | None, search_urls: list[str] | None = None) -> None:
        self._product_details = product_details
        self._search_urls = search_urls or []

    def search_products(self, query: str, max_pages: int = 1) -> list[dict]:
        return [{"url": u} for u in self._search_urls]

    def get_product_details(self, url: str) -> dict | None:
        return self._product_details


def test_amzpy_adapter_maps_fields_and_never_fills_seller():
    stub = _StubAmzpyScraper(
        product_details={
            "asin": "B0EXAMPLE1",
            "title": "Organizzatore da cucina",
            "price": 19.99,
            "currency": "€",
            "brand": "TestBrand",
            "rating": 4.3,
            "review_count": 128,
            "img_url": "https://example.test/img.jpg",
        }
    )
    adapter = AmzPyAdapter(scraper=stub)

    product = adapter.get_product("https://www.amazon.it/dp/B0EXAMPLE1")

    assert product is not None
    assert product.asin == "B0EXAMPLE1"
    assert product.title == "Organizzatore da cucina"
    assert product.price == 19.99
    assert product.brand == "TestBrand"
    # Il punto CRITICO del test: mai un valore inventato per il venditore.
    assert product.seller_name is None
    assert product.seller_id is None
    assert product.seller_url is None
    assert adapter.supports_seller_data is False


def test_amzpy_adapter_returns_none_on_failed_extraction():
    stub = _StubAmzpyScraper(product_details=None)  # amzpy stesso ritorna None su CAPTCHA/blocco/parsing fallito
    adapter = AmzPyAdapter(scraper=stub)

    assert adapter.get_product("https://www.amazon.it/dp/B0DOESNOTMATTER") is None


def test_amzpy_adapter_search_extracts_urls():
    stub = _StubAmzpyScraper(
        product_details=None,
        search_urls=["https://www.amazon.it/dp/B0AAA", "https://www.amazon.it/dp/B0BBB"],
    )
    adapter = AmzPyAdapter(scraper=stub)

    urls = adapter.search(query="organizzatore cucina")

    assert urls == ["https://www.amazon.it/dp/B0AAA", "https://www.amazon.it/dp/B0BBB"]


def test_amzpy_real_parser_extracts_no_seller_fields_from_synthetic_product_page():
    """Non uno stub: e' il vero amzpy.parser.parse_product_page, importato
    dalla libreria installata, contro un HTML sintetico costruito sugli
    stessi selettori che il parser usa davvero (#productTitle, .a-price-whole,
    #bylineInfo - letti dal sorgente). Prova diretta, non solo dichiarata,
    che oggi il venditore non viene estratto da nessuna parte."""
    from amzpy.parser import parse_product_page

    synthetic_html = """
    <html><body>
      <span id="productTitle">Organizzatore da cucina in bambu</span>
      <div id="bylineInfo">Visita lo Store di TestBrand</div>
      <span class="a-price-whole">19</span><span class="a-price-fraction">99</span>
      <span class="a-price-symbol">€</span>
      <div id="merchant-info">Venduto e spedito da Mario Rossi Commerce Srl</div>
      <a id="sellerProfileTriggerId" href="/sp?seller=A1B2C3D4E5">Mario Rossi Commerce Srl</a>
    </body></html>
    """

    result = parse_product_page(synthetic_html, url="https://www.amazon.it/dp/B0EXAMPLE1", country_code="it")

    assert result is not None
    assert result.get("title") == "Organizzatore da cucina in bambu"
    # Il merchant-info e il link del venditore SONO nell'HTML sintetico (li ho messi apposta,
    # con lo stesso id/selettore che Amazon usa davvero) - ma il parser di amzpy non li legge:
    assert "seller" not in result
    assert "merchant" not in result
    assert "seller_name" not in result


@pytest.mark.asyncio
async def test_amazon_scraping_provider_health_is_always_available():
    provider = AmazonScrapingProvider(adapter=AmzPyAdapter(scraper=_StubAmzpyScraper(product_details=None)))
    assert await provider.health() == ToolHealthStatus.AVAILABLE


@pytest.mark.asyncio
async def test_amazon_scraping_provider_excludes_seller_data_when_adapter_does_not_support_it():
    provider = AmazonScrapingProvider(adapter=AmzPyAdapter(scraper=_StubAmzpyScraper(product_details=None)))

    assert Capability.SELLER_DATA not in provider.supported_capabilities
    with pytest.raises(ProviderUnavailableError):
        await provider.fetch(Capability.SELLER_DATA, url="https://www.amazon.it/dp/B0EXAMPLE1")


class _FakeSellerCapableAdapter(ScrapingAdapter):
    """Adapter finto che DICHIARA supporto seller (simula un futuro
    BrowserActAdapter validato) - serve solo a provare che il gating della
    capability in AmazonScrapingProvider risponde davvero al flag
    dell'adapter, non e' hardcoded."""

    name = "fake_seller_capable"
    supports_seller_data = True

    def search(self, query: str, max_pages: int = 1) -> list[str]:
        return []

    def get_product(self, url: str):  # type: ignore[no-untyped-def]
        return ScrapedProduct(
            asin="B0EXAMPLE1",
            title="Prodotto di prova",
            price=9.99,
            currency="€",
            brand="Brand",
            rating=4.0,
            review_count=10,
            image_url=None,
            product_url=url,
            seller_name="Mario Rossi Commerce Srl",
            seller_id="A1B2C3D4E5",
            seller_url="https://www.amazon.it/sp?seller=A1B2C3D4E5",
        )


@pytest.mark.asyncio
async def test_amazon_scraping_provider_includes_seller_data_when_adapter_supports_it():
    provider = AmazonScrapingProvider(adapter=_FakeSellerCapableAdapter())

    assert Capability.SELLER_DATA in provider.supported_capabilities


@pytest.mark.asyncio
async def test_amazon_scraping_provider_product_data_maps_all_fields():
    provider = AmazonScrapingProvider(adapter=_FakeSellerCapableAdapter())

    results = await provider.fetch(Capability.PRODUCT_DATA, url="https://www.amazon.it/dp/B0EXAMPLE1")

    assert len(results) == 1
    r = results[0]
    assert r.data["seller_name"] == "Mario Rossi Commerce Srl"
    assert r.data_type == DataType.FACT
    assert r.is_mock is False


@pytest.mark.asyncio
async def test_amazon_scraping_provider_product_data_returns_empty_list_on_failed_extraction():
    provider = AmazonScrapingProvider(adapter=AmzPyAdapter(scraper=_StubAmzpyScraper(product_details=None)))

    results = await provider.fetch(Capability.PRODUCT_DATA, url="https://www.amazon.it/dp/B0GONE")

    assert results == []  # fetch riuscito, nessun dato estraibile - non un'eccezione


@pytest.mark.asyncio
async def test_amazon_scraping_provider_search_maps_urls():
    stub = _StubAmzpyScraper(product_details=None, search_urls=["https://www.amazon.it/dp/B0AAA"])
    provider = AmazonScrapingProvider(adapter=AmzPyAdapter(scraper=stub))

    results = await provider.fetch(Capability.PRODUCT_SEARCH, query="organizzatore cucina")

    assert results[0].data["product_urls"] == ["https://www.amazon.it/dp/B0AAA"]


@pytest.mark.asyncio
async def test_amazon_scraping_provider_requires_query_for_search():
    provider = AmazonScrapingProvider(adapter=AmzPyAdapter(scraper=_StubAmzpyScraper(product_details=None)))
    with pytest.raises(ProviderUnavailableError):
        await provider.fetch(Capability.PRODUCT_SEARCH)


@pytest.mark.asyncio
async def test_amazon_scraping_provider_requires_url_for_product_data():
    provider = AmazonScrapingProvider(adapter=AmzPyAdapter(scraper=_StubAmzpyScraper(product_details=None)))
    with pytest.raises(ProviderUnavailableError):
        await provider.fetch(Capability.PRODUCT_DATA)
