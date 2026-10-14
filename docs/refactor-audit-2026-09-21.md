# Refactor audit — Helium10 Optional + Provider/Capability Architecture

Stato: **audit completato, nessun file di codice modificato**. Come da tua istruzione (sezione 104, STEP 1-4), questo è il report pre-refactor. Nessuna Phase è iniziata.

---

## CURRENT ARCHITECTURE

### Milestone 1
STATUS: **completata e verificata**. Repository, config Pydantic+YAML, app FastAPI con `/health`, 9 test.

### Milestone 2
STATUS: **completata e verificata**. 22 tabelle su PostgreSQL reale, migration testata avanti/indietro/avanti, `alembic check` a zero diff, 12 test totali (3 di integrazione contro DB reale).

### Existing tool architecture
**Non esiste.** `app/tools/{amazon,keepa,junglescout,perpetua}/__init__.py` sono file vuoti (0 byte). Nessuna classe `IntelligenceTool`, nessun provider, nessun orchestrator è mai stato scritto — Milestone 3 (tool abstraction layer) non era ancora iniziata quando è arrivato questo documento. Questo è il fatto più importante del report: **non c'è niente da "refactorare" in questo layer, c'è solo da costruirlo con il disegno nuovo invece di quello vecchio.**

### Existing database
22 tabelle applicate. Rilevante per il refactor:

- `evidence` ha già `source`, `source_type`, `data_type`, `confidence`, `observed_at` — **manca** `provider` e `acquisition_method` (sezione 42/50 del tuo documento).
- `tool_calls` ha già `tool_name`, `status`, `duration_ms` — **manca** `provider`, `acquisition_method`, `capability`, `retry_count`, `cost_estimate` (sezione 51).
- Non esistono `browser_sessions` né `browser_tasks` (sezione 52-53) — tabelle nuove da creare.
- Tutte le altre 19 tabelle (companies, brands, amazon_*, research_*, opportunities, scores, ecc.) **non richiedono modifiche** per questo refactor.

### Existing AI layer
Vuoto (`app/ai/__init__.py`, 0 byte). Nulla da refactorare, solo da costruire — ma il tuo documento non lo tocca in questa fase (l'AI layer resta fuori scope per PHASE B-I salvo dove l'orchestrator deve passare provenance all'AI, sezione 48).

### Existing configuration
`app/core/config.py` ha flag booleani piatti per provider (`enable_keepa`, `enable_junglescout`, `enable_perpetua`, `enable_helium10`) e un `tool_status()` binario AVAILABLE/UNAVAILABLE. **Non supporta** `mode` (api/browser/manual/disabled), `priority`, `cost_model`, salute per-capability — richiesti dalla sezione 10. Va **ristrutturato**, non riscritto: la parte di merge YAML+env resta valida, cambia cosa viene modellato per ogni provider.

### Docker / VPS
Non esiste ancora (Milestone 18 mai iniziata). Le sezioni 35 e 85-87 lo anticipano per il browser-worker.

**Nota che devi conoscere prima di procedere**: questa sessione gira in un sandbox cloud effimero, non su un VPS. Posso scrivere e testare in mock mode Dockerfile/docker-compose e l'interfaccia del browser engine, ma **non posso verificare "profilo browser persistente che sopravvive tra sessioni"** nel mio ambiente — quello richiede l'infrastruttura reale su cui il sistema girerà. Dimmi se e quando c'è un VPS assegnato a questo progetto, altrimenti il browser-worker resta verificato solo in mock mode fino a quel momento.

---

## REQUIRED CHANGES

1. **`app/core/config.py`** — ristrutturare da flag piatti a configurazione per-provider (mode, priority, cost_model), probabilmente spostando la configurazione provider-specifica in YAML (`config/default.yaml`, sezione `providers:`) validata via Pydantic, mantenendo le env var solo per credenziali/secret.
2. **`app/models/evidence.py`** — aggiungere `provider` (str, nullable) e `acquisition_method` (str, nullable).
3. **`app/models/agent_run.py`** (classe `ToolCall`) — aggiungere `provider`, `acquisition_method`, `capability`, `retry_count`, `cost_estimate`.
4. **Nuovo `app/models/browser.py`** — `BrowserSession`, `BrowserTask`.
5. **Nuovo `app/tools/core/`** — `capability.py` (enum), `provider.py` (`IntelligenceProvider` ABC), `registry.py` (`ProviderRegistry`), `orchestrator.py` (`IntelligenceSourceOrchestrator`).
6. **Nuovo `app/tools/website/`** — `WebsiteIntelligenceProvider` con escalation HTTP→HTML→JS→browser.
7. **Nuovo `app/tools/helium10/`** — `provider.py`, `browser_provider.py`, `manual_provider.py`, `schemas.py`, `selectors.py`.
8. **Nuovo `app/tools/browser/`** — `BrowserAutomationEngine`, `BrowserSession`, `BrowserTask`, `MockBrowserAutomationEngine` (Playwright dietro interfaccia, mockabile).
9. `app/tools/amazon/` e `app/tools/keepa/` — passano da stub vuoti a provider reali che implementano `IntelligenceProvider` (in mock mode, dato che nessuna credenziale reale è ancora confermata).
10. `app/tools/perpetua/` — resta, ora esplicitamente in categoria OPTIONAL come da nuova gerarchia (nessun cambio rispetto a quanto già corretto dopo l'audit Sellics→Perpetua).
11. `.env.example` — aggiungere `HELIUM10_ENABLED`, `HELIUM10_MODE`, `HELIUM10_API_ENABLED`, `HELIUM10_BROWSER_PROFILE`, `BROWSER_*`.
12. `config/default.yaml` — aggiungere sezione `providers:` con enabled/priority/mode/acquisition per ciascuno (esempio già nella sezione 10 del tuo documento).
13. `docs/architecture.md` e `README.md` — aggiornare con la nuova gerarchia (core: Amazon/Keepa/Website; optional: Helium10/JungleScout/Perpetua) e lo stato del refactor.

## Files affected (esistenti, da modificare)

```
app/core/config.py
app/models/evidence.py
app/models/agent_run.py
app/models/__init__.py        (registrare le nuove tabelle)
.env.example
config/default.yaml
docs/architecture.md
README.md
```

## New files

```
app/models/browser.py
app/tools/core/{capability,provider,registry,orchestrator}.py
app/tools/website/{provider,http_client,html_parser}.py
app/tools/helium10/{provider,browser_provider,manual_provider,schemas,selectors}.py
app/tools/browser/{engine,mock_engine,session,task}.py
migrations/versions/xxxx_add_provider_acquisition_fields.py
tests/test_provider_registry.py
tests/test_orchestrator_fallback.py
tests/test_browser_mock.py
```

## Migration required

**YES** — ma puramente additiva: nuove colonne nullable su `evidence` e `tool_calls`, due tabelle nuove (`browser_sessions`, `browser_tasks`). Nessuna colonna esistente cambia tipo o viene rimossa.

## Backward compatibility

`evidence` e `tool_calls` sono **vuote in produzione** — nessun research job è mai stato eseguito, le uniche righe che ci sono mai state erano righe di test, e i test le fanno sempre rollback (verificato: `SELECT count(*)` = 0 su tutte le tabelle dopo la suite). Rischio di perdita dati sulla migration: **nullo**. I 12 test esistenti di Milestone 1+2 non toccano nessuna delle tabelle/colonne modificate da questo refactor — mi aspetto che continuino a passare invariati, e lo verificherò esplicitamente prima di dichiarare qualunque fase completata (obbligo che hai messo tu stesso in sezione 81).

---

## Architettura finale proposta

Adotto la tua gerarchia così come descritta (sezioni 2-13), con due note pratiche:

```
CAPABILITY (cosa serve)
      ↓
INTELLIGENCE PROVIDER (chi lo fornisce: Amazon / Keepa / Website / Helium10 / JungleScout / Perpetua)
      ↓
ACQUISITION ADAPTER (come: API / BROWSER / MANUAL / HTTP)
      ↓
ORCHESTRATOR (sceglie provider+adapter per availability/priority/cost/confidence/freshness)
```

- `HELIUM10_MANUAL` lo costruirei **insieme alla Phase C** (orchestrator), non alla Phase G come da tuo ordine — è economico, a rischio zero, e disponibile da subito col piano Basic. Il Browser Automation Engine vero (Playwright, sessioni persistenti, login umano) resta comunque nell'ordine che hai indicato, perché è lui il pezzo costoso e rischioso.
- Il browser-worker Docker/VPS (Phase J) lo costruisco e testo in mock mode qui; la verifica su infrastruttura reale dipende dal VPS che non è ancora stato menzionato in questa conversazione.

---

## Domande aperte prima di iniziare Phase B

1. Confermi che posso costruire `HELIUM10_MANUAL` in parallelo alla Phase C invece che aspettare la Phase G?
2. Il rischio ToS di Helium10 Browser Automation (sopra, non tecnico) — lo accetti consapevolmente, o per ora costruisco il Browser Engine generico ma non lo collego a Helium10 finché non decidi?
3. C'è già un VPS assegnato a questo progetto, o l'infrastruttura di deployment è ancora da definire?

Nessuna di queste blocca la Phase B (database), che è additiva e a rischio zero — posso partire da quella appena confermi.
