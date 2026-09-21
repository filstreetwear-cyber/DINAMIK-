# Dinamik Advisor — Amazon Intelligence Platform
## Agent 01 — Amazon Scout — Proposta Architetturale (STEP 1–6, pre-Milestone 1)

Stato: **in attesa di approvazione**. Nessun codice del progetto è stato ancora generato, come richiesto (sezione 72/75 del prompt originale). Questo documento è l'output di STEP 1–6.

---

## 0. Correzioni rispetto al prompt originale (verificate ora)

Prima dell'architettura, tre correzioni fattuali sui connector elencati, perché cambiano cosa è costruibile in Milestone 5+:

| Connector nel prompt | Realtà verificata | Impatto |
|---|---|---|
| **Sellics** | Non esiste più come prodotto. Sellics si è fuso in **Perpetua** (perpetua.io) anni fa — non un rebranding di facciata, un assorbimento completo della piattaforma. [Certain] | Il connector va chiamato `PerpetuaProvider`, non `SellicsProvider`. Se Dinamik non usa Perpetua, il connector resta `UNAVAILABLE` di default — non vale la pena costruirlo per la v1. |
| **Helium 10 API** | Esiste, ma **solo sul piano Enterprise**, e l'accesso non è self-serve: richiede contatto commerciale diretto con Helium 10 ("Book a Demo" → sales manager entro 24h). Non è una API key che si genera da pannello utente su un piano normale. [Certain] | Se Dinamik non ha già l'Enterprise plan, questo connector resta mock/unavailable finché non c'è una trattativa commerciale separata — non è un task di ingegneria, è un task di procurement. |
| **Jungle Scout API** | Esiste davvero, self-serve, con documentazione pubblica per generare le chiavi da account. [Certain] | Questo è il connector più realistico da rendere operativo per primo, **se** Dinamik ha un piano che include l'API. |

Il punto che resta aperto da prima e che il prompt non risolve: **non so se Dinamik ha già un abbonamento attivo a uno qualsiasi di questi tool.** Senza almeno uno di Keepa/Jungle Scout/Perpetua, il Discovery Engine non ha una fonte dati legittima — il prompt stesso vieta esplicitamente scraping aggressivo di Amazon (sezione 17), quindi non c'è un percorso alternativo "fai da te" pulito. Questo non blocca Milestone 1–4 (tutto costruibile in `MOCK_MODE`), ma blocca qualunque dato reale da Milestone 5 in poi.

**Chiarimento architetturale necessario su Amazon SP-API**: le credenziali Seller Central/SP-API richieste in sezione 17 sono scoped-per-account — funzionano solo su un account Amazon a cui Dinamik ha accesso concesso (un cliente già gestito), non permettono di "cercare" prodotti/seller terzi sul marketplace. Per l'Amazon Scout (discovery di *nuovi* prospect) sono quindi inutili. Sono invece il connector giusto per un futuro Ads Agent/Auditor su clienti già acquisiti. Ho separato questo nell'architettura sotto.

L'Amazon Product Advertising API (PA-API) non è un'alternativa praticabile: richiede un account Associates con un minimo di vendite qualificanti negli ultimi 30 giorni per mantenere l'accesso, ed è pensata per link di affiliazione, non per ricerca di mercato. [Likely, in base a come funziona il programma Associates — da verificare se lo stato attuale è cambiato]

---

## 1. Stack tecnologico — confermato

Nessuna obiezione tecnica alle scelte del prompt: Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, Redis, Celery. Sono scelte standard, mature, coerenti con un sistema che deve reggere job asincroni, stato persistente e crescita modulare. Non c'è motivo di deviare.

---

## 2. Struttura moduli — adottata da sezione 49, invariata

```
dinamik-amazon-intelligence/
├── app/
│   ├── api/
│   ├── agents/amazon_scout/
│   ├── ai/                    # AIProvider abstraction (Anthropic / OpenAI / local)
│   ├── core/
│   ├── database/
│   ├── models/
│   ├── schemas/
│   ├── services/
│   ├── tools/
│   │   ├── amazon/            # SP-API — SOLO per future agent su clienti già acquisiti
│   │   ├── keepa/
│   │   ├── junglescout/       # unico connector realisticamente self-serve oggi
│   │   └── perpetua/          # ex-Sellics — rinominato, opzionale, UNAVAILABLE by default
│   ├── workers/
│   └── main.py
├── prompts/amazon_scout/
├── tests/
├── scripts/
├── migrations/
├── docs/
├── fixtures/
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── Makefile
```

Nota: ho tolto la cartella `helium10/` dalla lista dei connector "di primo impianto" — resta come interfaccia prevista (`IntelligenceTool`) ma non come priorità di sviluppo, dato il gate commerciale sopra.

---

## 3. Schema database — confermato con una correzione

Adotto le tabelle elencate nel prompt (sezioni 7–14) integralmente: `companies`, `brands`, `amazon_sellers`, `amazon_products`, `amazon_listings`, `marketplaces`, `categories`, `research_jobs`, `research_results`, `competitors`, `keywords`, `website_profiles`, `opportunities`, `scores`, `evidence`, `contacts`, `prospect_status`, `auditor_packages`, `agent_runs`, `agent_events`, `tool_calls`, `errors`.

Unica modifica: nell'enum `source_type` della tabella `evidence` (sezione 14), sostituisco `SELLICS` con `PERPETUA`:

```
source_type:
  AMAZON
  KEEPA
  JUNGLESCOUT
  PERPETUA
  WEBSITE
  AI_INFERENCE
  USER
```

`HELIUM10` resta nell'enum ma segnato come fonte a bassa probabilità di essere `AVAILABLE` in v1, per il motivo sopra.

Vincolo confermato: `ASIN` unique per `marketplace` su `amazon_products`. `company != brand != seller` come entità separate, come specificato — corretto, è la distinzione che evita di fondere un distributore con il brand proprietario per errore.

---

## 4. Tool interface — confermata

```python
class IntelligenceTool(ABC):
    name: str
    async def search(...): ...
    async def get_product(...): ...
    async def get_brand(...): ...
    async def health_check(...): ...
```

Stati per ogni connector, come da sezione 22: `AVAILABLE / UNAVAILABLE / DEGRADED / RATE_LIMITED / AUTH_ERROR`. In assenza di credenziali confermate, all'avvio in modalità non-mock: `keepa=UNAVAILABLE`, `junglescout=UNAVAILABLE`, `perpetua=UNAVAILABLE`, `helium10=UNAVAILABLE`, `amazon_spapi=UNAVAILABLE` finché non arrivano le chiavi reali — il sistema deve avviarsi comunque (sezione 21).

---

## 5. Credenziali/API necessarie — risposta a STEP 6

| Provider | Serve per | Come si ottiene | Stato |
|---|---|---|---|
| **Anthropic** | AI layer (analisi opportunità, scoring qualitativo, summarization) | API key propria con billing dedicato — **non** riutilizzabile da questa sessione Cowork, va creata su console.anthropic.com | Da procurare |
| **Keepa** | Price/BSR/sales-rank history — la fonte più solida per stimare volume di vendita in modo indiretto | Abbonamento Keepa + API key self-serve | Da confermare se Dinamik ce l'ha |
| **Jungle Scout** | Product/market/demand intelligence, keyword data | Account con piano che include API + generazione chiave self-serve | Da confermare se Dinamik ce l'ha |
| **Perpetua (ex-Sellics)** | Storicamente overlap con Helium10/JS — probabilmente ridondante se già avete Keepa+JS | Serve un account Perpetua attivo | Probabilmente non necessario per v1 |
| **Helium 10** | Stesso ambito di Keepa/JS | Solo piano Enterprise, gate commerciale con sales call | Non self-serve — non bloccante per iniziare |
| **Amazon SP-API** | NON per lo Scout. Serve solo quando un prospect diventa cliente e Dinamik ottiene accesso al suo account | Registrazione sviluppatore Amazon + autorizzazione per account specifico | Rilevante da Agent 02/Ads Agent in poi, non ora |

**Raccomandazione pratica**: prima di investire su Milestone 5 (Amazon Scout discovery reale), verifica con Frank quali di questi abbonamenti Dinamik ha già attivi per la gestione dei 25+ account. È quasi certo che almeno uno esista già (l'agenzia stima fatturato e qualità listing per i clienti in qualche modo), e riusarlo è molto più veloce che aprirne di nuovi.

---

## 6. Milestone — adottate da sezione 58, annotate per dipendenza da credenziali reali

| # | Milestone | Richiede credenziali reali? |
|---|---|---|
| 1 | Repository + architecture + config | No |
| 2 | Database + migrations | No |
| 3 | Tool abstraction layer | No |
| 4 | Mock connectors (Amazon, Keepa, JS, Perpetua, AI) | No |
| 5 | Amazon Scout discovery | **Sì** (o resta su mock) |
| 6 | Brand resolution | No (regole deterministiche + dati mock) |
| 7 | Deduplication | No |
| 8 | Opportunity engine | No (regole deterministiche) |
| 9 | Scoring | No |
| 10 | AI layer | Sì (Anthropic key) |
| 11 | Agent orchestration | No |
| 12 | CLI | No |
| 13 | API | No |
| 14 | Workers + scheduler | No |
| 15 | Exports | No |
| 16 | Auditor package | No |
| 17 | Tests | No |
| 18 | Docker | No |
| 19 | Documentation | No |

17 delle 19 milestone sono costruibili e testabili **oggi**, in `MOCK_MODE`, senza aspettare nessuna decisione commerciale. Solo la Milestone 5 (dati reali di discovery) e parte della 10 (AI reale invece che mock) dipendono da credenziali esterne. Questo è il motivo per cui non è un blocco: si procede subito su tutto il resto.

---

## 7. Ambiguità aperte (sezione 72, punto 6)

1. Quali provider dati (Keepa/JS/Perpetua/Helium10) Dinamik ha già attivi — blocca solo Milestone 5+, non 1–4.
2. Categoria/nicchia Amazon.it di partenza per il primo research job di test — serve un valore reale anche in mock mode per i fixture.
3. Se Dinamik ha già un account Anthropic con billing proprio, o va aperto ex novo.

Nessuna di queste blocca l'inizio della Milestone 1.

---

## Prossimo passo

In attesa della tua approvazione su questa architettura (come da sezione 73 del prompt originale), poi si parte da Milestone 1 — repository, struttura directory, config, `.env.example` — interamente in mock mode.

---

## Addendum 2026-09-21 — Refactor: Helium10 Optional + Provider/Capability

Milestone 1 e 2 completate e verificate (vedi `docs/database.md`). Prima dell'inizio di Milestone 3 è arrivata una revisione architetturale (`docs/refactor-audit-2026-09-21.md`, audit completo) che sostituisce il modello di connector 1:1 sopra descritto con una gerarchia Capability → IntelligenceProvider → AcquisitionAdapter (API/Browser/Manual/HTTP) → Orchestrator, per non dipendere da un singolo tool commerciale. Punto chiave dell'audit: la sezione 0 sopra ipotizzava Keepa/Jungle Scout/Perpetua come possibili fonti dati reali — nel frattempo è stato confermato che **nessuno di questi è disponibile a Dinamik**, e Helium 10 (piano Basic, non Enterprise) è l'unico tool commerciale attivo.

**Decisione (21/09, in risposta alle domande aperte dell'audit):**

1. `HELIUM10_MANUAL` (inserimento dati a mano da dashboard) **rimandato**, non costruito insieme a Phase C come proposto — Frank vuole prima chiarezza sui Termini di Servizio di Helium10 prima di costruire qualunque automazione (anche manuale/assistita) legata al loro prodotto.
2. Stesso motivo per il Browser Automation Engine collegato a Helium10 — rimandato.
3. Un VPS è già assegnato al progetto (conferma 21/09) — l'infrastruttura di deployment reale esiste, ma il deployment vero e proprio resta fuori dallo scope delle fasi attuali (Phase B/C).

**Conseguenza pratica esplicita**: con Helium10 sospeso, il sistema — una volta completate le fasi in corso — avrà un'architettura Capability/Provider/Orchestrator generica e funzionante, ma **zero fonti dati reali collegate**: Website Intelligence (HTTP/HTML, nessuna credenziale terza parte) è l'unico provider "core" costruibile con dati reali oggi; Amazon e Keepa restano provider mock finché non si chiarisce quali credenziali Dinamik ha davvero. Questo non è un problema di questa fase — è il fatto che va tenuto presente prima di dichiarare "pronto" un qualunque research job che non sia in mock mode.

Stato fasi:
- **Phase A** (audit) — completata.
- **Phase B** (database: colonne additive su `evidence`/`tool_calls`, tabelle `browser_sessions`/`browser_tasks`) — completata e verificata, vedi `docs/database.md` addendum.
- **Phase C** (Capability/Provider/Registry/Orchestrator) — **completata**, vedi addendum sotto.
- **Phase D** (AmazonProvider, KeepaProvider) — **completata** (mock-only, nessuna credenziale confermata), vedi addendum sotto.
- **Phase E** (WebsiteProvider: HTTP acquisition, HTML parser, JS fallback, Browser fallback) — **completata**, vedi addendum "Phase E+F" sotto.
- **Phase F** (BrowserAutomationEngine, BrowserTask, BrowserSession, Playwright) — **completata in sostanza** (engine reale funzionante e verificato, non solo mock); BrowserWorker (Celery) rimandato perché Milestone 14 non è ancora iniziata — vedi dettagli sotto.
- **Phase G** (Helium10Provider/BrowserProvider/ManualProvider) — **sospesa**, come da tua decisione 21/09. Nessun file in `app/tools/helium10/`.
- **Phase H** (orchestrator: capability routing/fallback/cost awareness/confidence/availability) — **completata**, vedi addendum sotto.

---

## Addendum 2026-09-21 (2) — Refactor Phase C: Capability/Provider/Registry/Orchestrator

Costruito in `app/tools/core/`:

- `capability.py` — ri-esporta `Capability` da `app.models.enums` (non una seconda definizione: deve restare la stessa cosa che finisce in `ToolCall.capability`, Phase B).
- `provider.py` — `IntelligenceProvider` (ABC), `ProviderResult` (il dato prodotto — mappa quasi 1:1 su una riga Evidence), gerarchia di eccezioni (`ProviderUnavailableError`, `ProviderAuthError`, `ProviderNotConfiguredError`, `ProviderRateLimitedError`, `ProviderRequiresHumanError`) che l'Orchestrator usa per decidere il fallback.
- `registry.py` — `ProviderRegistry`: registrazione con priorità esplicita, lookup per capability ordinato. La priorità è passata a mano alla registrazione (non letta da config) perché la ristrutturazione di `config.py` per priorità/mode/cost_model per-provider (REQUIRED CHANGES #1 dell'audit) resta un lavoro aperto, separato da questa fase.
- `orchestrator.py` — `IntelligenceSourceOrchestrator`: prova i provider in ordine di priorità per una capability, cade sul successivo solo per un `ProviderError` (mai per un bug generico, che deve propagare), registra l'intero percorso di tentativi (`attempts`).

Due provider reali collegati:

- **`app/tools/website/`** (`http_client.py`, `html_parser.py`, `provider.py`) — **l'unico provider con dati reali** oggi: nessuna credenziale terza parte, fetch HTTP + parsing con BeautifulSoup/lxml, rileva tecnologia (Shopify/WooCommerce/Magento/WordPress), presenza di widget di chat, titolo/meta description. Segnali euristici (tecnologia, chatbot) marcati `DataType.INFERENCE`, mai FACT; titolo/meta letti direttamente dal markup marcati FACT.
- **`app/tools/amazon/provider.py`**, **`app/tools/keepa/provider.py`** — mock-only (nessuna credenziale confermata), dati da fixture in `fixtures/`. Ogni risultato porta `is_mock=True` — un campo nuovo su `ProviderResult`, separato da `data_type`, così un dato fixture non può mai essere scambiato per un'osservazione reale a bassa confidenza. Rifiutano di rispondere fuori da `MOCK_MODE` (`ProviderNotConfiguredError`), invece di restituire silenziosamente dati finti.

`ToolHealthStatus` esteso con `AUTH_REQUIRED`, `REQUIRES_HUMAN`, `CONFIGURATION_ERROR` (additivo, stessa logica string+enum di sempre).

**Verifica eseguita**: 27 nuovi test (registry, orchestrator fallback — incluso il caso esplicito "un bug reale in un provider non deve essere trattato come fallback", provider Website con HTML fisso via fixture e failure path, provider Amazon/Keepa mock), tutti verdi; i 14 test di Milestone 1/2/Phase B invariati e ancora verdi — 41/41, zero regressioni; `ruff` e `mypy app tests` puliti.

**Limite onesto, non nascosto**: ho provato a verificare `WebsiteIntelligenceProvider` anche contro un sito reale (sellerfast.tech ed example.com), non solo con HTML fisso in test. Il sandbox in cui giro nega la connessione in uscita verso domini generici a livello di gateway (403 alla CONNECT, verificato via `__agentproxy/status` — non è il sito che blocca, è la policy di rete di questo ambiente). Il percorso di fetch HTTP è quindi verificato fino al livello "parsing + gestione errori", non end-to-end contro internet reale da qui — quella verifica va fatta dove il sistema girerà davvero (la VPS che Frank ha già assegnato al progetto).

---

## Addendum 2026-09-21 (3) — Refactor Phase D (Amazon/Keepa) + Phase E (Website escalation) + Phase F (Browser Engine)

Ricevuto il dettaglio esatto delle fasi (C-K) dal documento originale — la mia numerazione nell'addendum precedente etichettava "Phase C" un lavoro che in realtà copriva anche Phase D. Correggo qui: Amazon/KeepaProvider (`app/tools/amazon/provider.py`, `app/tools/keepa/provider.py`) sono la Phase D, già costruiti e testati nel commit precedente — nessun nuovo codice necessario per dichiararla completa.

**Phase E completata per davvero**, non solo HTTP: `app/tools/website/html_parser.py` aggiunge `looks_js_rendered()` — euristica (HTML lungo, testo visibile quasi assente = probabile SPA lato client). `WebsiteIntelligenceProvider.fetch()` ora prova prima il livello HTTP (economico), e se il risultato sembra un guscio JS-rendered, esegue l'escalation al livello browser (Phase F) per un fetch con JS eseguito, ri-parsando quell'HTML. Se anche il livello browser fallisce, il provider tiene il risultato HTTP originale invece di sollevare un'eccezione — un sito JS-heavy senza fallback funzionante è un dato informativo (bassa completezza dati), non un errore del provider. Penalità di confidenza esplicita (-10 punti) sui segnali ottenuti via browser, perché è un passo di inferenza in più rispetto al DOM servito direttamente.

**Phase F**: `app/tools/browser/{task,session,engine,mock_engine}.py`. `BrowserAutomationEngine` genera due `task_type` generici, nessuno specifico di Helium10: `fetch_rendered_html` (quello che serve alla Phase E) e `check_requires_login` (rilevamento generico di un form di login — non tenta mai di autenticarsi, riporta solo `WAITING_FOR_HUMAN`). Motore reale basato su Playwright, usando il Chromium pre-installato in questo ambiente tramite il percorso stabile `/opt/pw-browsers/chromium` (indicato dalle istruzioni dell'ambiente stesso). **Verificato per davvero, non solo con mock**: lanciato un vero processo browser, navigato una pagina locale, letto l'HTML renderizzato, e — verifica esplicita del meccanismo umano-nel-loop — creata una pagina locale con un campo password e confermato che il motore reale ritorna `WAITING_FOR_HUMAN` senza tentare di interagire con il form. `BrowserWorker` (integrazione Celery) rimandato: Milestone 14 (workers/scheduler) non è ancora iniziata, `app/workers/` è vuoto — costruire un worker Celery ora significherebbe inventare un'infrastruttura che nessun'altra parte del sistema usa ancora. Ho costruito invece la funzione async riusabile (`BrowserAutomationEngine.run`) che quel worker chiamerà, senza forzare la forma dell'integrazione prima che serva davvero.

`app/core/config.py` guadagna `browser_profiles_dir` e `browser_headless` (additivo). I profili browser vivono fuori dal repo (`.gitignore` aggiornato).

**Bug di igiene dei test trovato e corretto durante la verifica**: la prima versione faceva sì che qualunque test toccasse il fallback browser di `WebsiteIntelligenceProvider` — anche con un motore mock iniettato — chiamasse comunque `resolve_session_handle()` con le impostazioni reali di default, creando `data/browser_profiles/` nella working tree come effetto collaterale della test suite. Corretto con una fixture `autouse` in `tests/conftest.py` che isola `BROWSER_PROFILES_DIR` per ogni test in una directory temporanea — trovato eseguendo la suite per davvero, non ipotizzato.

**Verifica eseguita**: 13 nuovi test (motore mock, motore reale — inclusi i due test end-to-end su Playwright reale, non skippati in questo ambiente perché il binario Chromium è presente —, escalation/non-escalation del provider Website, fallback-del-fallback quando il livello browser non produce nulla di utilizzabile). 54/54 totali verdi, zero regressioni. `ruff` e `mypy app tests` puliti. `alembic check` pulito (nessuna modifica di schema in questa fase).

---

## Addendum 2026-09-21 (4) — Refactor Phase H: Orchestrator (capability routing, fallback, cost awareness, confidence, availability)

Le cinque cose elencate nel documento per Phase H, una per una:

- **capability routing / fallback** — invariato da Phase C (`for_capability()` + il ciclo try/except dell'orchestrator).
- **availability**, nuovo: prima di chiamare `fetch()`, l'orchestrator controlla `provider.health()`. Solo `AVAILABLE`/`DEGRADED` procedono alla chiamata vera; un provider che sa già di non essere utilizzabile (`AUTH_REQUIRED`, `CONFIGURATION_ERROR`, `RATE_LIMITED`, ecc. — gli stati aggiunti in Phase B) viene saltato senza sprecare una chiamata, e lo skip finisce in `attempts` esattamente come un fallimento di `fetch()`.
- **cost awareness**, nuovo: `config/default.yaml` guadagna una sezione `providers:` (priorità, `cost_model`, `mode` — quest'ultimo validato ma non ancora letto da nessun provider, riservato alla Phase G). `ProviderRegistry.register()` accetta ora un `cost_model`, e `for_capability()` ordina per `(priority, cost_rank)` — la priorità resta la scelta esplicita dell'operatore e vince sempre; il costo rompe solo i pareggi tra priorità uguali. Un vero budget di spesa per-job **non** è implementato — serve un posto dove tracciare la spesa lungo un intero research job, che non esiste fino a quando la Milestone 5+ non collega `ResearchJob` a questo layer. Nuovo `app/tools/core/bootstrap.py`: `build_default_registry()` è il punto che trasforma la config in un registry reale — solo i provider con `enabled: true` vengono registrati (oggi: website_intelligence, amazon, keepa); Helium10/Jungle Scout/Perpetua semplicemente non compaiono nel registry, non sono registrati-ma-rotti.
- **confidence**, nuovo: `orchestrator.fetch()` accetta un `min_confidence` opzionale — un risultato sotto soglia viene trattato come "non abbastanza buono", registrato come tentativo fallito, e si passa al provider successivo. Deliberatamente **non** fa aggregazione/merge tra provider né ri-punteggia niente — quello spetta a chi consuma `OrchestratorResult` più avanti (un futuro EvidenceService/motore di scoring, Milestone 8/9), non al routing.

**Verifica eseguita**: 22 nuovi test (parsing config `providers:`, `cost_rank()` e il suo comportamento su valori sconosciuti, tie-break priorità/costo, skip per `UnhealthyProvider` su più stati di salute, `DEGRADED` che *non* viene saltato, skip per `BelowMinConfidence`, un risultato vuoto che non conta come "sotto soglia", `build_default_registry()` che include solo i provider abilitati nell'ordine giusto). Ho dovuto aggiornare due test esistenti di Phase C (`test_falls_back_to_second_provider_after_first_fails`, `test_rate_limited_provider_also_triggers_fallback`): i loro provider finti riportavano `health()` non disponibile, quindi con il nuovo controllo di disponibilità venivano scartati prima ancora di arrivare a `fetch()` — cambiava cosa il test stava davvero dimostrando. Li ho separati in due percorsi distinti e testati entrambi, invece di far quadrare l'assert. 71/71 test totali verdi, zero regressioni sulle fasi precedenti. `ruff`, `mypy app tests` e `alembic check` puliti (nessuna modifica di schema in questa fase).

## Addendum 2026-09-21 (5) — Phase I / Milestone 5: Amazon Scout discovery

Come annotato nell'addendum (1): Phase I del documento di refactor dice "refactor dell'Agent 01 affinché utilizzi esclusivamente `IntelligenceSourceOrchestrator`" — ma `app/agents/amazon_scout/` era vuoto, non c'era nulla da refactorare. Questa fase è quindi, di fatto, la Milestone 5 (discovery), costruita rispettando comunque il requisito letterale di Phase I: l'agente non importa mai una classe provider concreta, solo `IntelligenceSourceOrchestrator`/`Capability` — verificato con un test statico (`ast`) dedicato, non solo a occhio.

Cosa c'è, e cosa deliberatamente non c'è ancora (per non saltare passi verso Milestone 6/7/8/9/10):

- **Nuova capability** `PRODUCT_SEARCH` (`app/models/enums.py`): tutte le altre capability sono lookup per-ASIN; discovery va nella direzione opposta (categoria/keyword IN, ASIN candidati OUT). Nessun provider Amazon/Keepa/Jungle Scout reale offre oggi ricerca prodotto aperta per Dinamik — resta mock-only finché non si conferma un provider capace.
- `AmazonProvider` estesa con `_search()`: filtra il fixture esistente per `category`/`marketplace` (nessun nuovo file di fixture). Una ricerca senza risultati è un risultato vero (ricerca eseguita, categoria vuota), non un "entità sconosciuta" — quindi ritorna sempre un `ProviderResult`, mai `[]`, a differenza dei lookup per-ASIN.
- Bug pre-esistente corretto: `ResearchJob.created_at` non aveva `default=utcnow` (unico modello a mancarne, tutti gli altri lo ereditano da `TimestampMixin`/`CreatedAtMixin`). Nessuna migration necessaria — è un default lato Python, la colonna non cambia forma; confermato con `alembic check` (nessuna operazione rilevata).
- `app/agents/amazon_scout/discovery.py`, nuovo: `run_amazon_scout_discovery(session, job, orchestrator, max_products=20)`. Per ogni job: una chiamata `PRODUCT_SEARCH` (evidence attaccata al job stesso, prima che esista un'entità per-prodotto), poi per ogni ASIN trovato (capped a `max_products`) un `ResearchResult` grezzo arricchito con `PRODUCT_DATA`/`SELLER_DATA`/`BRAND_DATA`/`SALES_RANK` — quest'ultima inclusa apposta perché è l'unica capability servita sia da Amazon sia da Keepa, quindi un run reale esercita davvero il fallback tra provider, non solo la meccanica di health/confidence già testata in isolamento. Un `AgentRun` per l'intero job, `AgentEvent` per i milestone (`search_completed`, `product_enriched`), un `ToolCall` + zero-o-più `Evidence` per ogni chiamata all'orchestrator — così una decisione di fallback (Amazon fallito su SALES_RANK, Keepa ha risposto) è ricostruibile dal solo `ToolCall`.
- Ambito rispettato alla lettera: `ResearchResult.product_id`/`brand_id` restano `NULL` (Milestone 6/7), nessun calcolo di opportunità/score (Milestone 8/9), nessuna chiamata AI (Milestone 10) — ogni claim scritto viene passato quasi invariato da un `ProviderResult`, senza upgrade di confidence lungo il percorso.
- Gestione errori: un'eccezione non gestita durante l'arricchimento (un vero bug, non un `ProviderError`) è l'unica eccezione documentata alla regola "i bug si propagano" dell'orchestrator — qui viene catturata una volta sola, al livello dell'agent-run, e resa visibile via `job.status=FAILED` + `job.error` + una riga `ErrorLog`, non nascosta. Una singola capability irraggiungibile (`NoProviderAvailableError`) invece non ferma l'intero job — quel prodotto resta parzialmente arricchito e il job finisce `PARTIAL`, non `FAILED`.

**Verifica eseguita**: 10 nuovi test — 3 su `AmazonProvider._search()` (filtro categoria/marketplace, nessun match, nessun filtro), 6 sull'agente stesso: run completo con asserzioni su ogni tabella coinvolta (`ResearchResult`/`Evidence`/`AgentRun`/`AgentEvent`/`ToolCall`, inclusi i conteggi esatti), ricerca vuota che completa il job senza errore (caso 0==0), fallback reale Amazon→Keepa su `SALES_RANK` forzando un fallimento di Amazon (non un fake generico: un `AmazonProvider` vero con un solo metodo sovrascritto), ricerca senza provider registrati che non fa esplodere il job, un bug vero durante l'arricchimento catturato e registrato, e il controllo statico anti-import-concreto. Più 1 test aggiunto a `test_database_integration.py` per il fix di `ResearchJob.created_at`. 81/81 test totali verdi (71 + 10), zero regressioni, eseguiti per davvero contro un Postgres locale reale. `ruff`, `mypy app tests` puliti; `alembic check` conferma nessuna operazione di schema pendente.

## Addendum 2026-09-21 (6) — Phase J: Docker/VPS

Quattro pezzi, uno per voce del documento di refactor ("docker-compose, browser-worker, volumes, healthchecks"):

- **`Dockerfile`** (servizio `api`): installazione **editable** (`pip install -e .`), non un wheel buildato — `app/core/config.py` e i provider mock risolvono `config/` e `fixtures/` via `Path(__file__).resolve().parents[N]` relativo alla root del repo, non un path di dati impacchettato. Un `pip install .` normale avrebbe fatto avviare il container e poi fallire a runtime non trovando `config/`. Non è una scorciatoia, è l'unico modo che fa funzionare il codice così com'è oggi — annotato esplicitamente nel Dockerfile stesso.
- **`Dockerfile.browser-worker`**: immagine separata (non bloatare `api` con Chromium), `playwright install --with-deps chromium` seguito da un symlink stabile a `/opt/pw-browsers/chromium` — stessa convenzione di path hardcoded nell'engine (Phase F), così `BROWSER_EXECUTABLE_PATH` (nuovo setting, vedi sotto) può restare non impostato anche qui. Deliberatamente **non** ho scritto a mano l'elenco delle librerie apt che Chromium richiede (fonts/libnss/libatk/...): non essendo questa immagine mai stata effettivamente buildata in questo sandbox (nessun demone Docker disponibile — vedi sotto), fidarsi della lista ufficialmente mantenuta di Playwright (`--with-deps`) è più onesto di un elenco scritto a mano che non ho modo di verificare completo.
- **`docker-compose.yml`**: `postgres`, `redis`, `api` (esegue le migration all'avvio via `scripts/docker-entrypoint-api.sh`, poi serve l'app), `browser-worker` (volume dedicato `browser_profiles` per `BROWSER_PROFILES_DIR` — un login umano sopravvive a un redeploy), tutti con `healthcheck`. **Deliberatamente niente servizio `worker`/celery**: `app/workers/celery_app.py` non esiste (Milestone 14, non ancora costruita) — definire un servizio compose che punta a un modulo Python inesistente sarebbe esattamente il tipo di scorciatoia "sembra finito, non lo è" che questo progetto si è impegnato a non prendere. Scelta di sicurezza esplicita: `browser-worker` NON riceve `env_file: .env` per intero (a differenza di `api`) — riceve solo le variabili che legge davvero (`MOCK_MODE`, `ENVIRONMENT`, `LOG_LEVEL`, `BROWSER_HEADLESS`), non anche `ANTHROPIC_API_KEY`/`AMAZON_API_SECRET`/`HELIUM10_API_KEY` — stessa disciplina di "mai esporre segreti non necessari" della sezione 13, applicata a cosa un container riceve, non solo a cosa logga.
- **`app/workers/browser_worker.py`** + **`app/core/config.py`** (`BROWSER_EXECUTABLE_PATH`, nuovo) + **`app/tools/browser/engine.py`** (`browser_engine_available()`, `_resolve_executable_path()`): il processo che gira nel container `browser-worker` non è ancora un consumer di task (nessuna coda popolata da consumare — vedi sopra), è un healthcheck reale: al boot esegue un vero smoke test dell'engine (`fetch_rendered_html` su `about:blank`, zero rete richiesta) attraverso lo stesso identico percorso di codice usato da ogni provider (`build_browser_engine()`), non un percorso separato e non verificato, e lo serve su `GET /health`. Correzione collaterale reale: il path dell'eseguibile Chromium era hardcoded al path di simlink di *questo* sandbox di sviluppo — un vero bug di portabilità che avrebbe reso il browser-worker silenziosamente non funzionante su una VPS reale, dove `playwright install` non crea quel path automaticamente. Ora è configurabile (`BROWSER_EXECUTABLE_PATH`), col comportamento esistente invariato quando non impostato.

**Limite onesto, non nascosto**: in questo sandbox `docker` è presente ma **senza demone** (`docker version` risponde per il client, fallisce per il server) — build e run reali delle immagini non sono verificabili qui, stessa classe di limitazione già incontrata in Phase E per il fetch di siti reali. Quello che **è** stato verificato per davvero: `docker compose config` (validazione strutturale reale — risoluzione di `env_file`+`environment`, `depends_on`, volumi, porte; ha confermato che `browser-worker` riceve solo le variabili previste e che `api` risolve `DATABASE_URL`/`REDIS_URL` sugli hostname di rete compose corretti, non `localhost`), `shellcheck` pulito su `scripts/docker-entrypoint-api.sh`, e l'intera logica applicativa nuova (health server, selezione engine reale/mock, override di `BROWSER_EXECUTABLE_PATH`) testata per davvero via `pytest` — incluso un lancio reale di Chromium attraverso il path di codice del browser-worker, non solo attraverso l'engine testato in isolamento in Phase F. Build/deploy reali delle immagini vanno verificati sulla VPS assegnata al progetto.

**Verifica eseguita**: 4 nuovi test (`tests/test_browser_worker.py`) — health in modalità mock, health con un lancio Chromium reale attraverso l'engine e il path del container, un guardrail che il messaggio di `/health` continui a dichiarare onestamente l'assenza di un consumer di task, e che `BROWSER_EXECUTABLE_PATH` venga davvero letto da `build_browser_engine()`. 85/85 test totali verdi (81 + 4), zero regressioni. `ruff`, `mypy app tests` puliti; `alembic check` conferma nessuna operazione di schema pendente (nessuna modifica al DB in questa fase).
