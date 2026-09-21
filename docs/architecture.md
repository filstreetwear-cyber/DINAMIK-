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
