# Dinamik Advisor — Amazon Intelligence Platform
## Agent 01 — Amazon Scout — Proposta Architetturale (STEP 1–6, pre-Milestone 1)

> **Nota (Phase K, 21/09)**: questo documento è il **registro cronologico delle
> decisioni** — la proposta originale (sezioni 0-7 sotto) seguita dagli
> addenda di ogni fase del refactor, in ordine di scrittura. Per capire
> **come funziona il sistema oggi**, senza scorrere la cronologia, usa i
> documenti di riferimento per argomento:
>
> - [`docs/tools.md`](tools.md) — Capability/Provider/Registry/Orchestrator
> - [`docs/providers.md`](providers.md) — stato di ogni provider concreto (Amazon, Keepa, Website, Helium10 sospeso)
> - [`docs/browser.md`](browser.md) — Browser Automation Engine e il container browser-worker
> - [`docs/deployment.md`](deployment.md) — Docker/VPS, come fare il deploy
> - [`docs/database.md`](database.md) — schema e decisioni di design del DB
>
> "Stato: in attesa di approvazione" nella riga sotto è lo stato **storico**
> al momento in cui questo documento è stato scritto, prima della Milestone
> 1 — l'architettura è stata approvata da allora, come mostra il resto del
> file. Lasciato invariato perché è un registro, non riscritto per
> sembrare sempre aggiornato.

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

## Addendum 2026-09-21 (7) — Phase K: Documentazione

Ultima fase dell'ordinamento esatto che mi hai dato (Phase A→K). Quattro
nuovi documenti di riferimento per argomento — `docs/tools.md`,
`docs/browser.md`, `docs/providers.md`, `docs/deployment.md` — più una nota
in cima a questo file che lo dichiara esplicitamente per quello che è
diventato: un **registro cronologico delle decisioni** (sezioni 0-7 originali
+ sette addenda), non il punto da cui capire come funziona il sistema oggi.
Quel ruolo passa ai nuovi documenti, organizzati per argomento invece che
per fase temporale — architecture.md resta prezioso come cronologia del
"perché", i nuovi documenti rispondono al "come funziona ora".

Contenuto scritto rileggendo il codice reale (provider.py, orchestrator.py,
registry.py, bootstrap.py, engine.py, browser_worker.py, docker-compose.yml),
non copiato dagli addenda precedenti — dove i due divergevano (es. lo stato
di Jungle Scout/Perpetua: nessuna classe provider esiste affatto, non solo
"deprioritizzati") ho fatto fede al codice.

Nessuna modifica a codice applicativo in questa fase — solo documentazione
e il README aggiornato con i link ai nuovi file e la sezione Deployment
espansa. `ruff`, `mypy app tests`, `pytest` (85/85) e `alembic check`
restano quelli verificati nell'addendum (6), invariati.

**Fine dell'ordinamento Phase A→K.** Quello che resta aperto: Phase G
(Helium10, sospesa), il gap noto sul target `make run-worker` (punta a
`app.workers.celery_app`, che non esiste — Milestone 14), e tutte le
Milestone 6-17/19 non ancora affrontate (vedi README, sezione Milestone,
per l'elenco completo con stato).

## Addendum 2026-09-21 (8) — Milestone 6: Brand resolution

Prima milestone dopo la fine dell'ordinamento Phase A→K — sequenza normale
del documento di refactor, non più una fase con lettera propria. Regole
deterministiche, nessuna AI (Milestone 10): dato un `ResearchResult` grezzo
prodotto da Milestone 5, risolve o crea le righe `AmazonSeller`/`Brand`/
`AmazonProduct` implicate dal suo `raw_payload` e valorizza
`ResearchResult.product_id`/`brand_id`, lasciati `NULL` apposta da discovery.

- **`app/services/brand_resolution.py`**, nuovo: `resolve_research_result()`
  (una riga) e `resolve_research_job()` (l'intero job, con lo stesso pattern
  agent-run/try-except-boundary di `discovery.py` — un `AgentRun` proprio,
  `agent_name="brand_resolver"`, non riutilizza quello di Milestone 5).
  Quattro regole (`BrandResolutionRule`, enum locale al modulo, non in
  `app/models/enums.py` — stessa scelta già fatta per `BrowserTaskSpec`/
  `Outcome`, vive vicino a chi la usa): `NEW_BRAND_CREATED`,
  `EXACT_BRAND_NAME_MATCH` (match case-insensitive esatto, niente
  fuzzy — quello è Milestone 7), `EXISTING_SELLER_LINK` (un seller già
  visto riusa il suo link, non viene ri-collegato a ogni osservazione),
  `UNRESOLVED_MISSING_DATA` (arricchimento parziale di Milestone 5 — vedi
  il gap `enriched_any` nell'addendum (5) — il prodotto si risolve comunque,
  il brand no; non è un errore, è registrato come tale).
- **Decisione deliberata, non ovvia**: `AmazonSeller.verified` non viene
  **mai** impostato `True` da questo modulo, nemmeno quando la risoluzione
  è certa al 100% (un ASIN o combacia con un prodotto esistente o no — quella
  parte è esatta). La co-occorrenza di seller e brand su un singolo listing
  Amazon non è conferma indipendente che quel seller rappresenti quel
  brand — un rivenditore può listare un prodotto sotto un brand che non
  possiede. `verified` resta riservato a Milestone 10 (AI) o a una revisione
  umana futura. Il commento di design già presente in `app/models/amazon.py`
  ("BrandResolver, prima le regole poi l'AI") è quello che questo modulo
  implementa alla lettera — non l'ho scritto io ora, esisteva da prima.
- **Gestione conflitto**: se un seller già collegato a un brand osserva un
  nome brand diverso in una nuova rilevazione, il link ESISTENTE viene
  mantenuto (mai sovrascritto silenziosamente) e `outcome.conflict=True`
  genera un `AgentEvent` dedicato (`seller_brand_conflict`) — la discrepanza
  è visibile, non persa.
- **`resolve_research_job()` non tocca `ResearchJob.status`** di proposito —
  quel campo appartiene alla discovery (Milestone 5, COMPLETED/PARTIAL/
  FAILED) e sovrascriverlo qui confonderebbe due fasi diverse della
  pipeline in un solo campo. Il successo/fallimento di questa fase vive
  solo sul suo `AgentRun`.
- **Esplicitamente fuori ambito, per scelta**: company resolution
  (`Brand.company_id` resta `NULL` — servirebbe una capability
  `WEBSITE_DATA` sul brand scoperto che questa pipeline non chiama ancora);
  deduplicazione tra nomi quasi-uguali (Milestone 7); `price`/`currency` su
  `AmazonProduct` (Milestone 5 non chiama mai `PRODUCT_PRICE`).

**Verifica eseguita**: 10 nuovi test (`tests/test_brand_resolution.py`) —
le quattro regole individualmente, `verified` che resta `False` dopo
risoluzione puramente deterministica, il conflitto seller/brand (link
esistente mantenuto, evento registrato), il job driver che salta risultati
già risolti (`product_id IS NOT NULL`), un test end-to-end reale che
incatena Milestone 5 (`run_amazon_scout_discovery`) in Milestone 6
(`resolve_research_job`) e verifica che i due ASIN del fixture mock
(entrambi `seller_id=MOCKSELLER01`, `brand="MockBrand Kitchenware"`)
collassino in esattamente un `Brand` e un `AmazonSeller`, con entrambi i
prodotti collegati, e un'eccezione non gestita catturata al confine
dell'agent-run (stesso pattern di `discovery.py`). 95/95 test totali verdi
(85 + 10), zero regressioni, eseguiti per davvero contro un Postgres locale
reale. `ruff`, `mypy app tests` puliti; `alembic check` conferma nessuna
operazione di schema pendente — Milestone 6 non tocca il modello dati,
solo righe già esistenti nelle tabelle già create in Milestone 2.

## Addendum 2026-09-21 (9) — cap mancante su resolve_research_job + Milestone 7: Deduplication

**Correzione a Milestone 6, prima di procedere**: `resolve_research_job()`
non aveva alcun limite sul numero di `ResearchResult` processati in una
sola chiamata/transazione, a differenza di `run_amazon_scout_discovery`
(`max_products=20`). Su 2 ASIN mock non si vedeva; su un job reale con
centinaia di risultati significava centinaia di insert `AgentEvent` in una
singola transazione. Aggiunto `max_results: int = 100`, con `ORDER BY
created_at` + `LIMIT` — pensata per essere richiamata più volte (un futuro
scheduler, Milestone 14) finché non ritorna meno del cap, non per smaltire
l'intero backlog in una chiamata. Nuovo test
(`test_job_driver_respects_max_results_cap`) verifica il comportamento su
più chiamate consecutive. 96/96 test (95 + 1), `ruff`/`mypy`/`alembic
check` puliti.

**Milestone 7: Deduplication** — `app/services/brand_deduplication.py`,
nuovo. Milestone 6 crea un `Brand` nuovo alla prima osservazione di un nome
e fa match solo su uguaglianza esatta case-insensitive dopo — la sua stessa
docstring rimanda qui tutto ciò che è più sfumato. Due osservazioni dello
stesso brand reale possono quindi finire su due righe `Brand` separate
("Acme Kitchenware" vs "Acme Kitchenware Srl" vs "ACME KITCHENWARE,
S.R.L."). Due livelli di confidenza, tenuti deliberatamente separati —
stessa disciplina che `brand_resolution.py` applica a
`AmazonSeller.verified`:

- **Esatto dopo normalizzazione -> merge automatico.** `normalize_brand_name()`
  minuscolo, spoglia accenti/punteggiatura, elimina un suffisso di forma
  legale noto (Srl, Ltd, GmbH, ... — lista esplicitamente non esaustiva,
  IT/EN/DE/NL/ES/UK) mantenendo il collasso degli acronimi puntati
  ("S.R.L." -> "srl" come singolo token, non tre token "s"/"r"/"l" — bug
  reale scoperto e corretto durante la scrittura di questo modulo, prima di
  scrivere i test: senza quel passaggio "ACME KITCHENWARE, S.R.L." non
  avrebbe mai fatto match con "Acme Kitchenware Srl"). Non cambia *quale*
  azienda un nome indica — è la stessa disciplina della Milestone 6 per il
  match esatto sul nome brand.
- **Similarità alta dopo normalizzazione, ma non esatta -> solo segnalato,
  mai unito automaticamente.** `difflib.SequenceMatcher` (stdlib, non AI),
  soglia `DEFAULT_SIMILARITY_THRESHOLD=0.88` calibrata su casi reali
  ("acme kitchenware"/"acme kitchen ware" -> 0.97, unito solo se sopra
  soglia; "acme kitchenware"/"zenith kitchenware" -> 0.765, mai unito).
  Un merge automatico qui rischierebbe di fondere silenziosamente due
  aziende reali distinte — rischio maggiore di lasciare due righe in giro
  più a lungo. Registrato come `AgentEvent` (`duplicate_candidate`) per
  revisione futura (umana o Milestone 10/AI), non risolto qui.
- **Nuova colonna** `Brand.merged_into_id` (self-FK, `ondelete=SET NULL`,
  migration `8b2573c4b9e4`) — senza un puntatore, `status="MERGED"` sarebbe
  uno stato senza uscita, non ricostruibile. La riga duplicata non viene
  mai cancellata (cancellarla avrebbe comunque `SET NULL` sui figli già
  ripuntati, quindi nessun vantaggio, solo perdita di storia/audit).
  Nota tecnica sulla migration: l'autogenerate di Alembic ha renderizzato
  il vincolo FK senza nome (nessuna naming convention configurata su questo
  progetto — stesso gap preesistente per altri FK come
  `categories.parent_category_id`), il che avrebbe fatto fallire il
  downgrade (`drop_constraint(None, ...)`); corretto dandogli un nome
  esplicito e verificato con un vero giro upgrade→downgrade→upgrade contro
  Postgres locale, non solo `alembic check` sull'upgrade.
- **Selezione del canonico**: `first_seen_at` più antico vince (la prima
  osservazione reale), pareggio spezzato sull'id — deterministico, non
  dipendente dall'ordine di iterazione.
- **Conflitto su `company_id`**: se canonico e duplicato hanno
  `company_id` diversi e non nulli, quello del canonico resta (mai
  sovrascritto silenziosamente), evento `brand_merge_company_conflict`
  registrato — stessa disciplina del conflitto seller/brand di Milestone 6.
  Se solo il duplicato ha `company_id`, viene copiato sul canonico (colma
  un vuoto, non è un conflitto).
- **Cap**: `max_brands=200`, stessa logica di `resolve_research_job` —
  pensato per chiamate ripetute, `summary.truncated` dice se questa
  chiamata ha visto tutto.
- **Esplicitamente fuori ambito**: normalizzazione non-Latina (documentato,
  nessun dato reale oggi la richiede — unico marketplace vivo è "IT");
  una coda/UI di revisione per i `duplicate_candidate` (Milestone 12/13);
  l'un-merge (nessun meccanismo per invertire una fusione).

**Verifica eseguita**: 17 nuovi test (`tests/test_brand_deduplication.py`)
— 7 unitari puri su `normalize_brand_name` (incl. il bug degli acronimi
puntati e la guardia contro una normalizzazione vuota per un nome fatto
solo di suffisso, es. un brand chiamato letteralmente "LLC"), poi contro
Postgres reale: merge automatico con verifica di tutti i figli ripuntati
(seller/prodotto/research result) ed eventi registrati, selezione del
canonico per `first_seen_at`, gruppo di 3 duplicati che collassa in un
canonico, conflitto `company_id` (mantenuto + segnalato) e colmatura senza
conflitto, near-duplicate segnalato ma non unito, brand non correlati che
non generano nessun evento, un brand già `MERGED` escluso da una chiamata
successiva, il cap `max_brands`/`truncated` su chiamate ripetute, e
un'eccezione non gestita catturata al confine dell'agent-run. **113/113
test totali verdi** (96 + 17), `ruff`/`mypy app tests` puliti, `alembic
check` senza operazioni pendenti dopo l'applicazione della migration
(verificata upgrade/downgrade/upgrade reale, non solo `check`).

## Addendum 2026-09-21 (10) — correzione al merge automatico di Milestone 7

Il livello 1 (merge automatico) di `brand_deduplication.py` stripava,
nella stessa lista, sia suffissi legali lunghi/inequivocabili (Srl, Spa,
Ltd, GmbH...) sia token corti e ambigui (Co, Sa, Ag, Sl, Nv, Bv) — questi
ultimi si confondono facilmente con iniziali o parole comuni. "Roma Co" e
"Roma Corp" (o due brand realmente distinti che terminano entrambi per
coincidenza in uno di questi due-lettere) sarebbero stati fusi
automaticamente, senza revisione e senza meccanismo di un-merge.

Corretto separando la lista in `_STRONG_LEGAL_SUFFIXES` (usata sempre, nel
merge automatico) e `_WEAK_LEGAL_SUFFIXES` (usata solo dal livello 2, che
non unisce mai automaticamente — `normalize_brand_name()` ora accetta
`strip_weak_suffixes: bool = False`). Effetto pratico: "Acme Co" e "Acme
AG" non vengono più uniti automaticamente, ma vengono comunque segnalati
come `duplicate_candidate` (il livello 2 usa la normalizzazione aggressiva
proprio perché non ha conseguenze distruttive). Nessuna migration
necessaria — è un cambiamento di sola logica applicativa, nessuna colonna
tocca.

**Verifica eseguita**: 3 nuovi test — normalizzazione di default che non
tocca i suffissi deboli, normalizzazione esplicita che li strippa solo su
richiesta, e un test end-to-end contro Postgres che verifica "Acme Co"/
"Acme AG" NON vengono uniti ma vengono segnalati con similarità 1.0.
**116/116 test totali verdi** (113 + 3), `ruff`/`mypy app tests` puliti,
`alembic check` senza operazioni pendenti (nessuna modifica di schema).

## Addendum 2026-09-21 (11) — Milestone 8: Opportunity engine

`app/services/opportunity_engine.py`, nuovo. Regole deterministiche, no
AI. **Solo 2 dei 6 valori di `OpportunityType` sono davvero implementati:
`AMAZON_OPPORTUNITY` e `BUSINESS_VALUE`.** `config/default.yaml`'s
`scoring.weights` ha già una voce per tutte e sei (compito di Milestone 9,
che consuma righe `Score` — questo modulo non le scrive) ma un peso già
presente in config per un futuro step di scoring non è la stessa cosa di
"questa milestone sa valutare quella dimensione oggi". Le altre quattro
richiedono dati che questa pipeline non produce ancora:

- `LISTING_OPPORTUNITY` — servirebbe `AmazonListing` (bullet point, A+
  content, conteggio immagini/video): nessuna riga `AmazonListing` esiste
  da nessuna parte in questa pipeline, le capability di arricchimento di
  Milestone 5 non la toccano.
- `ADVERTISING_OPPORTUNITY` — stesso gap (`AmazonListing.sponsored_seen`
  o dati ads reali).
- `WEBSITE_OPPORTUNITY` — servirebbe `WEBSITE_DATA` (l'unico provider
  reale del progetto, vedi `docs/providers.md`) chiamato sul sito del
  brand scoperto: niente collega quel provider alla pipeline Amazon
  Scout, e discovery non impara mai l'URL di un brand
  (`Brand.website` resta `NULL` da Milestone 5 in poi).
- `SELLER_FAST_FIT` — servirebbero i criteri di fit reali per il servizio
  "Seller Fast" di Dinamik, mai specificati né nel documento di refactor
  né in questo file — inventarli sarebbe indovinare i criteri di business
  di Frank, peggio che non costruirlo.

Costruire regole segnaposto per questi quattro avrebbe prodotto righe
`Opportunity` che sembrano valutate ma non sono ancorate a nulla — peggio
che non averle.

- **`evaluate_amazon_opportunity`**: segnale di margine di miglioramento
  da dati prodotto reali — rating medio sotto soglia e/o base recensioni
  sottile nonostante il prodotto sia arricchito per davvero. HIGH se
  entrambe le condizioni scattano, MEDIUM se una, NONE se nessuna;
  `insufficient_product_data` se nessun prodotto ha un rating (dato reale
  distinto da "mai valutato", non silenziato).
- **`evaluate_business_value`**: proxy grezzo di dimensione/valore
  potenziale da conteggio prodotti e volume recensioni — non fatturato,
  perché Milestone 5 non chiama mai `PRODUCT_PRICE` (`price`/`currency`
  restano `NULL`). Non ritorna mai NONE per un brand con prodotti: anche
  un brand a un solo prodotto è un business reale, per quanto piccolo.
- Soglie in `config/default.yaml`'s nuova sezione `opportunity_rules:`
  (`Settings.opportunity_rule_config()`, stesso pattern di `score_weights()`).
- **`Opportunity` trattata come serie temporale append-only**, come
  `AgentEvent` — nessun vincolo unique su `(brand_id, opportunity_type)`
  nello schema; rieseguire il motore scrive righe nuove, non aggiorna
  quelle esistenti, così un futuro consumer (CLI/API, non ancora
  costruito) può vedere come una valutazione è cambiata nel tempo. Stessa
  logica per cui `Score` (non toccato da questa milestone) è pensato.
- Un brand valutato riceve sempre una riga per tipo implementato, anche a
  livello `NONE` ("valutato, nessun segnale") — stesso principio di
  `AmazonProvider._search()` per una categoria senza risultati. L'unico
  caso senza alcuna riga è un brand con zero `AmazonProduct` collegati:
  niente da valutare, nemmeno un risultato nullo.
- Brand `MERGED` (Milestone 7) esclusi dalla valutazione — i loro figli
  sono già stati ripuntati al canonico, valutare di nuovo la riga morta
  sarebbe valutare dati stantii.
- Cap `max_brands=200`, stesso pattern di `resolve_research_job`/
  `run_brand_deduplication`.

**Verifica eseguita**: 18 nuovi test — 11 unitari puri sulle due funzioni
di regola (nessun DB: le istanze `AmazonProduct`/`AmazonSeller` non
vengono mai salvate, solo usate come contenitori di attributi), poi contro
Postgres reale: creazione righe per un brand con prodotti, nessuna riga
per un brand senza prodotti, esclusione dei brand `MERGED`, natura
append-only su esecuzioni ripetute, cap `max_brands`/`truncated`,
un'eccezione non gestita catturata al confine dell'agent-run, e un test
end-to-end reale Milestone 5 → 6 → 8 sul fixture mock con valori noti
verificati esattamente contro le soglie reali di config (rating medio e
volume recensioni sani → `AMAZON_OPPORTUNITY` NONE; volume recensioni
altissimo del fixture — 3113 — supera la soglia `review_count_high=500`
da solo → `BUSINESS_VALUE` HIGH). **134/134 test totali verdi** (116 +
18), `ruff`/`mypy app tests` puliti, `alembic check` senza operazioni
pendenti (nessuna modifica di schema — `Opportunity` esiste già dalla
Milestone 2).

### (12) Milestone 8.5 — Audit + Agent Registry + Data Contracts + valutazione browser/dev-tooling (21/09)

Specifica esterna di Fil, 42 sezioni, con una regola esplicita in
apertura: audit prima di qualunque modifica ("non riscrivere alla cieca").
Quattro documenti nuovi in `docs/architecture/` (directory nuova, coesiste
con questo file — questo resta il registro cronologico, quelli sono
riferimenti per-argomento come `docs/tools.md`/`docs/providers.md`):

- `milestone-8.5-audit.md` — mappa la specifica sull'architettura
  esistente. Scoperta principale: gran parte di quanto chiesto esiste già
  (Capability/Provider/Registry/Orchestrator, Evidence, gerarchia
  `ProviderError` con `ProviderRequiresHumanError`, `BrowserAutomationEngine`)
  sotto nomi diversi. Gap reali confermati: nessun Agent Registry (un solo
  agente esisteva), `AmazonListing`/`AdvertisingData` mai popolate,
  `Brand.website` mai collegato a discovery, Seller Fast Fit senza criteri
  mai specificati (confermato anche via note personali di Fil — resta
  `NOT FORMALLY SPECIFIED`, nessun criterio inventato).
- `browser-abstraction.md` — Playwright MCP e Browser Use **installati e
  testati per davvero** (non solo documentati) contro il Chromium reale
  del sandbox. Playwright MCP: funziona al 100%, 25 tool reali via
  handshake MCP completo — `INTEGRATE_OPTIONAL`, non ora (nessun consumer
  interattivo esiste oggi, i suoi tool a esecuzione libera non sono
  adeguati a una pipeline automatica). Browser Use: funziona (motore CDP
  proprio, non più Playwright dalla 0.13) — `EVALUATE_LATER`, trigger
  esplicito = sblocco di Phase G (Helium10, sospesa da Frank).
- `opportunity-data-matrix.md` — le 6 righe di `OpportunityType` in una
  vista sola, con il Data Contract corrispondente e la distinzione tra
  "manca il provider" (Advertising), "manca solo il collegamento nella
  pipeline" (Website — il provider esiste già) e "manca la specifica
  stessa, non la tecnica" (Seller Fast Fit).
- `development-agents.md` — SWE-agent (`REJECT`: duplicherebbe l'agente
  di sviluppo autonomo che questa sessione già è, senza il checkpoint
  umano che governa ogni fase di questo progetto), Deep Agents e Agno
  (`EVALUATE_LATER` entrambi, stesso trigger: quando Milestone 11 — Agent
  orchestration — viene affrontata, non prima e non isolatamente).

**Codice nuovo** (i due gap reali, non i pezzi già esistenti):

- `app/agents/registry.py` (`AgentRegistry`/`AgentMetadata`) +
  `app/agents/bootstrap.py` (`build_default_agent_registry()`) — stesso
  pattern di `app/tools/core/registry.py`/`bootstrap.py` per coerenza, ma
  **non** una riscrittura di `amazon_scout/discovery.py` in una gerarchia
  di classi: l'entrypoint esistente (funzione, già testata) viene
  registrato così com'è. Un solo agente registrato oggi, perché un solo
  agente esiste.
- `app/schemas/contracts.py` — `AmazonProductContract`/
  `AmazonListingContract`/`WebsiteDataContract` con conversioni
  `from_orm_*` reali e testate; `AdvertisingDataContract`/
  `SellerFastFitContract` come sola forma, deliberatamente senza alcuna
  factory (verificato da un test che la assenza sia reale, non solo
  dichiarata) — nessun dato da cui costruirle esiste. `opportunity_engine.py`
  **non** viene migrato su questi contratti in questa milestone: funziona
  già ed è testato contro le righe ORM direttamente; farlo ora avrebbe
  toccato codice funzionante per un guadagno cosmetico, contro la stessa
  regola "preferisci il refactoring incrementale" della specifica.

**Verifica eseguita**: 20 nuovi test (11 Agent Registry, 9 Data Contracts)
— **154/154 test totali verdi**, `ruff check .` e `mypy app/` puliti su
tutto il progetto. Nessuna modifica di schema in questa fase (Agent
Registry e Data Contracts sono puro Python, nessuna tabella nuova) — nessun
`alembic check` necessario.

### (13) Milestone 9 — Scoring engine (21/09)

`app/services/scoring_engine.py` — deterministico, no AI (Milestone 10,
non iniziata — "AI signals validati" nel docstring di `Score` è quello che
QUELLA milestone aggiunge sopra questa, non qualcosa che fa questa).
**Consuma** le righe `Opportunity` già scritte da `opportunity_engine.py`
(Milestone 8) invece di ricalcolare i segnali da `AmazonProduct`/
`AmazonSeller` — un'unica fonte di verità per "cosa abbiamo concluso su
questo brand", non una seconda copia della logica di business destinata a
divergere dalla prima.

Decisioni deliberate, non arbitrarie:

- **Mappa livello→punteggio volutamente grezza** (NONE=0, LOW=25,
  POSSIBLE=40, MEDIUM=60, HIGH=90): esiste per poter pesare/confrontare
  dimensioni diverse con `config/default.yaml`'s `scoring.weights`, non
  perché "MEDIUM=60.0" sia una stima calibrata di valore reale — non ci
  sono ancora dati di esito (un brand HIGH ha davvero convertito?) per
  calibrarla meglio.
- **Confidenza a due livelli, onestamente limitata**: `85.0` se
  l'`Opportunity` sottostante veniva da dati rating/review reali, `30.0`
  se da `"insufficient_product_data"` (l'unico fattore di
  `scoring.confidence.decrease_on` che questo layer può verificare
  deterministicamente oggi). **Non riflette mock-vs-reale**
  (`ProviderResult.is_mock` non viene mai persistito su `AmazonProduct` —
  gap reale, annotato esplicitamente nel modulo invece di essere
  nascosto) — gli altri fattori di `increase_on`/`decrease_on`
  (`multiple_sources_agree`, `ai_inference`, ...) richiedono
  cross-referenziazione multi-provider o l'AI layer, nessuno dei due
  esiste ancora.
- **`evidence_ids` tracciati attraverso `ResearchResult`, non inventati**:
  `Evidence` è scritta contro `entity_type="research_result"`, non contro
  un brand direttamente — questo modulo segue `ResearchResult.brand_id`
  (Milestone 6) per trovare quali research result sono stati risolti su
  quel brand, poi colleziona le `Evidence` attaccate a quelli. Una catena
  reale e interrogabile, non una supposizione.
- **Nessun punteggio composito/aggregato persistito** — la somma pesata
  tra dimensioni è calcolabile a domanda da un futuro consumer
  (CLI/API, Milestone 12/13) dai `Score` per-dimensione già persistiti
  più i pesi già in config: persistere anche l'aggregato sarebbe stato
  stato derivato da tenere sincronizzato, non nuova informazione.
- Stesso pattern append-only di `Opportunity` (nessun vincolo unique su
  `(brand_id, score_type)`, confermato nel modello) — rieseguire scrive
  righe nuove.
- Solo i due `OpportunityType` che `opportunity_engine.py` implementa
  davvero ricevono una riga `Score` — gli altri quattro non hanno righe
  `Opportunity` da leggere, quindi nulla viene inventato per loro.

**Verifica eseguita**: 13 nuovi test (tutti contro Postgres reale — questo
modulo è interamente logica di lettura/scrittura DB, niente da testare in
isolamento puro) — collassamento sull'`Opportunity` più recente per tipo,
tracciamento `evidence_ids`, `weight_used` contro i pesi reali di config,
i due livelli di confidenza, esclusione brand `MERGED`, natura
append-only, cap `max_brands`, eccezione non gestita catturata. **167/167
test totali verdi**, `ruff check .`/`mypy app/` puliti, `alembic check`
senza operazioni pendenti (nessuna modifica di schema — `Score` esiste già
dalla Milestone 2).

## Addendum (14) — 21/09: Milestone 9.5 — Local AI Infrastructure (Ollama + Qwen3 1.7B + AI Gateway)

Richiesta esplicita di Fil dopo Milestone 9: "non voglio spendere un
patrimonio in API" — costruire l'infrastruttura AI locale, a costo zero
obbligatorio, PRIMA di qualunque chiamata AI a pagamento (Milestone 10).

**Architettura reale, costruita e testata**: `AIProvider` (ABC,
`app/ai/provider.py`, famiglia di eccezioni deliberatamente separata da
`IntelligenceProvider`/`ProviderError` per non confondere agenti DATA e
agenti AI — sezione 23 della spec) → `OllamaProvider` (implementazione
concreta via `httpx`, `app/ai/ollama_provider.py`) → `AIGateway` (unico
punto che un agente deve chiamare, `app/ai/gateway.py`) → `AIRouter`
(risolve `local`/`cloud` da config, solo `local` implementato,
`app/ai/router.py`) → `build_default_ai_gateway()` (mirror di
`build_default_registry()`, `app/ai/bootstrap.py`). `AIClaimDraft`/
`AIClaim` (`app/ai/contracts.py`) separano ciò che il modello produce da
ciò che il sistema attesta — `id`/`created_at`/`model`/`provider` non sono
mai auto-dichiarati dal modello. `AIAnalyst` (`app/ai/analyst.py`) è
l'unico agente AI: consuma `Evidence` strutturata, produce `AIClaim`
strutturate, non produce raccomandazioni commerciali finali, non promuove
mai silenziosamente INFERENCE/HYPOTHESIS a FACT (riusa `DataType`,
`app/models/enums.py`, nessun vocabolario nuovo), e scarta ogni claim che
cita un `evidence_id` non presente nell'evidenza realmente fornita —
mai accettato silenziosamente.

`config/default.yaml`'s sezione `ai:` ristrutturata (da un `default_model/
max_tokens/temperature` mai letto da nessun codice, confermato via grep)
in `enabled/provider/local/cloud` — nuovo accessor `Settings.ai_config()`.
`MockAIProvider` (`app/ai/mock_provider.py`) permette 27 test unitari
(`tests/test_ai_provider.py`) senza Ollama reale, sezione 29/30 della
spec — copertura: contratti, gateway, router (incluso l'errore controllato
`AICloudNotImplementedError` per `provider: cloud`), bootstrap
enabled/disabled, tutti gli stati di fallimento della sezione 27
(AI_UNAVAILABLE/MODEL_UNAVAILABLE/AI_TIMEOUT/VALIDATION_ERROR/
INSUFFICIENT_EVIDENCE), validazione evidence_ids, non-promozione di
data_type.

**Nessuna persistenza aggiunta in questa milestone** — `AIClaim` non
diventa una tabella; `alembic check` infatti non rileva operazioni
pendenti. Scelta deliberata, non un'omissione: persisterlo ora, prima che
Milestone 10 definisca come un `AIClaim` alimenta uno `Score`/una
`Opportunity`, avrebbe significato indovinare uno schema che quella
milestone potrebbe dover cambiare.

**Limite reale, non aggirato**: questo sandbox di sviluppo non può
raggiungere né `ollama.com` né `huggingface.co` (403 a livello di policy
di rete, confermato anche interrogando lo stato del proxy stesso) e non ha
accesso alla VPS reale assegnata a Dinamik. Le sezioni 5, 6, 25, 30-31
della spec (install reale, pull del modello, smoke test, benchmark, test
di integrazione contro Qwen3 1.7B vero) non sono state eseguibili da
questa sessione — comunicato a Fil prima di scrivere il resto del codice,
non nel report finale. `tests/test_ai_ollama_integration.py` esiste,
scritto per girare per davvero contro un'istanza Ollama reale, e si salta
in modo pulito (skip, non fallimento, non un fake pass) quando Ollama non
è raggiungibile — 2 test saltati nella run di verifica di questa sessione,
per questo motivo esatto. `docs/architecture/local-ai.md` e
`docs/architecture/local-ai-benchmark.md` documentano il blocco per
intero, con un runbook copia-incolla per Fil (o una sessione futura con
accesso alla VPS) e un template di benchmark deliberatamente vuoto — nessun
numero stimato o copiato da benchmark pubblici, come la spec vieta
esplicitamente.

**Verifica eseguita**: 23 nuovi test — 21 in `tests/test_ai_provider.py`
(tutti via `MockAIProvider`, nessuno richiede Ollama) + 2 in
`tests/test_ai_ollama_integration.py` (scritti per girare contro Ollama
reale, correttamente saltati in questa sessione per il motivo sopra).
**190 test totali (188 passati, 2 saltati)**, `ruff check .`/`mypy app/`
puliti su 10 nuovi file (`app/ai/provider.py`, `contracts.py`,
`mock_provider.py`, `ollama_provider.py`, `gateway.py`, `router.py`,
`bootstrap.py`, `analyst.py` + le due modifiche a `app/core/config.py` e
`config/default.yaml`), `alembic check` senza operazioni pendenti (nessuna
modifica di schema — scelta deliberata). Costo reale di questa milestone:
**€0** — nessuna chiave API richiesta, nessuna chiamata a un provider
cloud, `ai.cloud.enabled: false` per default.

## Addendum (15) — 21/09: Milestone 10 — AI layer (wiring reale, senza spec dettagliata da Fil)

A differenza di 8.5 e 9.5, Fil non ha fornito una spec dettagliata per
questa milestone — solo "continua con la prossima" dopo aver rimandato
l'accesso VPS a stasera. Ambito quindi dedotto dai riferimenti reali già
sparsi nel codice (`Score`'s docstring: "deterministic scoring engine +
validated AI signals"; `config/default.yaml`'s `scoring.confidence.
decrease_on: [..., ai_inference]`; `AmazonSeller.verified`'s docstring:
"the BrandResolver, rules first, then AI"; `brand_deduplication.py`'s
riferimento a una futura review AI dei `duplicate_candidate`), non
inventato da zero.

**Costruito**: il primo caller reale di `AIAnalyst` (Milestone 9.5).
`app/services/ai_analysis_engine.py` — stesso pattern batch/`AgentRun`/
`max_brands`/`truncated` di `opportunity_engine.py`/`scoring_engine.py`:
per ogni brand `ACTIVE`, legge le sue `Evidence` reali (stessa catena
`ResearchResult.brand_id` di `scoring_engine.py`, ma righe complete non
solo id, capped a 20 per brand più recenti — `ai.local.num_ctx: 2048` è un
budget di contesto reale, non un dettaglio ignorabile), le passa a
`AIAnalyst.analyze()`, persiste ogni `AIClaim` risultante nella nuova
tabella `ai_claims` (`app/models/ai_claim.py`, migrazione
`cc2851ec8377`). Un brand senza `Evidence` non viene nemmeno mandato
all'AI (spreco di uno slot di generazione su una VPS da 4GB con
`max_concurrency: 1`). Un fallimento AI per un singolo brand
(AI_UNAVAILABLE/MODEL_UNAVAILABLE/AI_TIMEOUT/VALIDATION_ERROR) viene
loggato come `ErrorLog` (categoria `AI_ERROR`) e non ferma il batch per
gli altri brand — `INSUFFICIENT_EVIDENCE` invece non produce un
`ErrorLog`, è un esito legittimo, non un errore. `ai.enabled: false` è un
no-op pulito e documentato (`AgentRun` SUCCESS, `ai_disabled: True`), non
un crash.

**Deliberatamente NON costruito in questo passaggio** — stessa disciplina
"gap documentato, non criterio inventato" di ogni milestone precedente:

- **Blending del confidence di `Score` con i segnali AI** (`ai_inference`
  in `scoring.confidence.decrease_on`): non esiste una specifica di COME
  un `AIClaim` dovrebbe influenzare la confidenza di uno `Score`
  deterministico — inventarla ora avrebbe rischiato i 13 test di
  `scoring_engine.py` che asseriscono valori di confidenza esatti (es.
  `85.0`) senza un guadagno specificato. `opportunity_engine.py` e
  `scoring_engine.py` restano invariati, non toccati da questa milestone.
- **Conferma AI di `AmazonSeller.verified`**: prefigurata nel docstring
  del modello ma non implementata — stessa ragione.
- **Review AI dei `duplicate_candidate` di `brand_deduplication.py`**:
  idem.

Tutti e tre riusano esattamente la stessa forma di chiamata
(`AIAnalyst.analyze()` su un set di evidenza diverso) — lavoro reale e
concreto per un prossimo passaggio, non un'invenzione qui.

**Verifica eseguita**: 9 nuovi test (Postgres reale, `MockAIProvider` —
nessuna chiamata Ollama reale), copertura: persistenza claim reale,
short-circuit su brand senza evidenza, isolamento del fallimento
per-brand (il batch non si ferma), `INSUFFICIENT_EVIDENCE` senza
`ErrorLog`, evidence_ids inventati → `ErrorLog`, `ai.enabled: false` →
no-op pulito, bookkeeping `AgentRun`/`AgentEvent`, cap `max_brands`,
esclusione brand `MERGED`. **199 test totali (197 passati, 2 saltati per
lo stesso motivo di rete della Milestone 9.5)**, `ruff check .`/`mypy
app/` puliti, `alembic check` senza operazioni pendenti dopo aver
applicato la migrazione `cc2851ec8377` (tabella `ai_claims` nuova — unica
modifica di schema di questa milestone).

## Addendum (16) — 21/09: Milestone 11 — Agent orchestration (scoping onesto, non il nome della milestone)

Nessuna spec da Fil neanche qui ("continua con la prossima milestone").
Ambito dedotto e volutamente ridimensionato rispetto al nome "Agent
orchestration": oggi esiste UN solo agente reale (`amazon_scout`,
`app/agents/registry.py`). L'audit di Milestone 8.5
(`docs/architecture/development-agents.md`) aveva già deciso di rimandare
la valutazione di Deep Agents/Agno (i due framework multi-agente reali
trovati su PyPI) a "quando Milestone 11 viene affrontata" — ma quella
decisione presuppone un secondo agente (Agent 02 — Amazon Auditor) da
coordinare, che non esiste. Costruire un router/coordinatore multi-agente,
o impegnarsi su una dipendenza framework, per un registry con una sola
voce sarebbe lo stesso errore di "costruire per un futuro immaginato" che
il docstring di `AgentRegistry` stesso mette in guardia. Questa decisione
resta quindi ferma, non rivisitata qui.

**Costruito invece**: `app/services/pipeline_orchestrator.py` —
`run_research_pipeline()`, la prima chiamata reale che incatena le sei
fasi già esistenti e già testate singolarmente (discovery Milestone 5 →
brand resolution 6 → dedup 7 → opportunity engine 8 → scoring 9 → AI
analysis 10) in un unico job, nell'ordine corretto. Prima di questa
milestone nessun codice le eseguiva in sequenza — ogni test che ne usa più
di una lo fa a mano, dentro il test stesso. Sicuro per costruzione: ogni
fase esistente già fallisce in modo controllato al proprio interno
(try/except che marca il proprio `AgentRun` FAILED senza rilanciare
l'eccezione, verificato leggendo tutte e cinque le fasi) — l'orchestratore
non ha bisogno di un try/except per fase, quella garanzia esiste già un
livello sotto. Aggiunge un `AgentRun` di livello pipeline
(`agent_name="research_pipeline"`) con un `AgentEvent` per fase che
registra il proprio `agent_run_id` e stato — visibilità sull'intera
catena in un posto solo.

**Asimmetria documentata, non nascosta**: `run_amazon_scout_discovery` è
scoped a UN `ResearchJob`; le fasi successive operano su TUTTI i brand
`ACTIVE`/risultati pendenti, non solo quelli di quel job — questo modulo
non inventa un filtro per far coincidere gli ambiti, esegue ogni fase
esattamente come già esiste.

**Non committa la sessione** — stesso contratto "il chiamante possiede la
transazione" di ogni fase esistente (verificato con un test dedicato che
fa rollback e conferma che nulla sopravvive).

**Verifica eseguita**: 4 nuovi test (Postgres reale, provider Amazon/Keepa
mock, `MockAIProvider` — nessuna chiamata Ollama reale) — run completa
delle sei fasi, un `AgentEvent` per fase con lo stato corretto, nessun
commit della sessione, un `AIClaim` reale persistito quando esiste
evidenza. **203 test totali (201 passati, 2 saltati per lo stesso motivo
di rete)**, `ruff check .`/`mypy app/` puliti, `alembic check` senza
operazioni pendenti (nessuna modifica di schema — questa milestone è pura
orchestrazione, nessuna tabella nuova).

## Addendum (17) — 21/09: Milestone 12 — CLI

L'unica milestone finora con uno scope non ambiguo anche senza una spec
di Fil: `pyproject.toml` dichiarava già `dinamik = "app.cli:app"` da
Milestone 1, e `typer>=0.12` era già una dipendenza — entrambi inutilizzati
fino ad ora. Prima di questa milestone, l'unico modo di eseguire
davvero qualcosa in questo sistema (discovery, resolution, dedup,
opportunity, scoring, AI analysis, o la pipeline di Milestone 11) era
scrivere uno script usa-e-getta o un test — `make run-worker` non
funziona ancora (Milestone 14) e non esiste un'API (Milestone 13).

**Costruito**: `app/cli.py`, quattro comandi — `health` (config + DB reale
+ stato AI reale), `pipeline run` (crea un `ResearchJob` ed esegue
`run_research_pipeline` di Milestone 11 per intero, **committando** — il
primo comando in questo codebase che possiede davvero il confine della
transazione, dato che ogni fase sottostante lo delega esplicitamente al
chiamante), `jobs list` e `brands list` (ispezione read-only, altrimenti
un `pipeline run` non lascerebbe modo di vedere cosa è successo senza
interrogare Postgres a mano).

**Bug reale trovato e corretto scrivendo i test**: i comandi inizialmente
riusavano l'`engine`/`SessionLocal` di `app/database/session.py` (pensati
per un unico event loop di lunga durata, quello di FastAPI). Un comando
CLI invece fa il proprio `asyncio.run()` per invocazione — un `engine`
condiviso tra più chiamate `asyncio.run()` nello stesso processo rompe le
connessioni asyncpg già aperte quando il primo event loop si chiude
("RuntimeError: Event loop is closed"). Emerso scrivendo
`tests/test_cli.py` (che invoca più comandi nello stesso processo via
`CliRunner` — esattamente lo scenario che lo espone; un utente reale che
lancia comandi separati da shell non lo incontrerebbe, ma uno script che
importa `app.cli` e chiama più comandi sì). Corretto: ogni funzione
`_*_async` ora crea e smaltisce il proprio `engine` a vita breve, stesso
pattern che ogni file di test di questo progetto già usa.

**Secondo problema reale, trovato eseguendo il comando per davvero
(non solo nei test)**: un mio smoke test manuale (`dinamik pipeline run`)
ha committato righe reali nel DB di sviluppo condiviso con la test suite,
rompendo 17 test che fanno query non filtrate assumendo tabelle altrimenti
vuote. Ripulito a mano con DELETE mirate per id (non un TRUNCATE globale).
Lezione applicata a `tests/test_cli.py`: una fixture `autouse` pulisce
esplicitamente OGNI tabella un comando CLI può aver scritto — inclusa
`errors`, dimenticata alla prima stesura e scoperta perché
`ai_analysis_engine` scrive righe `AI_ERROR` reali ogni volta che gira
qui (Ollama non raggiungibile in questo sandbox, stesso motivo di
Milestone 9.5) — la suite intera è stata rieseguita due volte di seguito
per confermare che il DB torna pulito, non solo che i singoli test
passano.

**Verifica eseguita**: 6 nuovi test (Postgres reale, `CliRunner` di Typer,
invocazione reale dei quattro comandi — non un mock del layer CLI),
eseguito anche a mano da terminale (`dinamik health`, `dinamik pipeline
run`, `dinamik jobs list`, `dinamik brands list`) contro Postgres reale
in mock mode, con pulizia esplicita dei dati di test. **209 test totali
(207 passati, 2 saltati per lo stesso motivo di rete)**, `ruff check
.`/`mypy app/` puliti, `alembic check` senza operazioni pendenti (nessuna
modifica di schema).

## Addendum (18) — 21/09: Milestone 13 — API

Scope ancorato allo stesso posto che questa sessione controlla da
sempre prima di scrivere codice: il docstring di `app/main.py` nomina
"research jobs, prospects, brands, agent runs" come ambito di Milestone
13 fin da Milestone 1, e Milestone 12 ha già stabilito cosa significhi
concretamente per questo progetto — quattro capacità (`health`,
`pipeline run`, `jobs list`, `brands list`), l'unico modo reale oggi di
eseguire o ispezionare qualcosa da fuori un test. Questa milestone espone
le stesse quattro capacità via HTTP (`GET /health`, `POST /jobs`, `GET
/jobs`, `GET /brands`) più un quinto endpoint genuinamente nuovo, `GET
/jobs/{job_id}` — necessario perché un chiamante HTTP (a differenza di un
utente da terminale) ha bisogno di recuperare il singolo job appena
creato dal suo id, senza listare e filtrare lato client. Nessun endpoint
per "prospects" o "agent runs": verificato con grep che nessun servizio
scrive mai una riga `ProspectStatus`, e "agent runs" oltre a quanto il
summary della pipeline già riporta sarebbe superficie inventata, non
richiesta da nessun precedente reale.

**Costruito**: `app/schemas/api.py` (schemi Pydantic request/response,
appiattiti sugli stessi campi scalari che `app/cli.py` già stampa con
`typer.echo` — non una reinvenzione), `app/main.py` riscritto con i cinque
endpoint sopra. `GET /health` sostituisce la versione precedente
(solo config statica, sempre "healthy" senza controllare nulla) con la
stessa tripla verifica di `dinamik health` — config, connettività DB
reale, stato reale del gateway AI. `POST /jobs` crea un `ResearchJob` ed
esegue `run_research_pipeline` di Milestone 11 fino al completamento
prima di rispondere — stesso contratto sincrono di `dinamik pipeline
run`, nessuna coda/worker in background (Milestone 14 non esiste ancora,
`make run-worker` non funziona). Riusa `app/database/session.py`'s
`get_session()` così com'è, a differenza della CLI: FastAPI sotto uvicorn
ha davvero un solo event loop di lunga durata per tutto il processo, la
condizione per cui quel modulo è stato progettato — non c'è bisogno di
reintrodurre l'engine-per-invocazione della CLI.

**Bug reale trovato scrivendo i test — una terza istanza della stessa
classe di bug di Milestone 12, non lo stesso bug**: i primi test con un
semplice `TestClient(app)` fallivano al secondo/terzo request nella
stessa funzione con `RuntimeError: ... attached to a different loop`.
Causa, confermata leggendo il sorgente di `starlette.testclient`: un
`TestClient(app)` usato senza `with` apre un NUOVO
`anyio.from_thread.start_blocking_portal()` — nuovo thread, nuovo event
loop — per OGNI singola richiesta, riusando `self.portal` solo quando il
client è aperto come context manager. L'`engine` di
`app/database/session.py`, creato una volta all'import, lega le proprie
connessioni asyncpg al loop della prima richiesta e si rompe alla
seconda. La premessa "FastAPI ha un solo event loop" nel docstring di
quel modulo è vera per uvicorn in produzione — non lo è per un
`TestClient(app)` non usato come context manager, e non era mai stata
verificata per davvero fino a qui. Corretto sostituendo il `client`
globale con una fixture `scope="module"` che apre `TestClient(app)` con
`with` una sola volta per file — la vera replica del comportamento di
produzione (un loop persistente), non un workaround.

**Verificato anche con un vero processo uvicorn**, non solo via
`TestClient`: avviato `uvicorn app.main:app` a mano, richieste `curl`
reali contro tutti e cinque gli endpoint (incluso un `POST /jobs` che ha
davvero eseguito discovery→resolution→dedup→opportunity→scoring→AI in
mock mode) — stesso output della CLI (`ai_disabled=false,
claims_created=0`, coerente con Ollama irraggiungibile in questo
sandbox). Ha committato righe reali nel DB condiviso con la test suite,
stesso rischio già documentato in Addendum (17); ripulito subito con
DELETE mirate per tabella (non un TRUNCATE globale) e la suite
rieseguita due volte di seguito, confermando via query diretta che DB
torna a zero righe.

Ruff ha segnalato `B008` (function-call-in-default-argument) su ogni
`Depends(get_session)` — il pattern idiomatico di FastAPI, non un bug
reale (FastAPI chiama `Depends(...)` una volta per richiesta, non una
volta alla definizione). Corretto con la soluzione documentata da ruff
stesso: `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls =
["fastapi.Depends", "app.database.session.get_session"]` in
`pyproject.toml`, non un `noqa` per riga.

**Verifica eseguita**: `tests/test_main.py` riscritto da 1 a 9 test reali
(Postgres reale, `TestClient` reale contro `app.main.app`, nessun mock
del layer HTTP) — 8 netti in più rispetto a Milestone 12 — più uno smoke
test manuale end-to-end con uvicorn reale. **217 test totali (215
passati, 2 saltati per lo stesso motivo di rete)**, `ruff check
.`/`mypy app tests` puliti, `alembic check` senza operazioni pendenti
(nessuna modifica di schema — questa milestone è pura superficie API sui
servizi esistenti). Suite rieseguita due volte di seguito, entrambe
217/217 stabili, DB confermato vuoto via query diretta dopo ciascuna.

## Addendum (19) — 21/09: Milestone 14 — Workers + scheduler

Scope ancorato a tre breadcrumb reali, non indovinato: `pyproject.toml`
dichiara `celery>=5.4`/`redis>=5.0` dal Milestone 1, `Makefile`'s
`run-worker` punta a `celery -A app.workers.celery_app worker` da sempre
(modulo mai esistito fino ad ora), e `config/default.yaml` ha una
sezione `scheduler:` (`enabled: false`, `research_job_cron: "0 8 * * 1"`)
dichiarata durante il refactor ma mai letta da nessun codice — confermato
con grep prima di scrivere questa milestone. `docker-compose.yml` aveva
già scritto da sé, nel proprio commento, cosa sarebbe toccato a questa
milestone: "Deliberately NOT here: a worker/celery service... That
service is Milestone 14's to add, once there's a real task to run."

**Costruito**: `app/workers/celery_app.py` (Celery app, broker/backend
Redis da `settings.redis_url`, serializzazione JSON non pickle),
`app/workers/tasks.py` con due task — `run_research_pipeline_task`
(equivalente in coda di `dinamik pipeline run`/`POST /jobs`: crea un
`ResearchJob`, esegue `run_research_pipeline`, committa, stesso pattern
"engine per invocazione" della CLI per lo stesso motivo) e
`run_scheduled_research_job` (il task periodico che Celery Beat chiama,
usa `default_marketplace` già dichiarato in YAML — non inventato — e
NESSUNA categoria, perché nessuna categoria di ricerca ricorrente è mai
stata specificata da nessuna parte in questo codebase: gap onesto,
documentato nel modulo, non riempito a caso). `Settings.scheduler_config()`
aggiunto a `app/core/config.py`, che rispecchia lo stesso precedente già
stabilito per `ai_config()`: la sezione YAML (`scheduler.enabled`) è
autorevole, non l'env flag `ENABLE_SCHEDULER` — verificato con grep che
quel flag non era mai letto da nessuna parte, esattamente come
`ENABLE_AI_ANALYSIS` prima che Milestone 9.5 lo scoprisse.

**`docker-compose.yml` aggiornato**: due nuovi servizi, `worker` e
`beat` (celery beat separato dal worker, non `worker -B` — un embedded
scheduler duplicherebbe i task periodici se `worker` venisse mai scalato
a più repliche), entrambi riusano l'immagine `api` via `build:` invece di
un Dockerfile dedicato (nessun bisogno di un browser reale, solo
Python+DB+Redis), nessuno dei due esegue le migration all'avvio
(`entrypoint: []`, non lo script di `api` — un servizio solo,
`depends_on: api: condition: service_healthy`, resta responsabile dello
schema). Validato con `docker compose config` (nessun demone Docker in
questo sandbox, stesso limite onesto di Phase J).

**`app/workers/browser_worker.py` resta invariato deliberatamente**: non
esiste ancora nessuna riga `BrowserTask` scritta da nessuna parte del
codebase (confermato con grep), quindi costruire un consumer di quella
coda ora sarebbe la stessa scorciatoia "sembra finito, non lo è" che
questo progetto continua a rifiutare — Milestone 14 ha dato un worker
reale al lavoro che esiste davvero (la pipeline), non a un lavoro
ipotetico.

**Verificato anche con infrastruttura reale, non solo `.apply()` in
processo**: avviato un vero `redis-server` locale, un vero processo
`celery -A app.workers.celery_app worker` (lo stesso comando esatto di
`make run-worker`), dispatch reale di un task via `.delay()` attraverso
Redis, risultato reale recuperato dal result backend (`products_found:
2`, coerente con ogni altro run mock-mode di questa sessione). Anche
`celery -A app.workers.celery_app beat` avviato per davvero e confermato
senza errori di configurazione. Righe committate dal test dal vivo
ripulite subito con DELETE mirate (stesso pattern di ogni milestone
precedente), non un TRUNCATE globale.

**Verifica eseguita**: `tests/test_workers.py`, 5 nuovi test (Postgres
reale, task Celery eseguiti per davvero via `.apply()` — non un mock del
layer worker). Un bug reale trovato scrivendo uno di questi test, non nel
codice di produzione: un test che chiamava `get_settings()` fresco dopo
che l'autouse fixture di `tests/conftest.py` aveva già invalidato la
cache otteneva un oggetto `Settings` diverso da quello che
`app/workers/celery_app.py` legge davvero (catturato una volta
all'import, stesso pattern di `app/main.py`) — il test non provava
nulla finché non è stato corretto per patchare l'oggetto `settings`
giusto. **222 test totali (220 passati, 2 saltati per lo stesso motivo
di rete)**, `ruff check .`/`mypy app tests` puliti, `alembic check`
senza operazioni pendenti (nessuna modifica di schema). Suite rieseguita
due volte di seguito dopo la pulizia del test dal vivo, entrambe
222/222 stabili, DB confermato vuoto via query diretta.

## Addendum (20) — 21/09: Weekly Intelligence Scan (feature, risposta al gap di Milestone 14)

Non una milestone numerata della sequenza originale — la risposta
diretta a un gap che io stesso avevo dichiarato chiudendo Milestone 14:
`scheduler.enabled` restava `false` perché nessuno aveva mai specificato
COSA una scansione ricorrente dovesse cercare. Fil ha risposto con una
spec completa di 46 sezioni ("DINAMIK — WEEKLY INTELLIGENCE SCAN"); il
dettaglio completo di audit, architettura, scope onestamente ridotto e
rationale delle soglie vive in `docs/architecture/weekly-scan.md` — qui
solo il riepilogo di cosa è stato costruito, il bug reale trovato, e la
verifica.

**Audit prima di scrivere codice** (dettagli completi nel documento
dedicato): `AgentRegistry` è introspection-only, non un dispatcher — il
lavoro reale passa ancora da `run_research_pipeline`; `AmazonProduct.
price`/`.currency` non sono mai popolati da questa pipeline (esclude
`PRICE_CHANGE`); `_get_or_create_product` aggiorna `rating`/
`review_count`/`bsr` in place ad ogni osservazione (motivo reale per cui
serve una tabella di snapshot separata); il provider Amazon mock è
statico (stesso output ad ogni chiamata — il change detection va testato
con dati sintetici, non due run reali); solo 2 dei 6 `OpportunityType`
hanno un detector reale; `ResearchJobStatus` copre già tutti gli stati
che servono a `WeeklyScan.status`.

**Costruito**: `app/models/weekly_scan.py` (`WeeklyScan` — vincolo
unique su `period_end` per l'idempotenza; `EntitySnapshot` — riusa il
pattern polimorfico non-FK già usato da `Evidence`, doppia funzione di
snapshot + cronologia cambiamenti via colonna JSONB `changes`), migrazione
`99ceb1d2af8f` (autogenerata, rivista, applicata, `alembic check` pulito);
`app/services/weekly_scan.py` — `ChangeThresholds`/`compute_changes()`/
`classify_opportunity_lifecycle()` (funzioni pure, testabili senza DB),
`run_weekly_scan()` (l'orchestratore: idempotenza → universo (Brand
ACTIVE esistenti, nessuna nuova tabella universo) → `run_research_
pipeline()` INTATTO → snapshot/change detection per prodotto →
classificazione lifecycle per opportunità → report markdown); sezione
`weekly_scan:` in `config/default.yaml` (`enabled: false`, day/time/
timezone, quattro soglie di significatività ciascuna con motivazione
documentata inline — non inventate a caso); `Settings.weekly_scan_
config()` + `weekly_reports_dir`, stesso precedente di `scheduler_
config()`/`ai_config()` (la sezione YAML è autorevole); tre comandi CLI
(`scan weekly [--dry-run] [--force]`, `scan status`, `scan history`),
stesso `typer.Typer` sub-app esistente; `run_weekly_scan_task` in
`app/workers/tasks.py` + una seconda entry `beat_schedule` indipendente
in `app/workers/celery_app.py` (`_weekly_scan_crontab()` converte
`weekly_scan.timezone: "Europe/Rome"` in un crontab UTC via `zoneinfo`
stdlib, con un limite di deriva DST documentato onestamente nel modulo
stesso, non nascosto).

**Bug reale trovato con testing dal vivo, non da un test scritto a
priori**: il branch `dry_run` era scritto DOPO il controllo di
idempotenza — un `dry_run=True` chiamato lo stesso giorno di uno scan
già `COMPLETED` restituiva silenziosamente il riepilogo stantio di
quello scan invece di un preview a stato zero, violando direttamente il
contratto della sezione 28 della spec ("NOT modify persistent state").
Trovato lanciando a mano la sequenza reale (run normale → skip → force →
dry-run) contro Postgres reale in mock mode, esattamente la disciplina
di verifica di ogni milestone precedente. Corretto spostando il branch
`dry_run` prima del lookup di idempotenza; coperto da un test di
regressione dedicato. Due variabili locali (`active_brands`/
`universe_size`) sono rimaste morte dopo lo spostamento del blocco —
rimosse, non lasciate come codice inutilizzato.

**Deliberatamente non costruito in questa passata** (onestà sullo
scope, non un elenco di scuse — dettagli e motivazioni nel documento
dedicato): 9 dei 12 tipi di cambiamento nominati dalla spec (nessun dato
reale dietro); 2 dei 7 stati di lifecycle (`DISAPPEARED`/`RECURRING`,
non distinguibili da una storia di sole due righe `Score`); un budget AI
dedicato per-scan con priorità change-driven (`weekly_scan.max_ai_
calls`) — questa build riusa l'AI già invocata da `run_research_
pipeline`, non aggiunge una seconda passata AI specifica sui cambiamenti
rilevati.

**Verificato con infrastruttura reale**: un vero `redis-server` locale,
un vero processo `celery -A app.workers.celery_app worker`, dispatch
reale di `run_weekly_scan_task` via `.delay()` attraverso Redis,
risultato reale dal result backend — stessa disciplina di Milestone 14,
non solo `.apply()` in processo. Righe committate da ogni run dal vivo
(smoke test manuale + dispatch Celery) ripulite subito con DELETE
mirate per ID, mai un TRUNCATE globale.

**Verifica eseguita**: `tests/test_weekly_scan.py`, 32 test nuovi —
unitari puri (nessun DB) per `compute_changes()`/
`classify_opportunity_lifecycle()`/`ChangeThresholds.from_config()`/
`_period_bounds()`; integrazione contro Postgres reale (mock mode,
pattern a rollback come `test_pipeline_orchestrator.py` — nessuna
DELETE manuale necessaria per questi) per idempotenza, force, il bug di
dry-run (regressione dedicata), change detection contro uno snapshot
sintetico scritto a mano, isolamento errori (pipeline mockata per
fallire → stato `PARTIAL`); test CLI reali via `CliRunner`; un test
Celery via `.apply()` più i test della conversione timezone e del
wiring dello scheduler. **252 test totali (250 passati, 2 saltati per lo
stesso motivo di rete)**, `ruff check .`/`mypy app tests` puliti (102
file sorgente), `alembic check` senza operazioni pendenti dopo
l'applicazione della migrazione. Suite rieseguita due volte di seguito,
entrambe 252/252 stabili; DB confermato a zero righe (`WeeklyScan`/
`EntitySnapshot`/`ResearchJob`/`Brand`) via query diretta dopo la
pulizia di ogni sessione di test dal vivo.

## Addendum (21) — 21/09: Milestone 15 — Exports

Nel codice non c'era niente da cui dedurre lo scope: solo un feature
flag morto (`enable_export`/`ENABLE_EXPORT`, dichiarato dal refactor
originale, mai letto da nessuna parte — confermato con grep prima di
scrivere una riga) e il nome "Exports" dalla sezione 58 della spec
originale, senza nessun dettaglio altrove in questo documento.
Diversamente dal gap dello scheduler di Milestone 14 (dove nessuno
scope ragionevole esisteva senza sapere COSA cercare), qui la scelta
era tra più opzioni ugualmente plausibili — non bloccante ma comunque
una decisione di business, non tecnica. Chiarito direttamente con Fil
prima di scrivere codice, due domande dirette: chi consuma l'export
(risposta: team interno Dinamik, per prioritizzare l'outreach — non un
pacchetto per un prospect, che si sovrapporrebbe alla Milestone 16 —
`AuditorPackage`, già modellata come snapshot JSONB per-brand per Agent
02, deliberatamente non duplicata qui) e in che formato (risposta:
.xlsx nativo con formattazione reale, non CSV).

**Costruito**: `app/services/export_service.py` — `build_brand_export_
rows()` (una riga per Brand, non per AmazonProduct: il rep contatta un
brand/seller, non un singolo ASIN, quindi i campi prodotto sono
aggregati — conteggio/rating medio/somma review/miglior BSR — invece di
ripetere le colonne brand su ogni riga ASIN), `write_brand_export_
workbook()` (openpyxl — intestazione in grassetto, riga congelata,
larghezza colonne calcolata sul contenuto reale, non CSV rinominato in
.xlsx), `run_brand_export()` (orchestratore, sola lettura end-to-end —
nessuna riga scritta nel DB, verificato non assunto — rispetta il
feature flag `ENABLE_EXPORT`, primo vero consumatore di quel flag).
Comando CLI `dinamik export brands [--status ACTIVE] [--limit N]`,
stesso `typer.Typer` sub-app esistente. Nuova dipendenza reale
`openpyxl` (+ `types-openpyxl` per mypy, stesso precedente di
`types-PyYAML`) — non era installata prima, verificato con un import
fallito prima di aggiungerla. Nuovo `Settings.exports_dir`
(`EXPORTS_DIR`, default `exports/`), stesso pattern di
`weekly_reports_dir` — isolato nei test con lo stesso fixture
`tests/conftest.py` già esteso per gli altri due.

**Riusa, non reinventa**: la stessa catena `ResearchResult.brand_id ->
Evidence(entity_type="research_result")` che `scoring_engine.py`'s
`_evidence_ids_for_brand` e `ai_analysis_engine.py`'s `_evidence_for_
brand` già usano (scritta di nuovo qui, non importata tra servizi —
coerente con come quei due la duplicano già tra loro invece di
condividere un helper privato); `Score`/`Opportunity` letti
direttamente per i due `OpportunityType` implementati, stesso insieme
già ristretto da `app/services/weekly_scan.py`'s
`_IMPLEMENTED_OPPORTUNITY_TYPES` — nessuna nuova logica di scoring o di
opportunity.

**Deliberatamente fuori scope**: nessun endpoint API (`POST /exports`
o simile) — solo CLI, perché nessuno lo ha chiesto e il consumo è
interno via file, non via richiesta HTTP; nessun export per singolo
job/ricerca (solo per stato Brand) — non richiesto, e "tutti i brand
ACTIVE" è già il caso d'uso reale per prioritizzare l'outreach; nessuna
sovrapposizione con `AuditorPackage` (Milestone 16, handoff per-brand
per Agent 02) — due export con scopi diversi, non un'unica feature
sdoppiata.

**Verificato con dati reali**: seed via `dinamik pipeline run` reale
(mock mode) contro Postgres reale, poi `run_brand_export()` chiamato
direttamente — workbook `.xlsx` reale ispezionato riga per riga con
`openpyxl.load_workbook` (non solo "il file esiste"): intestazioni
corrette, font grassetto sulla riga 1, `freeze_panes` impostato, dati
reali del brand mock (rating medio 4.45, 3113 review totali, BSR
migliore 430, opportunity level/score/confidence/motivazione presi
davvero da `Score`/`Opportunity`, non fabbricati). Verificato anche il
percorso `ENABLE_EXPORT=false` (eccezione dedicata `ExportDisabledError`,
non un'eccezione generica) e il filtro per status con zero risultati.
Righe committate dal test dal vivo (CLI) ripulite subito con DELETE
mirate per tabella, file `.xlsx` di test rimossi, mai un TRUNCATE
globale.

**Verifica eseguita**: `tests/test_export_service.py`, 12 test nuovi —
unitari puri per `_summarize_reasons()`; integrazione contro Postgres
reale (pattern a rollback, stesso di `test_pipeline_orchestrator.py`/
`test_weekly_scan.py`) per la costruzione delle righe, il filtro per
status, la scrittura reale del workbook (ispezionato con
`openpyxl.load_workbook`, non solo verificato che il file esista),
la garanzia di sola lettura (conteggio `Brand` invariato prima/dopo),
ed `ExportDisabledError`; test CLI reali via `CliRunner` (export dopo
un run pipeline reale, zero risultati, export disabilitato). **264 test
totali (262 passati, 2 saltati per lo stesso motivo di rete di
sempre)**, `ruff check .`/`mypy app tests` puliti (104 file sorgente),
`alembic check` senza operazioni pendenti (nessuna modifica di schema —
questa milestone non tocca il database, solo lo legge). Suite rieseguita
due volte di seguito, entrambe 264/264 stabili; DB confermato a zero
righe via query diretta dopo la pulizia di ogni sessione di test dal
vivo.
