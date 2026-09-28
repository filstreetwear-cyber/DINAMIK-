"""
AIRecommendation — Fase 5 (roadmap Fil/Frank, 28/09): "consigliare come e
dove intervenire" — la parte del progetto rimasta non costruita finché
`ai_claims`/`AIAnalyst` producevano solo interpretazioni di evidence
("cosa è vero"), mai un consiglio operativo ("cosa fare").

PERCHE' UNA TABELLA/CONTRATTO NUOVI, non un campo aggiunto ad `AIClaim`:
`app/ai/analyst.py` vieta esplicitamente all'AIAnalyst di produrre
raccomandazioni ("a human advisor does that", regola 6 del suo system
prompt) — una scelta deliberata contro l'allucinazione, non un
dimenticanza da estendere. Riusare `AIClaim` per un consiglio avrebbe
indebolito quella barriera invece di aggiungerne una nuova con le sue
proprie garanzie. `AIRecommendation` ha un ruolo AI diverso
(`AIAdvisor`, `app/ai/advisor.py`) con un proprio system prompt e le
proprie regole — stessa disciplina "due classi, non una, perché il
confine deve essere imponibile, non solo documentato" che
`app/ai/contracts.py` già applica ad `AIClaimDraft` vs `AIClaim`.

GARANZIA STRUTTURALE PIU' FORTE di AIClaim: ogni AIRecommendation è
ancorata a UN `Opportunity` reale e già deciso deterministicamente
(`opportunity_id`, FK) — `rule_triggered`/`reasons` di quella riga non
sono qualcosa che il modello diagnostica da solo, gli vengono dati come
fatti nel prompt (vedi advisor.py). L'AI decide solo IL TESTO del
consiglio e QUALI evidence_ids lo sostengono (stessa validazione
post-hoc di AIAnalyst: mai un id inventato), non COSA sia il problema.
`priority` è preso da `Opportunity.level` (HIGH/MEDIUM — mai generato
dal modello), non testo libero del modello.

Stesso pattern append-only/polimorfico/JSONB di `AIClaim`
(`app/models/ai_claim.py`) — vedi quel modulo per il ragionamento
completo, non ripetuto qui.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AIRecommendation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ai_recommendations"

    entity_type: Mapped[str] = mapped_column(String(50), index=True)  # "brand" only, questa fase
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    opportunity_type: Mapped[str] = mapped_column(String(50))  # stampato dalla riga Opportunity reale, mai dal modello
    priority: Mapped[str] = mapped_column(String(20))  # OpportunityLevel.value della Opportunity reale (HIGH/MEDIUM)
    recommendation: Mapped[str] = mapped_column(String(1000))  # il "cosa fare e come", scritto dal modello
    reasoning_summary: Mapped[str] = mapped_column(String(2000))
    evidence_ids: Mapped[list] = mapped_column(JSONB)  # list[str(uuid)] — sottoinsieme validato di Evidence reali
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
