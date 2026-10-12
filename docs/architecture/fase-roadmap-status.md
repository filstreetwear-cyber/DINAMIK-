# Fase 0→7 — stato del percorso autonomo (handoff, aggiornato 2026-10-12)

Questo documento è l'entry point per chi (umano o Claude Code) riprende questo
filone di lavoro. Non sostituisce `docs/architecture.md` (il registro
cronologico completo, addenda 1-27) né `README.md` (stato dei Milestone
numerati del refactor originale) — li riassume e dice dove si è fermato il
lavoro, cosa è verificato DAVVERO oggi, e cosa resta da fare, in ordine.

## Il mandato che governa questo filone

Istruzione permanente di Fil, data prima dell'inizio di questo filone e
ancora in vigore: *"Procedi con tutte le fasi, intanto programma tutto, fai
check ad ogni fasi su possibili bug poi passa alla prossima. Tienimi sempre
aggiornato ma esegui tutto in una task, piuttosto fai double check."*

In pratica: avanzare da soli attraverso le fasi 0→7, verificare prima di
avanzare, aggiornare Fil periodicamente (non necessariamente ad ogni fase),
non fermarsi a chiedere se non realmente bloccati — doppio controllo invece
di una domanda.

Questo filone ("Fase N") è distinto dai Milestone numerati del refactor
originale (Milestone 1→15, descritti in `README.md` e nei primi addenda di
`docs/architecture.md`): le Fasi 0→7 partono da un sistema già a Milestone 15
e aggiungono un ciclo operativo completo (opportunity → raccomandazione →
bozza di contatto) più governance/monitoraggio.

## Architettura chiave introdotta in questo filone

**Tre ruoli AI separati, mai indeboliti l'uno per costruire l'altro**:

- `AIAnalyst` (`app/ai/analyst.py`, preesistente) — "cosa è vero": interpreta
  Evidence, vietato produrre raccomandazioni.
- `AIAdvisor` (`app/ai/advisor.py`, Fase 5) — "cosa fare e come": raccomandazione
  operativa ancorata a un finding `Opportunity` già deciso deterministicamente;
  vietato scrivere testo di contatto o dare giudizio commerciale finale.
- `AIOutreachWriter` (`app/ai/outreach_writer.py`, Fase 6) — "bozza di contatto":
  scrive il testo, mai prezzo/sconto/scadenza promessi, mai finzione di
  contatto già avvenuto, sempre bozza per revisione umana (nessun invio email
  automatico esiste).

Pattern draft-vs-stampato usato identicamente 3 volte (`AIClaimDraft`→`AIClaim`,
`AIRecommendationDraft`→`AIRecommendation`, `ContactDraftBody`→`ContactDraft`
stampato dal motore): il modello scrive solo i campi di contenuto, tutta la
provenienza (id, timestamp, model, provider) e tutti i collegamenti
deterministici sono stampati dal codice chiamante dal risultato reale, mai
autodichiarati dall'LLM.

Tabelle append-only (`Opportunity`, `Score`, `AIClaim`, `AIRecommendation`,
`ContactDraft`): una nuova valutazione scrive una riga nuova, non sovrascrive
— chi consuma deve selezionare esplicitamente "l'ultima" per tipo.

## Stato per fase

**Fase 0/1 bis** (completata, prima di questo filone) — chiarito cosa è
bloccato in questo sandbox (VPS, credenziali Amazon reali) vs eseguibile.

**Fase 2** (completata, commit `4b22f55`) — `AmazonScrapingProvider`
(adapter pattern su `amzpy`). **Gap aperto, documentato, non di questa
fase**: nessun wiring verso il DB — i dati scrapati non finiscono mai in
`Brand`/`AmazonProduct`/`AmazonSeller`.

**Fase 3/4** (completata, commit `82ff862`) — Website Intelligence estesa
con `mobile_friendly` (HTML, gratuito) e `page_speed_score`/`seo_score` via
Lighthouse CLI reale (verificato end-to-end contro un server HTTP locale
reale, non solo mockato). Terza delle sei regole `OpportunityType` cablata
(`WEBSITE_OPPORTUNITY`). **Gap aperto, documentato, gemello del precedente**:
nessun input live popola `Brand.website`/`WebsiteProfile` — la regola esiste
ma non ha mai dati reali da valutare finché questo non si chiude.

**Fase 5** (completata, commit `14579f7`) — `AIAdvisor` + tabella
`ai_recommendations` + `app/services/ai_recommendation_engine.py`. Dedup per
`opportunity_id` (una nuova riga Opportunity, anche identica, genera un nuovo
tentativo di raccomandazione).

**Fase 6** (completata, commit `cc5252d`) — `AIOutreachWriter` + tabella
`contact_drafts` + `app/services/contact_draft_engine.py`. Dedup per
`brand_id` (una bozza per brand per sempre — rigenerazione è una feature
futura esplicita, non un bug). Chiude un gap preesistente da Milestone 6:
`ProspectStatus` (`app/models/contact.py`) esisteva ma nessun servizio lo
aveva mai scritto — ora `contact_draft_engine` lo popola
(`NEEDS_REVIEW`, `changed_by="system"`) alla prima bozza generata.

**Fase 7** (PARZIALMENTE completata, commit `ce64db1`, 2026-10-12) —
vedi sezione dedicata sotto. Il wiring è chiuso, il health-check promesso
dal nome della fase no.

## Fase 7 in dettaglio — cosa è chiuso, cosa non lo è

Durante l'audit "la pipeline è davvero end-to-end?" è emerso che
`run_ai_recommendation_engine`/`run_contact_draft_engine` (Fase 5/6) erano
costruiti e testati ma **mai chiamati** da `run_research_pipeline` —
l'unico punto che un futuro CLI/scheduler invocherebbe. `dinamik pipeline
run` avrebbe prodotto Opportunity/Score/AIClaim ma mai una
AIRecommendation o una ContactDraft. Non un bug dei moduli di Fase 5/6
(già verificati singolarmente) — un bug di composizione.

**Fix committato oggi (`ce64db1`)**: `app/services/pipeline_orchestrator.py`
estende la catena da 6 a 8 stage (`PipelineSummary`, `_STAGE_NAMES`,
`AgentEvent` per-stage), `app/cli.py` aggiornato, test di wiring estesi.
Verificato con Postgres reale: **345 passati, 2 saltati, 0 falliti**;
`ruff check .` pulito (dopo un fix di ordine import); `mypy app tests` ai
soliti 3 errori pre-esistenti, nessuno nuovo. Dettaglio completo in
`docs/architecture.md`, Addendum (27).

**Cosa manca ancora, e NON va scambiato per "fatto"**: il nome della fase
("Monitoraggio/health-check pipeline") promette un controllo che oggi non
esiste. `app/cli.py`'s comando `health` (`app/cli.py:82`) controlla solo
config + connettività DB reale + stato del gateway AI (`ai_gateway.
health_check()`). Non controlla:

1. Lo stato per-stage dell'ultima `run_research_pipeline` (quale stage ha
   fallito, quando, con quale errore) — l'informazione esiste già negli
   `AgentEvent`/`AgentRun` scritti da ogni pipeline run, va solo letta e
   presentata.
2. La presenza/versione dei binari esterni che due provider reali
   richiedono: `amzpy` (scraping Amazon, Fase 2) e il CLI `lighthouse`
   (performance audit, Fase 3/4) — oggi se mancano, i provider silenziosamente
   restituiscono `None`/saltano, senza che nessuno se ne accorga finché non
   guarda i dati a valle.

Questo è il prossimo lavoro concreto di Fase 7, non ancora iniziato.

## Gap noti e documentati (non bug, scelte/limiti dichiarati)

1. **Scraping Amazon → DB** (Fase 2): nessuna riga `Brand`/`AmazonProduct`
   popolata dai dati scrapati.
2. **Website discovery → WebsiteProfile** (Fase 3/4): nessun input live per
   la regola `WEBSITE_OPPORTUNITY`.
3. Entrambi richiedono nuove fonti dati (non solo cablaggio come il gap di
   Fase 7) — fuori scope per le fasi che li hanno segnalati, segnalati a Fil
   più volte (addenda 24/25/26), non nascosti.
4. **Discrepanza minore notata oggi, non investigata**: `.env`'s
   `AI_PROVIDER=anthropic` non corrisponde a `config/default.yaml`'s
   `ai.provider: local` (Ollama/Qwen3 1.7B, `ai.cloud.enabled: false`) — che
   è quello effettivamente letto da `Settings`. Probabile variabile
   d'ambiente residua e inutilizzata, non confermato. Da verificare prima di
   assumere che l'AI in produzione punti a Ollama o ad Anthropic.

## Task pendenti, in ordine

1. **Decidere ed eventualmente costruire** l'estensione del comando `health`
   (i due controlli elencati sopra) — questo è ciò che chiuderebbe
   davvero la Fase 7 per nome, non solo per codice.
2. **Verifica finale complessiva** (Task #30 del filone): run pytest/ruff/mypy
   completo sull'intero progetto (non solo i file toccati oggi — fatto per
   Fase 7, non ripetuto per l'intero albero), poi commit.
3. Nessun nuovo lavoro sui due gap di ingestion (Amazon scraping→DB, website
   discovery→WebsiteProfile) finché Fil non decide la priorità — richiedono
   conversazioni su fonti dati, non solo codice.
4. Igiene minore, non urgente: la riga "Stato attuale" in cima a `README.md`
   descrive solo i Milestone numerati (1→15) e non menziona questo filone
   Fase 0→7 — da aggiornare quando si chiude la Fase 7 per intero, seguendo
   la stessa convenzione di addendum.

## Note operative per chi riprende (incluso Claude Code)

- **Postgres non parte da solo in un container freddo**: se i test DB-dipendenti
  danno `ConnectionRefusedError`/errori di fixture invece di skip puliti,
  controllare con `pg_isready`; se down, `pg_ctlcluster 16 main start`. Non è
  un bug applicativo — è successo due volte in questo filone per container
  riavviati dopo inattività.
- Pattern dei test: Postgres reale-o-skip (`_db_reachable()`/fixture
  `session`), `MockAIProvider` iniettato per ogni test dei ruoli AI (nessun
  Ollama reale richiesto nei test).
- Attribuzione git richiesta per ogni commit di questa sessione:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_01PWJhePPGzKjs9Vcinni9Hk`.
- Migrazione corrente: `804dc045d4fc` (head). Workflow: `alembic revision
  --autogenerate` poi revisione manuale del file generato prima di
  `alembic upgrade head`.
- Baseline di verifica attuale (12/10/2026, confermata con Postgres up):
  **345 passed, 2 skipped**, `ruff check .` pulito, `mypy app tests` con 3
  errori pre-esistenti noti (2 `AIProvider._base_url` in
  `test_ai_provider.py`, 1 stub mancante `amzpy.parser` in
  `test_amazon_scraping_provider.py`) — qualsiasi numero diverso da questo
  dopo una modifica è un segnale reale, non rumore.
