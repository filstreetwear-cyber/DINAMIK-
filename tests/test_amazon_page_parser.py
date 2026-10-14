"""Anello 1: parser schede prodotto / profili venditore / pagine elenco (HTML sintetico, nessuna rete)."""
from __future__ import annotations

import pytest

from app.tools.amazon_scraping.page_parser import (
    CaptchaDetectedError,
    extract_asins_from_listing,
    is_captcha_page,
    normalize_asin,
    parse_product_page,
    parse_seller_page,
)
from tests.anello1_html import CAPTCHA_HTML, listing_html, product_html, seller_html


def test_product_page_with_seller_trigger_extracts_all_fields():
    page = parse_product_page(product_html(), "B0TEST0001")
    assert page.title == "Organizer cassetti cucina in bambù, set da 6 contenitori"
    assert page.brand == "CasaBella"
    assert page.seller_name == "CasaBella Srl"
    assert page.seller_id == "A1SELLERIT0001"
    assert page.seller_is_amazon is False
    assert page.rating == 4.1
    assert page.review_count == 37
    assert page.price == 24.99
    assert page.currency == "EUR"
    assert page.bsr == 1234
    assert page.bsr_category == "Casa e cucina"
    assert len(page.image_urls) == 3
    assert page.has_video is False
    assert page.has_a_plus_content is False
    assert len(page.bullet_points) == 3
    assert page.title_length == len(page.title or "")


def test_product_page_tabular_buybox_seller():
    page = parse_product_page(product_html(seller_mode="tabular", seller_id="A2TAB"), "B0TEST0002")
    assert page.seller_name == "CasaBella Srl"
    assert page.seller_id == "A2TAB"


def test_product_page_sold_by_amazon_is_marked_not_dropped():
    page = parse_product_page(product_html(seller_mode="amazon"), "B0TEST0003")
    assert page.seller_name == "Amazon"
    assert page.seller_id is None
    assert page.seller_is_amazon is True


def test_product_page_missing_fields_stay_none():
    html = "<html><body><span id='productTitle'>Solo titolo</span></body></html>"
    page = parse_product_page(html, "B0TEST0004")
    assert page.title == "Solo titolo"
    assert page.seller_name is None and page.seller_id is None
    assert page.rating is None and page.review_count is None and page.bsr is None
    assert page.image_urls == []


def test_product_page_video_and_a_plus_detected():
    page = parse_product_page(product_html(video=True, a_plus=True, images=7), "B0TEST0005")
    assert page.has_video is True
    assert page.has_a_plus_content is True
    assert len(page.image_urls) == 7


def test_captcha_page_raises_and_is_detected():
    assert is_captcha_page(CAPTCHA_HTML)
    with pytest.raises(CaptchaDetectedError):
        parse_product_page(CAPTCHA_HTML, "B0TEST0006")
    with pytest.raises(CaptchaDetectedError):
        parse_seller_page(CAPTCHA_HTML, "A1")
    with pytest.raises(CaptchaDetectedError):
        extract_asins_from_listing(CAPTCHA_HTML)


def test_seller_page_extracts_business_identity():
    profile = parse_seller_page(seller_html(), "A1SELLERIT0001")
    assert profile.display_name == "CasaBella Srl"
    assert profile.business_name == "CASABELLA S.R.L."
    assert profile.vat_country == "IT"
    assert profile.vat_number == "01234567890"
    assert profile.business_country == "IT"
    assert profile.business_address is not None
    assert profile.business_address.splitlines()[0] == "Via Roma 1"
    assert profile.business_address.splitlines()[-1] == "IT"


def test_seller_page_non_eu_seller():
    profile = parse_seller_page(
        seller_html(business_name="Shenzhen Foo Trading Co., Ltd.", vat=None,
                    address_lines=("Bao'an District", "Shenzhen", "Guangdong", "518000", "CN")),
        "A9CN",
    )
    assert profile.business_country == "CN"
    assert profile.vat_number is None


def test_seller_page_without_detail_block():
    profile = parse_seller_page("<html><body><h1 id='seller-name'>X</h1></body></html>", "A0")
    assert profile.display_name == "X"
    assert profile.business_name is None and profile.vat_number is None and profile.business_country is None


def test_listing_asins_in_order_without_duplicates():
    html = listing_html(["B0AAAAAAA1", "B0AAAAAAA2", "B0AAAAAAA1"])
    assert extract_asins_from_listing(html) == ["B0AAAAAAA1", "B0AAAAAAA2"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("B0AAAAAAA1", "B0AAAAAAA1"),
        ("  b0aaaaaaa1 ", "B0AAAAAAA1"),
        ("https://www.amazon.it/Organizer-Cucina/dp/B0AAAAAAA1/ref=sr_1_3?x=1", "B0AAAAAAA1"),
        ("https://www.amazon.it/gp/product/B0AAAAAAA1", "B0AAAAAAA1"),
        ("non-un-asin", None),
    ],
)
def test_normalize_asin(raw, expected):
    assert normalize_asin(raw) == expected
