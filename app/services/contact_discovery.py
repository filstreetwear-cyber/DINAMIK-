"""
Contact discovery (2026-10-25): email e telefono dell'azienda dal suo sito.

Fino a oggi il sistema trovava l'azienda (VIES) e il sito (P.IVA in home)
ma non un indirizzo a cui scrivere: la tabella `contacts` esisteva dalla
Milestone 1 e nessuno la scriveva. Questo modulo la riempie leggendo
SOLO pagine pubbliche del sito già confermato: home + al massimo poche
pagine "contatti / chi siamo / note legali" collegate dalla home.

Regole:
* Si prendono `mailto:`/`tel:` e indirizzi scritti nel testo (anche nella
  forma "info [at] dominio.it").
* Si scartano indirizzi tecnici e di terze parti (wix, sentry, esempio,
  nomi di file immagine) e quelli di domini diversi da quello del sito,
  salvo i provider di posta comuni usati dalle piccole aziende.
* Ogni indirizzo è marcato `role` (info@, vendite@, ...: casella
  aziendale) o `personal` (nome.cognome@: dato personale, GDPR). La
  scelta di chi contattare resta umana; l'interfaccia mostra la
  differenza.
* Ogni contatto ha la sua Evidence con l'URL della pagina dove compare.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, Brand, Company, Contact, ErrorLog, Evidence
from app.models.enums import AcquisitionMethod, DataType, ErrorCategory, EvidenceSourceType
from app.services.website_discovery import PageFetch
from app.tools.website.http_client import WebsiteFetchError, fetch_page

SOURCE = "website"
_MAX_EXTRA_PAGES = 3
_CONTACT_LINK_HINTS = (
    "contatt", "contact", "chi-siamo", "chisiamo", "about", "azienda", "note-legali",
    "notelegali", "impressum", "dove-siamo", "assistenza",
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\{at\}|\s+at\s+)\s*([A-Za-z0-9-]+(?:\s*(?:\[dot\]|\(dot\)|\.)\s*[A-Za-z0-9-]+)+)",
    re.IGNORECASE,
)
# Numeri italiani: fissi (0...) e mobili (3...), con o senza +39, con spazi/punti/trattini.
_PHONE_RE = re.compile(r"(?<![\d+])(?:\+39[\s./-]?|0039[\s./-]?)?(?:0\d{1,3}|3\d{2})[\s./-]?\d{2,4}[\s./-]?\d{2,4}(?:[\s./-]?\d{1,4})?(?!\d)")
_JUNK_EMAIL_PARTS = (
    "example.", "esempio.", "sentry", "wixpress", "domain.", "dominio.", "@2x", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".svg", "yourname", "nome@", "email@", "user@", "noreply", "no-reply",
)
_COMMON_PROVIDERS = frozenset({
    "gmail.com", "libero.it", "hotmail.it", "hotmail.com", "outlook.com", "outlook.it", "yahoo.it", "yahoo.com",
    "alice.it", "tiscali.it", "virgilio.it", "pec.it", "legalmail.it", "arubapec.it", "icloud.com",
})
_ROLE_LOCALS = frozenset({
    "info", "informazioni", "commerciale", "vendite", "sales", "marketing", "ordini", "orders", "shop",
    "amministrazione", "admin", "contatti", "contact", "contacts", "servizioclienti", "customer", "support",
    "assistenza", "hello", "ciao", "office", "ufficio", "segreteria", "export", "acquisti", "press", "stampa",
})


@dataclass(frozen=True)
class FoundContact:
    kind: str  # "email" | "phone"
    value: str
    category: str  # email: "role" | "personal" | "pec"; phone: "landline" | "mobile"
    page_url: str


@dataclass
class ContactDiscoveryResult:
    pages_read: list[str] = field(default_factory=list)
    contacts: list[FoundContact] = field(default_factory=list)


def _registrable(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def classify_email(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    if "pec" in domain.split(".") or domain.startswith("pec.") or domain in {"legalmail.it", "arubapec.it"}:
        return "pec"
    base = re.split(r"[._+-]", local)[0]
    return "role" if local in _ROLE_LOCALS or base in _ROLE_LOCALS else "personal"


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0039"):
        digits = digits[4:]
    elif digits.startswith("39") and len(digits) >= 11 and raw.strip().startswith("+"):
        digits = digits[2:]
    if not (6 <= len(digits) <= 11) or digits[0] not in "03":
        return None
    if len(set(digits)) <= 2:  # 0000000, 3333333: segnaposto
        return None
    return f"+39 {digits}"


def _accept_email(email: str, site_domain: str) -> bool:
    lowered = email.lower()
    if any(part in lowered for part in _JUNK_EMAIL_PARTS):
        return False
    domain = lowered.rsplit("@", 1)[-1]
    return _registrable(domain) == site_domain or domain in _COMMON_PROVIDERS


_NOT_PHONE_CONTEXT = re.compile(r"(?:iva|vat|fiscale|c\.?\s?f\.?|rea|cap|capitale|codice)\W{0,12}$", re.IGNORECASE)


def _phones_in_text(text: str, exclude_digits: set[str]) -> list[str]:
    """Numeri di telefono nel testo, scartando quelli che sono in realtà
    P.IVA, codice fiscale, REA o capitale sociale (stesse cifre, 11 digit
    che iniziano per 0: senza contesto sembrano un fisso)."""
    out: list[str] = []
    for match in _PHONE_RE.finditer(text):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in exclude_digits or digits.removeprefix("39") in exclude_digits:
            continue
        before = text[max(0, match.start() - 25) : match.start()]
        if _NOT_PHONE_CONTEXT.search(before):
            continue
        out.append(raw)
    return out


def extract_contacts(
    html: str, page_url: str, site_domain: str, *, exclude_digits: set[str] | None = None
) -> list[FoundContact]:
    soup = BeautifulSoup(html, "lxml")
    found: list[FoundContact] = []
    emails: list[str] = []
    phones: list[str] = []

    for link in soup.select("a[href]"):
        href = str(link.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            address = unquote(href[7:].split("?")[0]).strip()
            if address:
                emails.append(address)
        elif href.lower().startswith("tel:"):
            phones.append(unquote(href[4:]))

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ")
    emails += _EMAIL_RE.findall(text)
    for user, domain in _OBFUSCATED_RE.findall(text):
        clean_domain = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*", ".", domain, flags=re.IGNORECASE).replace(" ", "")
        emails.append(f"{user}@{clean_domain}")
    phones += _phones_in_text(text, exclude_digits or set())

    seen: set[str] = set()
    for email in emails:
        normalized = email.strip().strip(".,;:").lower()
        if normalized in seen or not _EMAIL_RE.fullmatch(normalized) or not _accept_email(normalized, site_domain):
            continue
        seen.add(normalized)
        found.append(FoundContact("email", normalized, classify_email(normalized), page_url))
    for raw in phones:
        number = normalize_phone(raw)
        if number is None or number in seen:
            continue
        seen.add(number)
        found.append(FoundContact("phone", number, "mobile" if number[4] == "3" else "landline", page_url))
    return found


def contact_page_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).netloc
    links: list[str] = []
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "")
        label = (a.get_text(" ") or "").lower()
        target = urljoin(base_url, href).split("#")[0]
        if urlparse(target).netloc != host or target.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = f"{target.lower()} {label}"
        if any(hint in haystack for hint in _CONTACT_LINK_HINTS) and target not in links:
            links.append(target)
    return links[:_MAX_EXTRA_PAGES]


async def discover_contacts(
    website: str, *, fetch: PageFetch = fetch_page, exclude_digits: set[str] | None = None
) -> ContactDiscoveryResult:
    result = ContactDiscoveryResult()
    exclude = exclude_digits or set()
    try:
        home = await fetch(website)
    except WebsiteFetchError:
        return result
    site_domain = _registrable(urlparse(home.url).netloc)
    result.pages_read.append(home.url)
    result.contacts.extend(extract_contacts(home.html, home.url, site_domain, exclude_digits=exclude))
    for url in contact_page_links(home.html, home.url):
        try:
            page = await fetch(url)
        except WebsiteFetchError:
            continue
        result.pages_read.append(page.url)
        result.contacts.extend(extract_contacts(page.html, page.url, site_domain, exclude_digits=exclude))
    # Dedup tra pagine, tenendo la prima pagina in cui compare.
    unique: dict[tuple[str, str], FoundContact] = {}
    for c in result.contacts:
        unique.setdefault((c.kind, c.value), c)
    result.contacts = list(unique.values())
    return result


async def save_contacts(
    session: AsyncSession, brand: Brand, found: list[FoundContact], *, observed_at: datetime | None = None
) -> int:
    """Crea le righe Contact mancanti (idempotente per valore) e riempie
    Company.email/phone se vuoti, preferendo caselle aziendali."""
    observed_at = observed_at or datetime.now(UTC)
    existing = (
        await session.execute(select(Contact).where(Contact.brand_id == brand.id, Contact.source == SOURCE))
    ).scalars().all()
    known = {c.email for c in existing if c.email} | {c.phone for c in existing if c.phone}
    created = 0
    for c in found:
        if c.value in known:
            continue
        contact = Contact(
            company_id=brand.company_id,
            brand_id=brand.id,
            email=c.value if c.kind == "email" else None,
            phone=c.value if c.kind == "phone" else None,
            role=c.category,
            source=SOURCE,
            verified=False,
        )
        session.add(contact)
        await session.flush()
        known.add(c.value)
        created += 1
        session.add(
            Evidence(
                entity_type="brand",
                entity_id=str(brand.id),
                claim=f"{'Email' if c.kind == 'email' else 'Telefono'} ({c.category}) pubblicato sul sito: {c.value}",
                data_type=DataType.FACT.value,
                source=f"contact_discovery:{c.page_url}"[:255],
                source_url=c.page_url[:500],
                source_type=EvidenceSourceType.WEBSITE.value,
                raw_value=c.value,
                confidence=90.0,
                observed_at=observed_at,
                provider="contact_discovery",
                acquisition_method=AcquisitionMethod.HTTP.value,
            )
        )
    if brand.company_id:
        company = await session.get(Company, brand.company_id)
        if company is not None:
            emails = [c for c in found if c.kind == "email" and c.category != "pec"]
            emails.sort(key=lambda c: 0 if c.category == "role" else 1)
            if company.email is None and emails:
                company.email = emails[0].value
            phones = [c for c in found if c.kind == "phone"]
            phones.sort(key=lambda c: 0 if c.category == "landline" else 1)
            if company.phone is None and phones:
                company.phone = phones[0].value
    await session.flush()
    return created


@dataclass
class ContactDiscoverySummary:
    agent_run_id: uuid.UUID
    brands_considered: int = 0
    brands_with_contacts: int = 0
    contacts_created: int = 0
    details: list[dict[str, str]] = field(default_factory=list)


async def run_contact_discovery(
    session: AsyncSession, *, max_brands: int = 50, fetch: PageFetch = fetch_page
) -> ContactDiscoverySummary:
    """Brand ACTIVE con sito confermato e ancora senza contatti dal sito."""
    agent_run = AgentRun(agent_name="contact_discovery", input={"max_brands": max_brands}, status="RUNNING")
    session.add(agent_run)
    await session.flush()
    summary = ContactDiscoverySummary(agent_run_id=agent_run.id)
    try:
        has_contacts = select(Contact.id).where(Contact.brand_id == Brand.id, Contact.source == SOURCE).exists()
        brands = (
            await session.execute(
                select(Brand)
                .where(Brand.status == "ACTIVE", Brand.website.is_not(None), ~has_contacts)
                .order_by(Brand.first_seen_at)
                .limit(max_brands)
            )
        ).scalars().all()
        for brand in brands:
            summary.brands_considered += 1
            assert brand.website is not None
            company = await session.get(Company, brand.company_id) if brand.company_id else None
            exclude = {company.vat_number} if company and company.vat_number else set()
            result = await discover_contacts(brand.website, fetch=fetch, exclude_digits=exclude)
            created = await save_contacts(session, brand, result.contacts)
            summary.contacts_created += created
            if result.contacts:
                summary.brands_with_contacts += 1
            summary.details.append({
                "brand": brand.name,
                "pages": str(len(result.pages_read)),
                "contacts": ", ".join(c.value for c in result.contacts) or "nessuno",
            })
        agent_run.status = "SUCCESS"
        agent_run.output = {
            "brands_considered": summary.brands_considered,
            "brands_with_contacts": summary.brands_with_contacts,
            "contacts_created": summary.contacts_created,
        }
        await session.flush()
    except Exception as exc:  # noqa: BLE001 -- stessa disciplina agent-run degli altri motori
        agent_run.status = "FAILED"
        agent_run.error = str(exc)
        session.add(ErrorLog(category=ErrorCategory.UNKNOWN.value, message=str(exc), agent_run_id=agent_run.id))
        await session.flush()
    return summary
