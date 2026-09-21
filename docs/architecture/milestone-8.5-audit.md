# Milestone 8.5 — Audit pre-refactor

Stato: **audit completato, nessun file di codice modificato**. Come richiesto
dalla specifica Milestone 8.5 (sezione "CRITICAL RULE" / Phase 1 del suo
stesso ordine in 20 fasi), questo documento precede qualunque implementazione.
Ogni affermazione qui sotto è verificata da lettura diretta del codice in
questa sessione (21/09/2026), non da memoria di averlo scritto io in fasi
precedenti — dove la specifica chiede "current-state, evidence-based", questo
è il criterio seguito.

Metodologia: `git status`/`git log` (repo pulito, HEAD `275b863`), `find` su
ogni sottopacchetto di `app/`, lettura integrale di `app/tools/core/{capability,provider,registry,orchestrator,bootstrap}.py`,
`app/tools/browser/{engine,mock_engine,session,task}.py`, `app/models/{evidence,agent_run,amazon,website,opportunity,market,enums}.py`,
`app/agents/amazon_scout/discovery.py`, `docs/{providers,browser}.md`,
`docs/refactor-audit-2026-09-21.md`, verifica rete/toolchain via shell reale
(non assunta).

## 1. La scoperta principale

**Gran parte di quello che la specifica Milestone 8.5 chiede esiste già**,
costruito nel refactor Phase A→K di questa stessa sessione, sotto nomi e forme
leggermente diversi da quelli che la specifica propone. Questo non è un
giudizio di merito sulla specifica — è il fatto che rende "non riscrivere alla
cieca" (la sua stessa regola) sia possibile che necessario qui. Il lavoro
reale di Milestone 8.5 non è "costruire da zero", è: **mappare la
terminologia della specifica su quella già in uso, confermare/estendere dove
serve, costruire solo i pezzi genuinamente assenti**.

| Concetto richiesto dalla specifica | Equivalente esistente | Stato |
|---|---|---|
| Data Contract (AmazonProduct/Listing/WebsiteData/AdvertisingData/SellerFastFit normalizzati) | `ProviderResult` (dataclass, `app/tools/core/provider.py`) + i modelli ORM stessi (`AmazonProduct`, `AmazonListing`, `WebsiteProfile`) | **Parziale** — il concetto esiste diviso in due livelli (risultato-di-provider vs riga-DB), non come un singolo tipo "Data Contract" nominato. Nessuna classe `AdvertisingData`/`SellerFastFit` esiste — non c'è dato per popolarle (vedi §4). |
| Evidence Layer | `app/models/evidence.py` | **Già conforme** — `entity_type`/`entity_id` polimorfico, `claim`, `data_type` (FACT/ESTIMATE/INFERENCE/HYPOTHESIS), `source`/`source_url`/`source_type`, `raw_value`/`normalized_value`, `confidence` (0-100), `observed_at`, `provider`, `acquisition_method`. Non manca nulla di quanto la specifica chiede a una evidence row. |
| Agent Registry (register/get/list/find_by_capability + AgentMetadata) | **Non esiste** | **Gap reale**. `app/agents/` contiene un solo agente (`amazon_scout/discovery.py`), nessuna classe base `Agent`, nessun registro. |
| Provider abstraction | `IntelligenceProvider` (ABC) + `ProviderRegistry` + `IntelligenceSourceOrchestrator` | **Già conforme e più maturo di quanto la specifica sembra assumere** — routing per capability, fallback su `ProviderError`, health-check pre-chiamata, ordinamento per (priority, cost), soglia di confidenza opzionale. |
| Gerarchia errori con degradazione a umano | `ProviderError` → `ProviderUnavailableError`/`ProviderAuthError`/`ProviderNotConfiguredError`/`ProviderRateLimitedError`/`ProviderRequiresHumanError` | **Già conforme** — `ProviderRequiresHumanError` è esattamente il pattern "serve un umano" che la specifica chiede, già propagato fino a `BrowserTaskStatus.WAITING_FOR_HUMAN`. |
| BrowserProvider abstraction | `BrowserAutomationEngine` (ABC) + `PlaywrightBrowserAutomationEngine`/`MockBrowserAutomationEngine` + `BrowserSessionHandle` | **Esiste ma più stretto** — solo 2 `task_type` generici implementati (`fetch_rendered_html`, `check_requires_login`), non l'interfaccia navigate/extract/click/type/screenshot/wait/close completa che la specifica propone. Vedi §5. |
| Capability come namespace puntato (`amazon.product.discovery`) | `Capability` enum piatto, 13 valori (`PRODUCT_SEARCH`, `PRODUCT_DATA`, ...) | **Divergenza di stile, non di sostanza** — stesso ruolo (routing), vocabolario diverso. Cambiarlo tocca `ToolCall.capability`, `Evidence` (indirettamente), ogni provider e l'agente esistente: è un refactor con superficie ampia per un beneficio principalmente cosmetico. Raccomandazione in §7. |
| Opportunity Detector separato da Scoring | `app/services/opportunity_engine.py` — funzioni `evaluate_amazon_opportunity()`/`evaluate_business_value()` (detection, deterministiche) scrivono su `Opportunity`; `app/models/opportunity.py::Score` (scoring, Milestone 9, non costruita) è già un modello separato | **Concettualmente già separato** — la specifica chiede una "architettura" per questa separazione; esiste già nello schema (`Opportunity` vs `Score` sono tabelle diverse) e nel codice (nessuna funzione di detection calcola uno score aggregato). Manca solo la formalizzazione a doc esplicita (§8). |

## 2. Cosa NON esiste (gap reali, non da minimizzare)

- **Agent Registry**: zero. Un solo agente reale (`amazon_scout/discovery.py`), nessuna classe `Agent` base, nessun `AgentMetadata`, nessun `register()/get()/list()/find_by_capability()`. Da costruire da zero.
- **AmazonListing popolato**: la tabella esiste (`app/models/amazon.py`), ma **nessun codice la scrive mai** — Amazon Scout discovery (Milestone 5) scrive solo `ResearchResult`/`AmazonProduct`, mai `AmazonListing`. `LISTING_OPPORTUNITY` non è quindi implementabile oggi senza prima costruire l'acquisizione di listing content (titolo/bullet/immagini/A+), che nessun provider fa.
- **AdvertisingData**: nessun modello, nessun provider, nessuna capability `PPC_DATA` mai chiamata da un agente. `ADVERTISING_OPPORTUNITY` non è implementabile.
- **WebsiteData sui brand scoperti**: `WebsiteIntelligenceProvider` esiste ed è reale (non mock), ma **Amazon Scout discovery non lo chiama mai** — `Brand.website` resta sempre `NULL` da Milestone 5 in poi (vedi `docs/architecture.md`, già annotato). `WEBSITE_OPPORTUNITY` non è implementabile finché discovery non acquisisce l'URL del brand da qualche parte.
- **Seller Fast Fit — criteri**: **mai specificati**, né nel documento di refactor originale, né in questo file, né altrove nel repo. Confermato anche via memoria: Seller Fast (sellerfast.tech) è un prodotto separato di Frank (chatbot AI shopping-assistant), non un servizio con criteri di fit Amazon documentati da qualche parte. **Nessun criterio verrà inventato in questa fase** — `SELLER_FAST_FIT` resta `NOT FORMALLY SPECIFIED`, esattamente come già annotato nel docstring di `opportunity_engine.py` e in `docs/architecture.md`.
- **Data Contracts come tipo nominato**: non esiste una classe `AmazonProduct`/`WebsiteData`/... "source-independent" distinta sia dal modello ORM sia da `ProviderResult`. Se la Milestone 8.5 vuole questo, è lavoro nuovo (probabilmente `dataclass`/Pydantic leggeri che wrappano l'ORM, non sostituirlo — vedi §7).

## 3. Capacità reali di questo sandbox (verificate ora, non assunte)

Punto critico per le sezioni 13-16 della specifica (Playwright MCP, Browser Use, SWE-agent/Deep Agents/Agno):

| Capacità | Stato verificato |
|---|---|
| Rete verso `registry.npmjs.org` | **Raggiungibile** (HTTP 200) |
| Rete verso `pypi.org` | **Raggiungibile** |
| `npm view @playwright/mcp` | **Pacchetto reale trovato**, ultima versione pubblicata `0.0.82` |
| `pip download browser-use` (senza installare) | **Riuscito** — pacchetto reale su PyPI |
| `node`/`npm` disponibili nel sandbox | Sì — Node 22.22.2, npm 10.9.7 |
| `pip install playwright` | Già installato (`/usr/local/lib/python3.11/dist-packages/playwright`) |
| Binario Chromium reale | **Presente** — `/opt/pw-browsers/chromium` (symlink a `chromium-1194/chrome-linux/chrome`), lo stesso che `PlaywrightBrowserAutomationEngine` già usa in questo sandbox |
| Demone Docker | **Assente** (`docker.sock` non risponde) — invariato rispetto a Phase J |

Correzione rispetto a quanto assunto prima di questo audit: l'installazione e
uno smoke test reali di Playwright MCP e Browser Use **sono tecnicamente
fattibili in questo sandbox** — non è più solo "da verificare", è verificato.
Il vincolo reale non è l'installazione, è se ha senso farli diventare
dipendenze runtime del progetto dato che `PlaywrightBrowserAutomationEngine`
già copre il caso d'uso attuale (fetch HTML renderizzato, rilevazione
login-richiesto) con meno superficie. La valutazione tecnica dettagliata
(INTEGRATE / INTEGRATE_OPTIONAL / EVALUATE_LATER / REJECT) è oggetto della
fase successiva, non di questo audit — richiede di capire cosa Playwright MCP
e Browser Use offrirebbero che l'engine attuale non offre già, non solo se
sono installabili.

## 4. Tabella di copertura OpportunityType (verificata contro il codice attuale)

| OpportunityType | Implementato? | Dato mancante se no |
|---|---|---|
| `AMAZON_OPPORTUNITY` | Sì (`opportunity_engine.py::evaluate_amazon_opportunity`) | — |
| `BUSINESS_VALUE` | Sì (`opportunity_engine.py::evaluate_business_value`) | — |
| `LISTING_OPPORTUNITY` | No | `AmazonListing` mai popolata |
| `ADVERTISING_OPPORTUNITY` | No | Nessun provider/capability PPC mai chiamato |
| `WEBSITE_OPPORTUNITY` | No | `Brand.website` mai popolato da discovery, quindi `WebsiteIntelligenceProvider` mai invocato su un brand scoperto |
| `SELLER_FAST_FIT` | No | Criteri di business mai specificati da Frank — non un gap tecnico, un gap di specifica a monte |

Invariata rispetto a quanto documentato in Milestone 8 — questo audit la
conferma, non la rivede.

## 5. Nota su Helium10 — decoupling già confermato

`config/default.yaml` (`providers.helium10.enabled: false`, `mode: disabled`),
`app/tools/core/bootstrap.py` (nessuna entry `helium10` nel dict `candidates`
— saltato "a prescindere da `enabled`", per commento esplicito nel file), e
`docs/providers.md` confermano tutti e tre indipendentemente che Helium10 è
già completamente disaccoppiato: **zero codice Helium10-specifico esiste**,
il sistema non lo importa, non lo istanzia, non fallisce se assente. Non
serve refactoring per questo punto della specifica — serve solo
documentarlo esplicitamente come "già vero", cosa che questo audit fa.

## 6. Cosa NON è stato ancora fatto in questa fase

Questo documento chiude la Fase 1 (audit) della specifica. Non è stato
scritto né modificato altro codice. Le fasi 2-20 (Data Contracts formali,
Agent Registry, decisione framework browser, i tre documenti rimanenti,
refactor di dettaglio, test) sono pianificate ma non iniziate — vedi il
messaggio di riepilogo che accompagna questo audit per la proposta di
sequenza e i punti che richiedono una tua conferma prima di procedere.
