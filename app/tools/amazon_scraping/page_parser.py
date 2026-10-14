"""
Parser delle pagine Amazon.it per l'Anello 1 (2026-10-14): scheda prodotto
(/dp/{ASIN}), profilo venditore (/sp?seller={ID}) e pagine elenco
(bestseller/ricerca) da cui ricavare ASIN.

Funzioni pure, nessuna rete: ricevono HTML, ritornano dataclass. Il fetch
vive in fetcher.py. Separati apposta: quando Amazon cambia markup si
corregge e si ri-testa questo file contro l'HTML grezzo salvato come
evidenza, senza rifare nessuna richiesta.

Regola di sempre su questo progetto: un campo non trovato resta None, mai
indovinato. I selettori sono quelli noti delle pagine Amazon EU; finché il
gate di validazione (docs/architecture/milestone-anello-1-prompt.md,
Step 1.5) non passa su pagine reali, sono un'ipotesi testata solo contro
HTML sintetico.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_DP_LINK_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)")
_INT_RE = re.compile(r"\d[\d.\s ]*")
_BSR_RE = re.compile(r"n\.\s*([\d.]+)\s+in\s+([^(\n]+)", re.IGNORECASE)
# P.IVA nel blocco "Informazioni dettagliate sul venditore": prefisso paese
# opzionale + cifre/lettere. Per l'Italia sono 11 cifre.
_VAT_LABEL_RE = re.compile(r"(?:partita\s+iva|numero\s+di\s+partita\s+iva|vat|ust-?idnr)", re.IGNORECASE)
_VAT_VALUE_RE = re.compile(r"\b([A-Z]{2})?\s?([0-9A-Z]{8,12})\b")

_AMAZON_SELLER_NAMES = frozenset({"amazon", "amazon.it", "amazon eu s.a r.l.", "amazon eu s.à r.l."})

_CAPTCHA_MARKERS = (
    "/errors/validatecaptcha",
    "captchacharacters",
    "inserisci i caratteri che vedi",
    "enter the characters you see below",
    "api-services-support@amazon.com",
)


@dataclass(frozen=True)
class ProductPageData:
    asin: str
    marketplace: str
    title: str | None
    brand: str | None
    seller_name: str | None
    seller_id: str | None
    seller_is_amazon: bool
    rating: float | None
    review_count: int | None
    price: float | None
    currency: str | None
    bsr: int | None
    bsr_category: str | None
    bullet_points: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    has_video: bool = False
    has_a_plus_content: bool = False
    description: str | None = None

    @property
    def title_length(self) -> int:
        return len(self.title) if self.title else 0


@dataclass(frozen=True)
class SellerProfileData:
    seller_id: str
    marketplace: str
    display_name: str | None
    business_name: str | None
    business_address: str | None
    business_country: str | None  # ISO-2, dedotto dall'ultima riga dell'indirizzo o dal prefisso P.IVA
    vat_number: str | None  # senza prefisso paese
    vat_country: str | None  # prefisso paese della P.IVA, se presente


class CaptchaDetectedError(Exception):
    """La pagina ricevuta è una pagina CAPTCHA/robot-check. Il chiamante
    deve fermare il batch: non si tenta mai di risolverla."""


def is_captcha_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _CAPTCHA_MARKERS)


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    match = _INT_RE.search(raw)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def _parse_it_decimal(raw: str | None) -> float | None:
    """'4,3 su 5 stelle' -> 4.3 ; '1.234,56 €' -> 1234.56"""
    if not raw:
        return None
    match = re.search(r"\d[\d.]*,\d+|\d+", raw)
    if not match:
        return None
    value = match.group(0).replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _seller_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    query = parse_qs(urlparse(href).query)
    for key in ("seller", "me", "merchant"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    return None


def _clean_brand(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    for prefix in ("Visita lo Store di ", "Marca: ", "Brand: ", "Visit the "):
        text = text.removeprefix(prefix)
    text = text.removesuffix(" Store")
    return text.strip() or None


def _extract_seller(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    trigger = soup.select_one("#sellerProfileTriggerId")
    if trigger is not None:
        name = _text(trigger)
        href = trigger.get("href") if isinstance(trigger.get("href"), str) else None
        return name, _seller_id_from_href(href)  # type: ignore[arg-type]

    # Buybox tabulare ("Venduto da" / "Sold by").
    for row in soup.select("#tabular-buybox .tabular-buybox-text, #tabular-buybox [tabular-attribute-name]"):
        label = row.get("tabular-attribute-name")
        if label in ("Venduto da", "Sold by"):
            link = row.select_one("a")
            name = _text(link) or _text(row)
            href = link.get("href") if link is not None else None
            return name, _seller_id_from_href(href if isinstance(href, str) else None)

    merchant = soup.select_one("#merchant-info")
    if merchant is not None:
        link = merchant.select_one("a[href*='seller=']")
        if link is not None:
            href = link.get("href")
            return _text(link), _seller_id_from_href(href if isinstance(href, str) else None)
        text = _text(merchant) or ""
        if "amazon" in text.lower():
            return "Amazon", None
    return None, None


def _extract_images(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for img in soup.select("#altImages li.imageThumbnail img, #altImages li.item img"):
        src = img.get("src")
        if isinstance(src, str) and src and src not in urls:
            urls.append(src)
    if not urls:
        main = soup.select_one("#landingImage, #imgBlkFront")
        if main is not None:
            src = main.get("data-old-hires") or main.get("src")
            if isinstance(src, str) and src:
                urls.append(src)
    return urls


def _extract_bsr(soup: BeautifulSoup) -> tuple[int | None, str | None]:
    candidates = soup.select(
        "#detailBulletsWrapper_feature_div li, #productDetails_detailBullets_sections1 tr, "
        "#prodDetails tr, #SalesRank"
    )
    for node in candidates:
        text = _text(node) or ""
        if "classifica bestseller" not in text.lower() and "best sellers rank" not in text.lower():
            continue
        match = _BSR_RE.search(text)
        if match:
            rank = _parse_int(match.group(1))
            return rank, match.group(2).strip()
    return None, None


def parse_product_page(html: str, asin: str, marketplace: str = "IT") -> ProductPageData:
    if is_captcha_page(html):
        raise CaptchaDetectedError(f"captcha page returned for ASIN {asin}")

    soup = BeautifulSoup(html, "lxml")

    title = _text(soup.select_one("#productTitle"))
    brand = _clean_brand(_text(soup.select_one("#bylineInfo")))
    seller_name, seller_id = _extract_seller(soup)
    seller_is_amazon = bool(seller_name) and seller_name.strip().lower() in _AMAZON_SELLER_NAMES  # type: ignore[union-attr]

    rating_node = soup.select_one("#acrPopover")
    rating_raw = rating_node.get("title") if rating_node is not None else None
    rating = _parse_it_decimal(rating_raw if isinstance(rating_raw, str) else _text(rating_node))
    review_count = _parse_int(_text(soup.select_one("#acrCustomerReviewText")))

    price_node = soup.select_one(".a-price .a-offscreen") or soup.select_one("#priceblock_ourprice")
    price_text = _text(price_node)
    price = _parse_it_decimal(price_text)
    currency = "EUR" if price_text and "€" in price_text else None

    bsr, bsr_category = _extract_bsr(soup)

    bullets = [
        text
        for li in soup.select("#feature-bullets ul li span.a-list-item")
        if (text := _text(li))
    ]
    has_video = soup.select_one("#altImages li.videoThumbnail, .videoBlockIngress, #videoCount") is not None
    has_a_plus = soup.select_one("#aplus, #aplus_feature_div .aplus-v2") is not None
    description = _text(soup.select_one("#productDescription"))

    return ProductPageData(
        asin=asin,
        marketplace=marketplace,
        title=title,
        brand=brand,
        seller_name=seller_name,
        seller_id=seller_id,
        seller_is_amazon=seller_is_amazon,
        rating=rating,
        review_count=review_count,
        price=price,
        currency=currency,
        bsr=bsr,
        bsr_category=bsr_category,
        bullet_points=bullets,
        image_urls=_extract_images(soup),
        has_video=has_video,
        has_a_plus_content=has_a_plus,
        description=description,
    )


def _country_from_address(address: str | None) -> str | None:
    if not address:
        return None
    last = address.strip().splitlines()[-1].strip() if address.strip() else ""
    last_token = last.split()[-1] if last.split() else ""
    if len(last_token) == 2 and last_token.isalpha() and last_token.isupper():
        return last_token
    return None


def parse_seller_page(html: str, seller_id: str, marketplace: str = "IT") -> SellerProfileData:
    """Blocco "Informazioni dettagliate sul venditore": Amazon lo mostra per
    gli obblighi informativi UE. Struttura tipica: righe <span> con
    etichetta "Nome dell'azienda:", "Partita IVA:", "Indirizzo aziendale:"
    seguite da righe di indirizzo, l'ultima delle quali è il codice paese."""
    if is_captcha_page(html):
        raise CaptchaDetectedError(f"captcha page returned for seller {seller_id}")

    soup = BeautifulSoup(html, "lxml")
    display_name = _text(soup.select_one("#seller-name, #sellerName-rd, h1#seller-name"))

    section = soup.select_one("#page-section-detail-seller-info, #seller-info-feedback-summary, #about-seller")
    business_name = vat_number = vat_country = None
    address_lines: list[str] = []
    if section is not None:
        # Solo le righe "foglia": Amazon annida .a-row dentro .a-row, e il
        # testo della riga esterna ripeterebbe quello delle interne.
        rows = [t for r in section.select(".a-row") if r.select_one(".a-row") is None and (t := _text(r))]
        in_address = False
        for row in rows:
            lowered = row.lower()
            if lowered.startswith(("nome dell'azienda", "nome commerciale", "business name")):
                business_name = row.split(":", 1)[1].strip() if ":" in row else None
                in_address = False
            elif _VAT_LABEL_RE.match(row):
                raw = row.split(":", 1)[1].strip() if ":" in row else ""
                match = _VAT_VALUE_RE.search(raw.replace(" ", ""))
                if match:
                    vat_country, vat_number = match.group(1), match.group(2)
                in_address = False
            elif lowered.startswith(("indirizzo aziendale", "business address")):
                in_address = True
                rest = row.split(":", 1)[1].strip() if ":" in row else ""
                if rest:
                    address_lines.append(rest)
            elif in_address:
                if ":" in row and not row.split(":", 1)[0].strip().isdigit():
                    in_address = False
                else:
                    address_lines.append(row)

    business_address = "\n".join(address_lines) or None
    business_country = _country_from_address(business_address) or vat_country
    return SellerProfileData(
        seller_id=seller_id,
        marketplace=marketplace,
        display_name=display_name,
        business_name=business_name or None,
        business_address=business_address,
        business_country=business_country,
        vat_number=vat_number,
        vat_country=vat_country,
    )


def extract_asins_from_listing(html: str) -> list[str]:
    """ASIN da una pagina elenco (bestseller, novità, risultati di ricerca),
    in ordine di apparizione, senza duplicati. Le posizioni sponsorizzate
    nei risultati di ricerca vengono incluse: il chiamante filtra se serve."""
    if is_captcha_page(html):
        raise CaptchaDetectedError("captcha page returned for listing")
    soup = BeautifulSoup(html, "lxml")
    seen: list[str] = []
    for node in soup.select("[data-asin]"):
        asin = node.get("data-asin")
        if isinstance(asin, str) and _ASIN_RE.match(asin) and asin not in seen:
            seen.append(asin)
    for link in soup.select("a[href]"):
        href = link.get("href")
        if not isinstance(href, str):
            continue
        match = _DP_LINK_RE.search(href)
        if match and match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def normalize_asin(raw: str) -> str | None:
    value = raw.strip()
    match = _DP_LINK_RE.search(value) if "/" in value else None
    if match:
        value = match.group(1)
    value = value.upper()
    return value if _ASIN_RE.match(value) else None
