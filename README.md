# Dinamik Advisor — Amazon Intelligence Platform

**Stato attuale: Milestone 1 completata — repository, architettura, configurazione.**

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
| Keepa | discovery | non disponibile per Dinamik — deprioritizzato |
| Jungle Scout | discovery | non disponibile per Dinamik — deprioritizzato |
| Perpetua (ex-Sellics) | discovery | non disponibile per Dinamik — deprioritizzato |
| **Helium 10** | discovery | **confermato come unico tool disponibile (21/09) — connector prioritario.** Resta da verificare se il piano include l'API Enterprise o solo l'accesso da dashboard |

## Sviluppo

```bash
make test
make lint
```

## Milestone

- [x] 1 — Repository + architecture + config
- [ ] 2 — Database + migrations
- [ ] 3 — Tool abstraction layer
- [ ] 4 — Mock connectors
- [ ] 5 — Amazon Scout discovery
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
