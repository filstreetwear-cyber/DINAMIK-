"""
Data Contracts (app/schemas/contracts.py) — Milestone 8.5.

Populated contracts (AmazonProduct/AmazonListing/WebsiteData) get real
from_orm() round-trip tests. The two NOT_IMPLEMENTED/NOT_FORMALLY_SPECIFIED
contracts (AdvertisingData, SellerFastFit) get a different kind of test:
proving no factory/fabrication path exists for them, not that they "work".
"""
from __future__ import annotations

from datetime import UTC, datetime

import pydantic
import pytest
from pydantic import ValidationError

from app.models.amazon import AmazonListing, AmazonProduct
from app.models.website import WebsiteProfile
from app.schemas.contracts import (
    AdvertisingDataContract,
    AmazonListingContract,
    AmazonProductContract,
    SellerFastFitContract,
    WebsiteDataContract,
)


def test_amazon_product_contract_from_orm_round_trip():
    product = AmazonProduct(
        asin="B0TEST0001",
        marketplace="IT",
        title="Test Product",
        category="Casa e cucina",
        subcategory="Pentole",
        price=19.99,
        currency="EUR",
        rating=4.5,
        review_count=120,
        bsr=1500,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )

    contract = AmazonProductContract.from_orm_product(product)

    assert contract.asin == "B0TEST0001"
    assert contract.marketplace == "IT"
    assert contract.price == pytest.approx(19.99)
    assert contract.rating == pytest.approx(4.5)
    assert contract.review_count == 120


def test_amazon_product_contract_handles_missing_optional_fields():
    product = AmazonProduct(
        asin="B0TEST0002",
        marketplace="IT",
        title="Minimal Product",
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )

    contract = AmazonProductContract.from_orm_product(product)

    assert contract.price is None
    assert contract.rating is None
    assert contract.review_count is None


def test_amazon_product_contract_is_frozen():
    product = AmazonProduct(
        asin="B0TEST0003", marketplace="IT", title="X",
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
    )
    contract = AmazonProductContract.from_orm_product(product)

    with pytest.raises(ValidationError):
        contract.title = "changed"


def test_amazon_listing_contract_from_orm_round_trip():
    listing = AmazonListing(
        bullet_points=["Bullet 1", "Bullet 2"],
        description="A description",
        has_a_plus_content=True,
        image_count=6,
        video_count=1,
        sponsored_seen=True,
        observed_at=datetime.now(UTC),
    )

    contract = AmazonListingContract.from_orm_listing(listing, product_asin="B0TEST0001")

    assert contract.product_asin == "B0TEST0001"
    assert contract.bullet_points == ["Bullet 1", "Bullet 2"]
    assert contract.has_a_plus_content is True
    assert contract.image_count == 6


def test_website_data_contract_from_orm_round_trip():
    profile = WebsiteProfile(
        domain="example-brand.it",
        technology="SHOPIFY",
        has_chatbot=True,
        mobile_friendly=True,
        page_speed_score=85,
        seo_score=70,
    )

    contract = WebsiteDataContract.from_orm_profile(profile)

    assert contract.domain == "example-brand.it"
    assert contract.technology == "SHOPIFY"
    assert contract.has_chatbot is True
    assert contract.page_speed_score == 85


# --- Contracts with no data source: shape only, no fabrication --------------


def test_advertising_data_contract_is_marked_not_implemented():
    contract = AdvertisingDataContract(asin="B0TEST0001", observed_at=datetime.now(UTC))
    assert contract.STATUS == "NOT_IMPLEMENTED"


def test_advertising_data_contract_has_no_from_orm_or_from_provider_factory():
    """The real assertion: nothing manufactures one of these from a
    provider/ORM row, because no provider produces this data. A factory
    appearing here later without a real PPC_DATA-producing provider behind
    it would be exactly the fabricated-completeness failure Milestone 8.5
    forbids. `from_orm` itself is excluded — it's pydantic BaseModel's own
    (deprecated) inherited method, present on every model including the
    populated ones above, not something this file defines."""
    inherited = {name for name in dir(pydantic.BaseModel) if name.startswith("from_")}
    factory_names = {
        name for name in dir(AdvertisingDataContract) if name.startswith("from_")
    } - inherited
    assert factory_names == set()


def test_seller_fast_fit_contract_is_marked_not_formally_specified():
    contract = SellerFastFitContract(brand_id="00000000-0000-0000-0000-000000000000", observed_at=datetime.now(UTC))
    assert contract.STATUS == "NOT_FORMALLY_SPECIFIED"


def test_seller_fast_fit_contract_has_no_factory():
    inherited = {name for name in dir(pydantic.BaseModel) if name.startswith("from_")}
    factory_names = {
        name for name in dir(SellerFastFitContract) if name.startswith("from_")
    } - inherited
    assert factory_names == set()
