"""
VIES (VAT Information Exchange System, Commissione Europea) — verifica di
una P.IVA UE e, quando lo Stato membro lo consente, ragione sociale e
indirizzo registrati.

Servizio pubblico e gratuito, nessuna chiave. Endpoint REST:
GET {base_url}/ms/{country}/vat/{number}. Risposta JSON con almeno
`isValid`, `name`, `address`, `userError`, `requestDate`. Quando lo Stato
non divulga i dati, `name`/`address` valgono "---": li normalizziamo a None.

Non raggiungibile dal sandbox cloud di sviluppo (proxy 403, verificato il
14/10): testato con httpx.MockTransport. La forma della risposta va
riconfermata alla prima chiamata reale dal PC di casa/server.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

DEFAULT_BASE_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api"

EU_COUNTRY_CODES = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU",
        "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI",
    }
)
_UNDISCLOSED = {"", "---", "N/A"}
_IT_CAP_CITY_RE = re.compile(r"\b(\d{5})\s+([A-ZÀ-Ü' .-]+?)(?:\s+([A-Z]{2}))?\s*$")


@dataclass(frozen=True)
class ViesResult:
    country_code: str
    vat_number: str
    is_valid: bool
    name: str | None
    address: str | None
    city: str | None
    checked_at: datetime
    error: str | None = None  # userError diverso da VALID, o errore di rete

    @property
    def definitive(self) -> bool:
        """False quando VIES non ha potuto rispondere (Stato membro non
        disponibile, timeout): il risultato va ritentato più tardi, non
        trattato come P.IVA non valida."""
        return self.error is None or self.error in ("VALID", "INVALID")


def normalize_vat(country_code: str, vat_number: str) -> tuple[str, str]:
    """('it', 'IT 012 345 678 90') -> ('IT', '01234567890'). Grecia: VIES usa EL, non GR."""
    country = country_code.strip().upper()
    if country == "GR":
        country = "EL"
    number = re.sub(r"[^0-9A-Za-z]", "", vat_number).upper()
    number = number.removeprefix(country)
    return country, number


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if text in _UNDISCLOSED else text


def _city_from_it_address(address: str | None) -> str | None:
    if not address:
        return None
    for line in reversed([ln.strip() for ln in address.splitlines() if ln.strip()]):
        match = _IT_CAP_CITY_RE.search(line.upper())
        if match:
            return match.group(2).strip().title()
    return None


class ViesClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def check(self, country_code: str, vat_number: str) -> ViesResult:
        country, number = normalize_vat(country_code, vat_number)
        now = datetime.now(UTC)
        if country not in EU_COUNTRY_CODES:
            return ViesResult(country, number, False, None, None, None, now, error="NOT_EU")

        url = f"{self._base_url}/ms/{country}/vat/{number}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ViesResult(country, number, False, None, None, None, now, error=f"REQUEST_FAILED: {exc}")

        user_error = payload.get("userError")
        is_valid = bool(payload.get("isValid"))
        name = _clean(payload.get("name"))
        address = _clean(payload.get("address"))
        return ViesResult(
            country_code=country,
            vat_number=number,
            is_valid=is_valid,
            name=name,
            address=address,
            city=_city_from_it_address(address) if country == "IT" else None,
            checked_at=now,
            error=None if user_error in (None, "VALID") else str(user_error),
        )
