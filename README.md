# Dinamik Advisor — Amazon Intelligence Platform

**Stato attuale: refactor "Helium10 Optional + Provider/Capability" Phase A→K completato, più Milestone 6 (Brand resolution), 7 (Deduplication), 8 (Opportunity engine, solo 2 dei 6 tipi), 8.5 (Audit + Agent Registry + Data Contracts + valutazione reale Playwright MCP/Browser Use/SWE-agent/Deep Agents/Agno — vedi `docs/architecture/`), 9 (Scoring engine, consuma le Opportunity di Milestone 8) e 9.5 (Local AI Infrastructure — Ollama + Qwen3 1.7B + AI Gateway/Router/Analyst, costo zero obbligatorio; verifica reale contro Ollama non eseguibile in questo sandbox — vedi `docs/architecture/local-ai.md`) e 10 (AI layer, wiring reale: `AIAnalyst` chiamato su Evidence reali, `AIClaim` persistite; ambito dedotto dai riferimenti nel codice, non da una spec di Fil — vedi `docs/architecture.md` addendum (15)) della sequenza normale del documento di refactor. Phase G (Helium10) sospesa su tua decisione. Build/run reali delle immagini Docker non verificabili in questo sandbox (nessun demone Docker) — vedi `docs/deployment.md`. Documentazione per argomento in `docs/`: [`tools.md`](docs/tools.md), [`providers.md`](docs/providers.md), [`browser.md`](docs/browser.md), [`deployment.md`](docs/deployment.md), [`database.md`](docs/database.md); `docs/architecture.md` è il registro cronologico delle decisioni, `docs/architecture/` i riferimenti di Milestone 8.5/9.5.**

Piattaforma proprietaria di Dinamik Advisor per l'individuazione, analisi e
qualificazione di brand/seller Amazon con margine di crescita commerciale.
Agent 01 (Amazon Scout) è il primo modulo; l'architettura è pensata per
essere estesa con Agent 02 (Amazon Auditor), Agent 03 (Sales Copilot) e i
moduli successivi (Listing Optimizer, Ads Analyst, Competitor Monitor,
Seller Fast Intelligence, Customer Intelligence).

## Documentazione

Organizzata per argomento (Phase K), non per fase temporale — ogni
documento risponde a "come funziona oggi", `architecture.md` risponde a
"perché è stato costruito così":

- [`docs/tools.md`](docs/tools.md) — layer Capability/Provider/Registry/Orchestrator: come routing e fallback decidono chi risponde a una richiesta di dati
- [`docs/providers.md`](docs/providers.md) — stato di ogni provider concreto (Amazon/Keepa mock, Website reale, Jungle Scout/Perpetua non costruiti, Helium10 sospeso)
- [`docs/browser.md`](docs/browser.md) — Browser Automation Engine, mock vs reale, il container browser-worker
- [`docs/deployment.md`](docs/deployment.md) — Docker/VPS: servizi, volumi, come fare il deploy, limiti verificati vs da verificare sulla VPS
- [`docs/database.md`](docs/database.md) — schema applicato, decisioni di design (UUID, vocabolari come stringhe non enum nativi, evidence polimorfica), come rieseguire le migration
- [`docs/architecture.md`](docs/architecture.md) — registro cronologico: la proposta architetturale originale approvata + un addendum per ogni fase del refactor

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
credenziale reale — i provider mock (Amazon, Keepa) sono già costruiti
(Phase D); restano da fare solo Jungle Scout/Perpetua/Helium10 (vedi
"Stato dei connector" sotto).

## Stato dei connector

Riepilogo rapido — dettaglio completo (fixture usate, capability supportate,
cosa serve per renderli reali) in [`docs/providers.md`](docs/providers.md).

| Connector | Uso | Stato |
|---|---|---|
| Amazon SP-API | non usato dall'Amazon Scout | account-scoped, rilevante da Agent 02 in poi |
| `amazon` / `keepa` (provider interni) | discovery | mock (fixture), fallback reciproco reale tramite l'orchestrator |
| Website Intelligence | discovery | **reale** — nessuna credenziale terza parte richiesta |
| Jungle Scout / Perpetua | discovery (optional) | non costruiti, deprioritizzati |
| **Helium 10** | discovery (optional) | unico tool commerciale attivo per Dinamik — **sospeso il 21/09**, ToS da chiarire |

Il sistema non dipende da un singolo connector: l'orchestrator sceglie tra i
provider disponibili per ogni "capability" richiesta. Con Helium10 sospeso,
però, **nessun provider con dati reali di mercato Amazon è oggi
disponibile** — il sistema resta in mock mode per quella parte finché non si
sblocca almeno un provider.

## Sviluppo

```bash
make test
make lint
```

`make run-worker` **non funziona oggi** — punta a `app.workers.celery_app`,
un modulo che non esiste ancora (Milestone 14: Workers + scheduler, non
costruita). Scoperto lavorando su Phase J, lasciato così finché quella
milestone non viene affrontata invece di essere nascosto.

## Deployment (Docker/VPS)

```bash
cp .env.example .env   # valorizza le credenziali reali prima del deploy
docker compose up --build -d
```

Quattro servizi: `postgres`, `redis`, `api` (esegue le migration Alembic all'avvio, poi serve `app.main:app` su `:8000`), `browser-worker` (immagine separata con Chromium reale via Playwright, volume dedicato `browser_profiles` così un login umano sopravvive a un redeploy, healthcheck su `:8100/health`). Nessun servizio `worker`/celery ancora — `app/workers/celery_app.py` non esiste (Milestone 14). Vedi `docs/architecture.md` addendum (6) per i dettagli e il limite onesto: build/run reali non sono state verificate in questo sandbox di sviluppo (nessun demone Docker disponibile) — solo `docker compose config`, `shellcheck` e la logica applicativa via `pytest`. Verifica end-to-end reale (build immagini, avvio compose) da fare sulla VPS assegnata.

## Milestone

- [x] 1 — Repository + architecture + config
- [x] 2 — Database + migrations
- [x] 3 — Tool abstraction layer — sostituita dal refactor Phase C-F: `app/tools/core/` (Capability/Provider/Registry/Orchestrator), provider Website (reale, con fallback JS/browser)/Amazon/Keepa (mock), Browser Automation Engine (Playwright, verificato reale)
- [ ] 4 — Mock connectors — Amazon/Keepa coperti da Phase D; restano Jungle Scout/Perpetua (deprioritizzati) e Helium10 (sospeso, Phase G)
- [x] 5 — Amazon Scout discovery — `app/agents/amazon_scout/discovery.py`; produce solo ResearchResult/Evidence grezzi, nessuna risoluzione brand/dedup/scoring/AI (vedi `docs/architecture.md` addendum 21/09 (5))
- [x] 6 — Brand resolution — `app/services/brand_resolution.py`, regole deterministiche (no AI): risolve/crea AmazonSeller/Brand/AmazonProduct da un ResearchResult grezzo; `AmazonSeller.verified` resta sempre `False` (riservato a Milestone 10/revisione umana); company resolution e dedup nomi-brand restano fuori ambito (vedi `docs/architecture.md` addendum 21/09 (8)); cap `max_results` aggiunto a `resolve_research_job` (addendum (9))
- [x] 7 — Deduplication — `app/services/brand_deduplication.py`, due livelli: merge automatico solo su suffissi legali inequivocabili dopo normalizzazione (Srl/Spa/Ltd/GmbH..., incl. forme puntate "S.R.L."; suffissi ambigui come Co/Sa/Ag restano fuori dal merge automatico, addendum (10)), near-duplicate per similarità (`difflib`, soglia 0.88) solo segnalato via `AgentEvent`, mai unito automaticamente; nuova colonna `Brand.merged_into_id` (vedi `docs/architecture.md` addendum 21/09 (9)-(10))
- [x] 8 — Opportunity engine — `app/services/opportunity_engine.py`, regole deterministiche: solo `AMAZON_OPPORTUNITY` e `BUSINESS_VALUE` implementati (gli altri 4 tipi richiedono dati che questa pipeline non produce ancora — listing content, ads, sito web del brand, criteri Seller Fast mai specificati); `Opportunity` trattata come serie temporale append-only (vedi `docs/architecture.md` addendum 21/09 (11))
- [x] 8.5 — Audit + Agent Registry + Data Contracts + Evidence/Provider/Browser review + valutazione reale Playwright MCP/Browser Use/SWE-agent/Deep Agents/Agno — `app/agents/{registry,bootstrap}.py`, `app/schemas/contracts.py`; documentazione in `docs/architecture/` (`milestone-8.5-audit.md`, `browser-abstraction.md`, `opportunity-data-matrix.md`, `development-agents.md`); nessuno dei framework valutati diventa dipendenza runtime (vedi `docs/architecture.md` addendum 21/09 (12))
- [x] 9 — Scoring — `app/services/scoring_engine.py`, deterministico: consuma le righe `Opportunity` di Milestone 8 (non ricalcola), mappa livello→punteggio (0-100), confidenza a due livelli onestamente limitata (non riflette mock-vs-reale — gap noto), `evidence_ids` tracciati via `ResearchResult`, `weight_used` da `config/default.yaml`; solo i 2 `OpportunityType` implementati ricevono uno `Score`, nessun punteggio composito persistito (vedi `docs/architecture.md` addendum 21/09 (13))
- [x] 9.5 — Local AI Infrastructure — `app/ai/{provider,ollama_provider,mock_provider,gateway,router,bootstrap,contracts,analyst}.py`: `AIProvider`/`AIGateway`/`AIRouter` (local-only, `cloud` solleva `AICloudNotImplementedError`), `AIAnalyst` (consuma `Evidence`, produce `AIClaim`, mai una raccomandazione commerciale finale, mai promuove INFERENCE/HYPOTHESIS a FACT), target Ollama + `qwen3:1.7b` esatto, costo €0 obbligatorio; **verifica reale contro Ollama non eseguibile in questo sandbox** (rete bloccata verso `ollama.com`/`huggingface.co`, nessun accesso alla VPS reale — vedi `docs/architecture/local-ai.md` e `local-ai-benchmark.md`), test unitari (21) via `MockAIProvider`, test di integrazione reale scritto e correttamente saltato (2) (vedi `docs/architecture.md` addendum 21/09 (14))
- [x] 10 — AI layer (wiring reale) — `app/services/ai_analysis_engine.py`, tabella `ai_claims` (`app/models/ai_claim.py`, migrazione `cc2851ec8377`): primo caller reale di `AIAnalyst` su `Evidence` reali per brand, persiste `AIClaim`, isola i fallimenti AI per-brand (non ferma il batch), `ai.enabled: false` è un no-op pulito; **deliberatamente NON fatto**: blending del segnale AI nel confidence di `Score` (nessuna specifica su come, rischio ai 13 test esatti di `scoring_engine.py`), conferma AI di `AmazonSeller.verified`, review AI dei duplicate_candidate — tutti documentati come prossimo passo, non inventati (vedi `docs/architecture.md` addendum 21/09 (15))
- [ ] 11 — Agent orchestration
- [ ] 12 — CLI
- [ ] 13 — API
- [ ] 14 — Workers + scheduler
- [ ] 15 — Exports
- [ ] 16 — Auditor package
- [ ] 17 — Tests
- [x] 18 — Docker — sostituita dal refactor Phase J: `docker-compose.yml`, `Dockerfile` (api), `Dockerfile.browser-worker`, volumi, healthcheck; build/run reali da verificare sulla VPS (vedi sezione Deployment sopra)
- [x] 19 — Documentation — sostituita dal refactor Phase K: `docs/tools.md`, `docs/providers.md`, `docs/browser.md`, `docs/deployment.md`, README riorganizzato; `docs/architecture.md` resta il registro cronologico delle decisioni
