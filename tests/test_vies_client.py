"""Anello 1: client VIES con httpx.MockTransport (VIES non raggiungibile dal sandbox)."""
from __future__ import annotations

import httpx

from app.tools.vies.client import ViesClient, normalize_vat


def _client(handler) -> ViesClient:
    return ViesClient(base_url="https://vies.test/rest-api", transport=httpx.MockTransport(handler))


async def test_valid_vat_with_name_and_address():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={
            "isValid": True, "userError": "VALID", "name": "CASABELLA S.R.L.",
            "address": "VIA ROMA 1 \n20121 MILANO MI\n", "requestDate": "2026-10-14T10:00:00Z",
        })

    result = await _client(handler).check("it", "IT 012 345 678 90")
    assert seen["url"] == "https://vies.test/rest-api/ms/IT/vat/01234567890"
    assert result.is_valid is True
    assert result.name == "CASABELLA S.R.L."
    assert result.city == "Milano"
    assert result.error is None
    assert result.definitive is True


async def test_undisclosed_name_becomes_none():
    def handler(request):
        return httpx.Response(200, json={"isValid": True, "userError": "VALID", "name": "---", "address": "---"})

    result = await _client(handler).check("DE", "123456789")
    assert result.is_valid is True
    assert result.name is None and result.address is None


async def test_member_state_unavailable_is_not_definitive():
    def handler(request):
        return httpx.Response(200, json={"isValid": False, "userError": "MS_UNAVAILABLE"})

    result = await _client(handler).check("IT", "01234567890")
    assert result.is_valid is False
    assert result.definitive is False


async def test_network_error_is_not_definitive():
    def handler(request):
        raise httpx.ConnectError("boom")

    result = await _client(handler).check("IT", "01234567890")
    assert result.definitive is False
    assert result.error is not None and result.error.startswith("REQUEST_FAILED")


async def test_non_eu_country_is_not_queried():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("VIES must not be called for non-EU countries")

    result = await _client(handler).check("CN", "91440300MA5")
    assert result.error == "NOT_EU"
    assert result.is_valid is False


def test_normalize_vat_handles_greece_and_prefix():
    assert normalize_vat("gr", "EL094259216") == ("EL", "094259216")
    assert normalize_vat("IT", "it-01234567890") == ("IT", "01234567890")
