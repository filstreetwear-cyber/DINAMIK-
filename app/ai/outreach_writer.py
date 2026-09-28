"""
AIOutreachWriter — Fase 6 (roadmap Fil/Frank, 28/09): il TERZO (e ultimo,
per come questo progetto è specificato oggi) ruolo AI, distinto sia da
AIAnalyst (app/ai/analyst.py, "cosa è vero") che da AIAdvisor
(app/ai/advisor.py, "cosa fare e come"). Questo scrive la bozza del primo
contatto con il venditore — esattamente il pezzo che AIAdvisor rifiuta
esplicitamente di fare (regola 5 del suo system prompt: "never draft an
outreach message... that is a separate step a human handles").

Non è un'estensione o un aggiramento di quel divieto: è la ragione per
cui esiste un modulo apposta, con le SUE proprie regole, più strette
perché il testo qui uscirà (dopo revisione umana) davanti a un vero
venditore, non resterà interno come una raccomandazione tecnica:

1. Non promette MAI un prezzo, uno sconto, una scadenza, un contratto o
   un risultato garantito — Dinamik non ha dato a questo modulo alcun
   listino/offerta da citare, e inventarne uno sarebbe un impegno
   commerciale reale preso da un modello, non un errore recuperabile
   dopo l'invio.
2. Non finge MAI di aver già lavorato per il venditore, di essere già in
   contatto, o di conoscere fatti su di loro oltre alle raccomandazioni
   fornite — quelle sono le UNICHE cose che può citare, parafrasate, mai
   nuove diagnosi.
3. Il testo prodotto è dichiaratamente una BOZZA per la revisione umana,
   non un messaggio pronto per l'invio automatico — questo sistema non
   ha (e non costruisce qui) alcuna integrazione di invio email reale;
   `ContactDraft` (app/models/contact_draft.py) è un record che un umano
   legge, modifica ed eventualmente invia lui stesso.
4. Breve, concreto, in italiano (il pubblico sono venditori Amazon.it) —
   non un testo di marketing generico, ogni frase deve derivare da una
   raccomandazione reale fornita.

Stesso shape architetturale (DB-free input, retry-on-parse-failure,
status uniforme) di AIAnalyst/AIAdvisor per lo stesso motivo: testabile
senza sessione, comportamento prevedibile per chi già conosce gli altri
due moduli.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from app.ai.contracts import ContactDraftBody
from app.ai.gateway import AIGateway
from app.ai.provider import (
    AIGenerationRequest,
    AIModelUnavailableError,
    AIProviderError,
    AITimeoutError,
    AIUnavailableError,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the DINAMIK AIOutreachWriter. Dinamik Advisor e' \
un'agenzia di account management per venditori Amazon con sede a Firenze. \
Scrivi la BOZZA di una prima email a un venditore Amazon.it, che un membro \
del team di Dinamik rilegge, modifica ed eventualmente invia -- non la \
invii tu, non e' un messaggio finale.

Regole che non devi mai violare:
1. Usa SOLO le raccomandazioni fornite qui sotto per dire cosa Dinamik ha \
notato. Non inventare nuovi problemi, dati, numeri o fatti sul venditore \
non presenti nelle raccomandazioni fornite.
2. Non promettere MAI un prezzo, uno sconto, una scadenza, un contratto o \
un risultato garantito -- non ti e' stato dato alcun listino da citare.
3. Non affermare MAI di aver gia' lavorato per questo venditore, di essere \
gia' in contatto con loro, o di conoscere fatti su di loro oltre alle \
raccomandazioni fornite.
4. Tono professionale, breve (max 150 parole nel corpo), concreto -- cita \
1-2 osservazioni specifiche dalle raccomandazioni fornite, non generiche.
5. Chiudi con un invito a una breve conversazione, non con una richiesta \
di acquisto o un link di pagamento.
6. Scrivi in italiano.

Rispondi solo con JSON conforme allo schema richiesto."""

_CONTACT_DRAFT_SCHEMA = ContactDraftBody.model_json_schema()

_MAX_GENERATE_ATTEMPTS = 2


@dataclass(frozen=True)
class RecommendationInput:
    """Vista DB-free di UNA riga AIRecommendation reale gia' generata
    (Fase 5) -- questo modulo non diagnostica nulla, riusa solo testo gia'
    prodotto e vagliato da AIAdvisor."""

    recommendation: str
    opportunity_type: str
    priority: str


@dataclass
class AIOutreachWriterOutcome:
    status: str  # stessi valori di AIAnalystOutcome/AIAdvisorOutcome.status, + INSUFFICIENT_RECOMMENDATIONS
    subject: str | None = None
    body: str | None = None
    # Provenienza stampata dal vero AIGenerationResult (result.model/result.provider),
    # mai autoriportata dal modello -- stessa garanzia di AIClaim.from_draft/
    # AIRecommendation.from_draft. None quando status != "OK".
    model: str | None = None
    provider: str | None = None
    generated_at: datetime | None = None
    error_message: str | None = None


def _build_prompt(brand_name: str, recommendations: list[RecommendationInput]) -> str:
    lines = [f"Venditore/brand: {brand_name}", "\nOsservazioni disponibili (Dinamik le ha gia' verificate):"]
    for rec in recommendations:
        lines.append(f"- [{rec.opportunity_type}, priorita' {rec.priority}] {rec.recommendation}")
    lines.append(
        "\nScrivi una bozza email come JSON: "
        '{"subject": ..., "body": ...}. Usa solo le osservazioni sopra.'
    )
    return "\n".join(lines)


def _status_for_error(exc: AIProviderError) -> str:
    if isinstance(exc, AITimeoutError):
        return "AI_TIMEOUT"
    if isinstance(exc, AIModelUnavailableError):
        return "MODEL_UNAVAILABLE"
    if isinstance(exc, AIUnavailableError):
        return "AI_UNAVAILABLE"
    return "AI_UNAVAILABLE"


class AIOutreachWriter:
    def __init__(self, gateway: AIGateway) -> None:
        self._gateway = gateway

    async def draft(self, brand_name: str, recommendations: list[RecommendationInput]) -> AIOutreachWriterOutcome:
        if not recommendations:
            # Stessa disciplina di AIAnalyst.analyze([]): niente da dire, niente
            # chiamata AI sprecata per scoprirlo -- una bozza senza alcuna
            # osservazione reale sarebbe generica per costruzione, l'esatto
            # difetto la regola 4 del system prompt vieta.
            return AIOutreachWriterOutcome(
                status="INSUFFICIENT_RECOMMENDATIONS", error_message="no recommendations supplied"
            )

        prompt = _build_prompt(brand_name, recommendations)

        last_error: Exception | None = None
        for attempt in range(1, _MAX_GENERATE_ATTEMPTS + 1):
            try:
                result = await self._gateway.structured_generate(
                    AIGenerationRequest(prompt=prompt, system_prompt=_SYSTEM_PROMPT, schema=_CONTACT_DRAFT_SCHEMA)
                )
            except AIProviderError as exc:
                return AIOutreachWriterOutcome(status=_status_for_error(exc), error_message=str(exc))

            try:
                payload = json.loads(result.text)
                body_draft = ContactDraftBody.model_validate(payload)
            except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
                last_error = exc
                logger.warning("ai_outreach_structured_output_invalid attempt=%d error=%s", attempt, exc)
                continue

            return AIOutreachWriterOutcome(
                status="OK",
                subject=body_draft.subject,
                body=body_draft.body,
                model=result.model,
                provider=result.provider,
                generated_at=datetime.now(UTC),
            )

        return AIOutreachWriterOutcome(status="VALIDATION_ERROR", error_message=str(last_error))
