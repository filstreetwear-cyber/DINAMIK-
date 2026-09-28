"""
ContactDraft — Fase 6 (roadmap Fil/Frank, 28/09): la bozza di primo
contatto scritta da AIOutreachWriter (app/ai/outreach_writer.py), non un
messaggio pronto per l'invio. Nessuna integrazione di invio email esiste
in questo progetto (fuori scope, budget zero, e comunque un invio
automatico contraddirebbe l'intero impianto "il final say resta umano"
già applicato ad AIAnalyst e AIAdvisor) — questa riga è letta, modificata
ed eventualmente inviata da un umano del team Dinamik.

**Finalmente un writer reale per `ProspectStatus`** (`app/models/contact.py`):
quel modello esiste da Milestone 6 ma nessun servizio lo ha mai popolato
(`app/main.py`'s own scope note lo dichiara esplicitamente). Generare una
bozza è il momento naturale per scrivere `ProspectStatus(status=NEEDS_REVIEW,
changed_by="system")` — il gate "nothing reaches Outreach without an
explicit status transition" che `contact.py` descrive dal primo giorno,
finalmente collegato a qualcosa (`app/services/contact_draft_engine.py`),
invece di aggiungere un TERZO concetto di stato parallelo a `Opportunity`/
`AIRecommendation`.

Append-only (`CreatedAtMixin`, non `TimestampMixin`) — stesso principio di
`AIClaim`/`AIRecommendation`: una bozza non viene mai modificata in
place da questo sistema (un umano che la modifica lo fa fuori, nella
propria email), una rigenerazione produce una nuova riga.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ContactDraft(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "contact_drafts"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(4000))
    recommendation_ids: Mapped[list] = mapped_column(JSONB)  # list[str(uuid)] — le AIRecommendation sintetizzate qui
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
