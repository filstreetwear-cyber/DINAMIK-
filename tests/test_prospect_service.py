"""Prospect service: lista, dettaglio, stati, bozze, AI on demand. Postgres reale o skip."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.ai.gateway import AIGateway
from app.ai.mock_provider import MockAIProvider
from app.models import AmazonSeller, ContactDraft, ProspectStatus
from app.services.opportunity_engine import run_opportunity_engine
from app.services.prospect_service import (
    ProspectNotFoundError,
    dashboard_counts,
    generate_ai_for_brand,
    get_prospect,
    list_prospects,
    save_draft_edit,
    set_status,
)
from app.services.scoring_engine import run_scoring_engine
from app.services.scout_runner import run_scout
from tests.test_scout_runner import FakeFetcher, _pages, _vies


async def _seed(session, tmp_path):
    vies = _vies({"01234567890": {"name": "CASABELLA SRL", "address": "VIA ROMA 1\n20121 MILANO MI"}})
    await run_scout(
        session, fetcher=FakeFetcher(_pages(), tmp_path / "ev"), vies=vies,
        asins=["B0ITSELL01", "B0ITSELL02", "B0CNSELL01"], name="seed",
    )
    await run_opportunity_engine(session, max_brands=5000)
    await run_scoring_engine(session, max_brands=5000)
    seller = (await session.execute(select(AmazonSeller).where(AmazonSeller.seller_id == "A1ITSELLER"))).scalar_one()
    return seller.brand_id


async def test_list_and_detail(session, tmp_path):
    brand_id = await _seed(session, tmp_path)

    rows = await list_prospects(session, search="CasaBella", limit=50)
    row = next(r for r in rows if r.brand_id == brand_id)
    assert row.company_name == "CASABELLA SRL"
    assert row.vat == "IT01234567890"
    assert row.is_target is True
    assert row.status == "NEW" and row.status_label == "Da valutare"
    assert row.listing_level == "HIGH"
    assert row.product_count == 2
    assert row.score is not None

    # Il brand cinese non è target: escluso dalla lista di default.
    all_targets = await list_prospects(session, search="FooHome")
    assert all_targets == []
    assert await list_prospects(session, search="FooHome", target_only=False)

    detail = await get_prospect(session, brand_id)
    assert detail.company is not None and detail.company.city == "Milano"
    assert len(detail.products) == 2
    weak = next(p for p in detail.products if p.product.asin == "B0ITSELL01")
    assert "nessun_video" in weak.issues and weak.screenshot_id is not None
    assert "LISTING_OPPORTUNITY" in detail.opportunities
    assert detail.evidence, "le evidenze dello scout devono comparire nel dettaglio"
    assert detail.overall_score is not None


async def test_status_transitions_and_draft_edits(session, tmp_path):
    brand_id = await _seed(session, tmp_path)
    await set_status(session, brand_id, "APPROVED", reason="buon fit", changed_by="frank")
    detail = await get_prospect(session, brand_id)
    assert detail.status == "APPROVED" and detail.status_history[0].changed_by == "frank"
    assert [r.brand_id for r in await list_prospects(session, status="APPROVED", search="CasaBella")] == [brand_id]

    with pytest.raises(ValueError):
        await set_status(session, brand_id, "BOGUS", reason=None, changed_by="x")

    draft = await save_draft_edit(session, brand_id, subject=" Oggetto ", body="Testo", changed_by="frank")
    assert draft.provider == "human" and draft.subject == "Oggetto"

    counts = await dashboard_counts(session)
    assert counts["approved"] >= 1 and counts["targets"] >= 1


async def test_generate_ai_on_demand_with_mock_model(session, tmp_path):
    brand_id = await _seed(session, tmp_path)
    response = json.dumps({
        "recommendations": [{
            "recommendation": "Aggiungere un video e portare la galleria ad almeno 6 immagini.",
            "reasoning_summary": "La scheda ha 3 immagini e nessun video.",
            "evidence_ids": [],
        }],
        "subject": "Le sue schede su Amazon",
        "body": "Buongiorno, abbiamo notato alcune opportunità sulle sue schede.",
    })
    gateway = AIGateway(MockAIProvider(structured_response=response))
    outcome = await generate_ai_for_brand(session, brand_id, gateway=gateway)
    assert outcome.ai_disabled is False
    assert outcome.recommendations_created >= 1
    assert outcome.draft_id is not None
    draft = await session.get(ContactDraft, outcome.draft_id)
    assert draft is not None and draft.subject == "Le sue schede su Amazon"
    statuses = (await session.execute(select(ProspectStatus).where(ProspectStatus.brand_id == brand_id))).scalars().all()
    assert statuses, "la bozza generata deve mettere il prospect in revisione"


async def test_unknown_brand(session):
    import uuid

    with pytest.raises(ProspectNotFoundError):
        await get_prospect(session, uuid.uuid4())
