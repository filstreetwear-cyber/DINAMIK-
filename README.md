# Dinamik Advisor — Amazon Intelligence Platform

**Stato attuale: Milestone 1+2 completate. Refactor "Helium10 Optional + Provider/Capability" — Phase A (audit), B (database), C (Capability/Provider/Registry/Orchestrator), D (Amazon/Keepa provider), E (Website: HTTP + HTML + JS/Browser fallback), F (Browser Automation Engine, Playwright, verificato reale) e H (Orchestrator: cost awareness, confidence, availability) completate. Phase G (Helium10) sospesa su tua decisione. Phase I / Milestone 5 (Amazon Scout discovery) completata — vedi sotto. Vedi `docs/architecture.md` (addendum 21/09) e `docs/refactor-audit-2026-09-21.md`.**

Piattaforma proprietaria di Dinamik Advisor per l'individuazione, analisi e
qualificazione di brand/seller Amazon con margine di crescita commerciale.
Agent 01 (Amazon Scout) è il primo modulo; l'architettura è pensata per
essere estesa con Agent 02 (Amazon Auditor), Agent 03 (Sales Copilot) e i
moduli successivi (Listing Optimizer, Ads Analyst, Competitor Monitor,
Seller Fast Intelligence, Customer Intelligence).

## Architettura

Vedi [`docs/architecture.md`](docs/architecture.md) per la proposta
approvata: stack, schema database, struttura moduli, interfacce dei tool,
credenziali necessarie e piano delle milestone.

Vedi [`docs/database.md`](docs/database.md) per lo schema applicato, le
decisioni di design (UUID, vocabolari come stringhe non enum nativi,
evidence polimorfica) e come rieseguire le migration.

## Installazione

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Configurazione

Le variabili d'ambiente (credenziali, connection string, feature flag)
sono documentate in `.env.example`. I parametri non sensibili (pesi di
scoring, retry, rate limit, marketplace supportati) sono in
`config/default.yaml`, con override per ambiente in
`config/development.yaml` e `config/production.yaml` (selezionati da
`ENVIRONMENT`).

`MOCK_MODE=true` (default) fa funzionare l'intero sistema senza alcuna
credenziale reale — i connector mock arrivano in Milestone 4.

## Stato dei connector

| Connector | Uso | Note |
|---|---|---|
| Amazon SP-API | non usato dall'Amazon Scout | account-scoped, rilevante da Agent 02 in poi |
| Keepa | discovery (core, per architettura) | nessuna credenziale confermata — provider mock finché non chiarito |
| Jungle Scout | discovery (optional) | non disponibile per Dinamik — deprioritizzato |
| Perpetua (ex-Sellics) | discovery (optional) | non disponibile per Dinamik — deprioritizzato |
| Website Intelligence | discovery (core) | nessuna credenziale terza parte richiesta — unico provider con dati reali costruibile oggi |
| **Helium 10** | discovery (optional, non più prioritario) | unico tool commerciale attivo (piano Basic, non Enterprise). **Lavoro specifico Helium10 (modalità manuale e browser automation) sospeso il 21/09** in attesa di chiarimento sui Termini di Servizio — vedi `docs/architecture.md` addendum |

Dalla revisione architetturale del 21/09 (`docs/refactor-audit-2026-09-21.md`), il sistema non dipende più da un singolo connector: l'orchestrator sceglie tra i provider disponibili per ogni "capability" richiesta. Con Helium10 sospeso, però, **nessun provider con dati reali di mercato Amazon è oggi disponibile** — il sistema resta in mock mode per quella parte finché non si sblocca almeno un provider.

## Sviluppo

```bash
make test
make lint
```

## Milestone

- [x] 1 — Repository + architecture + config
- [x] 2 — Database + migrations
- [x] 3 — Tool abstraction layer — sostituita dal refactor Phase C-F: `app/tools/core/` (Capability/Provider/Registry/Orchestrator), provider Website (reale, con fallback JS/browser)/Amazon/Keepa (mock), Browser Automation Engine (Playwright, verificato reale)
- [ ] 4 — Mock connectors — Amazon/Keepa coperti da Phase D; restano Jungle Scout/Perpetua (deprioritizzati) e Helium10 (sospeso, Phase G)
- [x] 5 — Amazon Scout discovery — `app/agents/amazon_scout/discovery.py`; produce solo ResearchResult/Evidence grezzi, nessuna risoluzione brand/dedup/scoring/AI (vedi `docs/architecture.md` addendum 21/09 (5))
- [ ] 6 — Brand resolution
- [ ] 7 — Deduplication
- [ ] 8 — Opportunity engine
- [ ] 9 — Scoring
- [ ] 10 — AI layer
- [ ] 11 — Agent orchestration
- [ ] 12 — CLI
- [ ] 13 — API
- [ ] 14 — Workers + scheduler
- [ ] 15 — Exports
- [ ] 16 — Auditor package
- [ ] 17 — Tests
- [ ] 18 — Docker
- [ ] 19 — Documentation
