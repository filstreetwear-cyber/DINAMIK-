"""Contact discovery: email/telefono dal sito confermato. Nessuna rete reale."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Brand, Company, Contact, Evidence
from app.services.contact_discovery import (
    classify_email,
    contact_page_links,
    discover_contacts,
    extract_contacts,
    normalize_phone,
    run_contact_discovery,
)
from app.tools.website.http_client import FetchedPage, WebsiteFetchError

HOME = """<html><body>
<nav><a href="/chi-siamo">Chi siamo</a> <a href="/contatti/">Contattaci</a> <a href="https://facebook.com/x">FB</a>
<a href="/prodotti">Prodotti</a></nav>
<footer>CasaBella S.r.l. - P.IVA 01234567890 - <a href="mailto:info@casabella.it">info@casabella.it</a>
<img src="logo@2x.png"> tracking: abc@sentry.wixpress.com</footer></body></html>"""
CONTACT = """<html><body><h1>Contatti</h1>
<p>Ufficio commerciale: commerciale [at] casabella [dot] it</p>
<p>Responsabile: mario.rossi@casabella.it</p>
<p>Tel. <a href="tel:+390212345678">02 1234 5678</a> - Cell. 347 123 4567 - Fax 02.1234.5679</p>
<p>PEC: casabella@pec.it - Esempio: nome@dominio.it - spam: x@altrosito.com</p>
<script>var e = "hidden@casabella.it";</script>
</body></html>"""


def _fetcher(pages: dict[str, str]):
    async def fetch(url: str) -> FetchedPage:
        if url not in pages:
            raise WebsiteFetchError(url)
        return FetchedPage(url=url, status_code=200, html=pages[url])
    return fetch


def test_classify_and_normalize():
    assert classify_email("info@casabella.it") == "role"
    assert classify_email("commerciale.italia@casabella.it") == "role"
    assert classify_email("mario.rossi@casabella.it") == "personal"
    assert classify_email("casabella@pec.it") == "pec"
    assert normalize_phone("+39 02 1234 5678") == "+39 0212345678"
    assert normalize_phone("347.123.4567") == "+39 3471234567"
    assert normalize_phone("1234") is None
    assert normalize_phone("000 000 0000") is None


def test_extract_contacts_filters_junk_and_foreign_domains():
    found = extract_contacts(CONTACT, "https://www.casabella.it/contatti/", "casabella.it")
    values = {c.value: c.category for c in found}
    assert values["commerciale@casabella.it"] == "role"
    assert values["mario.rossi@casabella.it"] == "personal"
    assert values["casabella@pec.it"] == "pec"
    assert values["+39 0212345678"] == "landline"
    assert values["+39 3471234567"] == "mobile"
    assert "nome@dominio.it" not in values and "x@altrosito.com" not in values
    assert "hidden@casabella.it" not in values  # dentro <script>: non è pubblicato per un lettore


def test_vat_and_fiscal_numbers_are_not_phones():
    html = "<p>P.IVA 01234567890 - C.F. 01234567890 - REA MI-1234567 - Capitale sociale 0100000 - Tel 02 1234 5678</p>"
    phones = [c.value for c in extract_contacts(html, "https://x.it", "x.it") if c.kind == "phone"]
    assert phones == ["+39 0212345678"]
    html2 = "<p>01234567890 | Telefono 02 1234 5678</p>"  # P.IVA senza etichetta: esclusa perché nota
    phones2 = [c.value for c in extract_contacts(html2, "https://x.it", "x.it", exclude_digits={"01234567890"})
               if c.kind == "phone"]
    assert phones2 == ["+39 0212345678"]


def test_contact_page_links_same_host_only():
    links = contact_page_links(HOME, "https://www.casabella.it")
    assert links == ["https://www.casabella.it/chi-siamo", "https://www.casabella.it/contatti/"]


async def test_discover_contacts_reads_home_and_contact_pages():
    fetch = _fetcher({
        "https://www.casabella.it": HOME,
        "https://www.casabella.it/contatti/": CONTACT,
    })
    result = await discover_contacts("https://www.casabella.it", fetch=fetch)
    values = {c.value for c in result.contacts}
    assert "info@casabella.it" in values and "commerciale@casabella.it" in values
    assert not any("sentry" in v or "2x" in v for v in values)
    assert len(result.pages_read) == 2  # /chi-siamo non esiste: saltata senza errore


async def test_unreachable_site_returns_empty():
    result = await discover_contacts("https://www.nope.it", fetch=_fetcher({}))
    assert result.contacts == [] and result.pages_read == []


@pytest.mark.usefixtures("session")
async def test_run_contact_discovery_saves_contacts_and_fills_company(session):
    company = Company(legal_name="CASABELLA SRL CD", vat_country="IT", vat_number="09999999990", country="IT")
    session.add(company)
    await session.flush()
    brand = Brand(name="CasaBellaCDTest", company_id=company.id, website="https://www.casabellacd.it")
    session.add(brand)
    await session.flush()
    fetch = _fetcher({
        "https://www.casabellacd.it": HOME.replace("casabella.it", "casabellacd.it"),
        "https://www.casabellacd.it/contatti/": CONTACT.replace("casabella", "casabellacd"),
    })
    summary = await run_contact_discovery(session, max_brands=5000, fetch=fetch)
    assert summary.contacts_created >= 4
    contacts = (await session.execute(select(Contact).where(Contact.brand_id == brand.id))).scalars().all()
    assert {c.email for c in contacts if c.email} >= {"info@casabellacd.it", "commerciale@casabellacd.it"}
    await session.refresh(company)
    assert company.email in {"info@casabellacd.it", "commerciale@casabellacd.it"}  # casella aziendale, non personale
    assert company.phone == "+39 0212345678"  # fisso preferito al cellulare
    ev = (await session.execute(select(Evidence.claim).where(Evidence.entity_id == str(brand.id)))).scalars().all()
    assert any("pubblicato sul sito" in c for c in ev)

    again = await run_contact_discovery(session, max_brands=5000, fetch=fetch)
    assert all(d["brand"] != "CasaBellaCDTest" for d in again.details)  # già fatto: non riletto
