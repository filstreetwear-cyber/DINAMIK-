"""
Quali Evidence appartengono a un brand (Anello 1, 2026-10-14).

Prima dello scout live l'unica fonte era la discovery mock, che scrive
Evidence su entity_type="research_result". Lo scout scrive invece su
"product", "seller", "company" e "brand". Scoring e motori AI devono
vedere entrambe le catene: questo modulo è l'unico posto che le elenca.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AmazonProduct, AmazonSeller, Brand, Evidence, ResearchResult


async def brand_evidence_condition(session: AsyncSession, brand_id: uuid.UUID) -> ColumnElement[bool]:
    research_ids = (
        (await session.execute(select(ResearchResult.id).where(ResearchResult.brand_id == brand_id))).scalars().all()
    )
    product_ids = (
        (await session.execute(select(AmazonProduct.id).where(AmazonProduct.brand_id == brand_id))).scalars().all()
    )
    sellers = (
        await session.execute(select(AmazonSeller.id, AmazonSeller.company_id).where(AmazonSeller.brand_id == brand_id))
    ).all()
    brand_company = (await session.execute(select(Brand.company_id).where(Brand.id == brand_id))).scalar_one_or_none()
    company_ids = {str(c) for _, c in sellers if c} | ({str(brand_company)} if brand_company else set())

    clauses: list[ColumnElement[bool]] = [and_(Evidence.entity_type == "brand", Evidence.entity_id == str(brand_id))]
    for entity_type, ids in (
        ("research_result", [str(i) for i in research_ids]),
        ("product", [str(i) for i in product_ids]),
        ("seller", [str(s) for s, _ in sellers]),
        ("company", sorted(company_ids)),
    ):
        if ids:
            clauses.append(and_(Evidence.entity_type == entity_type, Evidence.entity_id.in_(ids)))
    return or_(*clauses) if clauses else false()
