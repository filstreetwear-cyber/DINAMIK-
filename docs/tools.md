# Tool layer — Capability / Provider / Registry / Orchestrator

Riferimento per `app/tools/core/`, il cuore del refactor "Helium10 Optional +
Provider/Capability" (21/09). Sostituisce la Milestone 3 originale ("Tool
abstraction layer") — vedi `docs/architecture.md` addendum (2)/(4) per la
cronologia delle decisioni; questo documento descrive lo stato attuale, non
come ci si è arrivati.

## L'idea in una frase

L'Amazon Scout (e ogni futuro agente) chiede dati per **capability**
("dammi SALES_RANK per questo ASIN"), mai per provider ("chiama Keepa"). Chi
risponde, con quale metodo, e cosa fare se il primo tentativo fallisce sono
decisioni dell'orchestrator, non del chiamante.

```
agente → IntelligenceSourceOrchestrator.fetch(Capability, **params)
             → ProviderRegistry.for_capability(Capability)   # chi può rispondere, in che ordine
                 → IntelligenceProvider.health()              # è disponibile ORA?
                 → IntelligenceProvider.fetch(...)             # tentativo vero
             → OrchestratorResult | NoProviderAvailableError
```

## Capability (`app/tools/core/capability.py`)

Enum, definito in `app.models.enums` (single source of truth, perché
`Capability` è anche il valore di `ToolCall.capability` — non due
vocabolari che possono divergere). Valori attuali:

`PRODUCT_SEARCH`, `PRODUCT_DATA`, `PRODUCT_PRICE`, `PRICE_HISTORY`,
`SALES_RANK`, `SELLER_DATA`, `BRAND_DATA`, `KEYWORD_DATA`, `SEARCH_VOLUME`,
`COMPETITOR_DATA`, `MARKET_DATA`, `WEBSITE_DATA`, `PPC_DATA`.

`PRODUCT_SEARCH` è l'unica capability "al contrario": categoria/keyword IN,
ASIN candidati OUT — le altre sono tutte lookup per-ASIN (vedi
`docs/architecture.md` addendum (5) per il perché).

## IntelligenceProvider (`app/tools/core/provider.py`)

Interfaccia astratta che ogni fonte dati implementa:

- `name: str` — stabile, finisce su `Evidence.provider`/`ToolCall.provider`.
  Rinominarlo rompe la continuità storica dei dati, non è un refactor
  innocuo.
- `supported_capabilities: frozenset[Capability]`
- `async def health() -> ToolHealthStatus` — economico, senza side-effect,
  **non deve mai sollevare eccezioni**: uno stato non disponibile si
  riporta, non si lancia.
- `async def fetch(capability, **params) -> list[ProviderResult]` — solleva
  una delle eccezioni sotto se il provider stesso non può rispondere ora;
  ritorna `[]` (non un'eccezione) se ha risposto ma non ha trovato nulla —
  distinzione che l'orchestrator usa per decidere se ritentare con un altro
  provider.

Un provider **non deve mai inventare un FACT** che non ha osservato
direttamente — se sta stimando (euristica, non un campo documentato), è lui
a impostare `data_type=ESTIMATE`/`INFERENCE`, non il chiamante: è l'unico
che sa come ha ottenuto il numero.

`ProviderResult` (frozen dataclass) — quasi 1:1 con una riga `Evidence`:
`capability`, `data`, `data_type`, `confidence` (0-100, la STIMA DEL
PROVIDER prima di qualsiasi aggiustamento a valle), `acquisition_method`,
`observed_at`, `source_url`, `raw_value`, `cost_estimate`, `is_mock`.

`is_mock` è deliberatamente separato da `data_type`: `data_type` risponde
"quanto siamo sicuri che questo claim sia vero", `is_mock` risponde "questo
riguarda un prodotto vero o un fixture". Un FACT può essere mock (un
fixture che dichiara con certezza totale il prezzo di un ASIN finto) —
confondere i due permetterebbe a output mock di passare per un claim reale
a bassa confidenza, o viceversa.

Eccezioni (`ProviderError` e sottoclassi) — la famiglia che l'orchestrator
cattura per decidere il fallback; qualunque altra eccezione (un bug vero)
si propaga normalmente, non viene silenziata:

| Eccezione | Significato |
|---|---|
| `ProviderUnavailableError` | giù/irraggiungibile/non implementato per questa capability |
| `ProviderAuthError` | credenziali rifiutate (una chiamata è stata tentata) |
| `ProviderNotConfiguredError` | nessuna credenziale/config esiste affatto |
| `ProviderRateLimitedError` | throttling (`retry_after_seconds` informativo) |
| `ProviderRequiresHumanError` | serve un passo umano (login/CAPTCHA/MFA) |

## ProviderRegistry (`app/tools/core/registry.py`)

`register(provider, priority=100, cost_model="unknown")` — priorità più
bassa = tentato prima; a parità di priorità, `cost_model` rompe il pareggio
(`free` < `mock` < `cheap` < `paid` < `unknown`; `unknown` è deliberatamente
il peggiore, non il migliore — un provider mai costato non va preferito a
uno noto per essere economico). Ri-registrare lo stesso `name` sovrascrive
— comodo per test e per override in fase di avvio.

`for_capability(capability)` — provider che la supportano, ordinati per
`(priority, cost_rank)`.

Il registry stesso **non dipende da `app.core.config`** — priorità e costo
sono passati esplicitamente da chi registra, cosa che lo mantiene testabile
con provider finti e numeri arbitrari (vedi `tests/test_provider_registry.py`).
Chi lo collega davvero alla config è `bootstrap.py`, sotto.

## IntelligenceSourceOrchestrator (`app/tools/core/orchestrator.py`)

`fetch(capability, min_confidence=None, **params) -> OrchestratorResult`,
per ogni provider (nell'ordine del registry):

1. **Availability** — `health()`; se non è `AVAILABLE`/`DEGRADED`, skip
   registrato come tentativo fallito (`error_type="UnhealthyProvider"`),
   **senza sprecare una `fetch()` reale**.
2. **Fetch** — try/except sulla famiglia `ProviderError`; un fallimento
   passa al provider successivo.
3. **Confidence** — se `min_confidence` è impostato e il miglior risultato
   è sotto soglia, trattato come fallito (`error_type="BelowMinConfidence"`)
   e si passa oltre. Un risultato **vuoto** non conta come "sotto soglia"
   (non c'è nulla di cui essere poco sicuri) — altrimenti un "nessun dato
   trovato" legittimo scatenerebbe un fallback sbagliato.
4. Primo successo vince: ritorna `OrchestratorResult(provider, results,
   attempts)`. Se tutti i provider falliscono/vengono saltati:
   `NoProviderAvailableError(capability, attempts)` — `attempts` porta la
   traccia completa, non un generico "nessun dato".

**Non fa aggregazione multi-provider** né ri-punteggia nulla — quello
spetta a chi consuma `OrchestratorResult` più avanti (Milestone 8/9), non al
routing. E **non fa retry automatico su bug**: un'eccezione che non è un
`ProviderError` si propaga e rompe la chiamata — nasconderla come "provider
non disponibile" nasconderebbe un bug vero.

## bootstrap.py — dalla config a un registry reale

`build_default_registry(settings=None) -> ProviderRegistry` è l'unico punto
che trasforma `config/default.yaml`'s `providers:` in un registry
popolato. Solo i provider con `enabled: true` vengono registrati — oggi
`website_intelligence`, `amazon`, `keepa`. Jungle Scout/Perpetua/Helium10
**non compaiono nel registry**, non sono registrati-ma-rotti — è così che
la sospensione di Helium10 si vede concretamente nel sistema in esecuzione,
non solo nella documentazione.

Ogni agente/servizio che ha bisogno di un orchestrator dovrebbe ottenerlo
da qui, non registrare provider a mano con numeri di priorità inventati
(quello è riservato ai test che verificano la meccanica in isolamento).

## Aggiungere un nuovo provider

1. Implementa `IntelligenceProvider` in `app/tools/<nome>/provider.py`.
2. Aggiungi una voce in `config/default.yaml`'s `providers:` (priorità,
   `cost_model`, `mode`, `enabled`).
3. Aggiungi il factory lambda in `bootstrap.py`'s `candidates`.
4. Test: minimo uno per capability supportata, più il comportamento di
   `health()` in/fuori `MOCK_MODE` (vedi `tests/test_mock_providers.py`
   come modello).

Vedi `docs/providers.md` per lo stato dei provider concreti e
`docs/browser.md` per il livello di acquisizione via browser.
