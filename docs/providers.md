# Provider concreti — stato

Riferimento per le implementazioni concrete di `IntelligenceProvider`
(vedi `docs/tools.md` per l'interfaccia astratta). Per ognuna: cosa
supporta, mock o reale, cosa serve per renderla reale.

## Riepilogo

| Provider | File | Capability supportate | Stato | Priorità/cost_model |
|---|---|---|---|---|
| `website_intelligence` | `app/tools/website/provider.py` | `WEBSITE_DATA` | **Reale** (HTTP+HTML sempre; fallback browser via Phase F) | 10 / `free` |
| `amazon` | `app/tools/amazon/provider.py` | `PRODUCT_SEARCH`, `PRODUCT_DATA`, `PRODUCT_PRICE`, `SALES_RANK`, `SELLER_DATA`, `BRAND_DATA` | Mock (fixture) | 20 / `mock` |
| `keepa` | `app/tools/keepa/provider.py` | `PRICE_HISTORY`, `SALES_RANK`, `PRODUCT_PRICE` | Mock (fixture) | 30 / `mock` |
| `junglescout` | — (nessuna classe) | — | Non costruito, deprioritizzato | 90 / `unknown`, `enabled: false` |
| `perpetua` | — (nessuna classe) | — | Non costruito, deprioritizzato | 90 / `unknown`, `enabled: false` |
| `helium10` | — (nessuna classe) | — | **Sospeso** (Phase G, ToS da chiarire) | 100 / `unknown`, `enabled: false` |

`enabled`/`priority`/`cost_model` sono in `config/default.yaml`'s
`providers:`; `app/tools/core/bootstrap.py::build_default_registry()`
registra solo quelli con `enabled: true` — vedi `docs/tools.md`.

## Website Intelligence

Unico provider con dati **reali** costruibile oggi — nessuna credenziale
di terze parti richiesta. Tre livelli, con degradazione esplicita:

1. **HTTP** (`app/tools/website/http_client.py`) — `httpx.AsyncClient`,
   User-Agent custom, timeout 10s di default.
2. **HTML parsing** (`app/tools/website/html_parser.py`) — rilevamento
   tecnologia (Shopify/WooCommerce/Magento/WordPress via firme note),
   chatbot, form di ricerca, title/meta.
3. **Fallback browser** (Phase F) — se `looks_js_rendered(html)` sospetta
   che la pagina sia uno shell JS (HTML lungo ma poco testo visibile), si
   tenta il livello browser (`fetch_rendered_html`). Il risultato browser
   porta una penalità di confidenza (`-10.0`) rispetto a un HTTP diretto —
   è comunque meno diretto. Se anche il browser fallisce, si tiene il
   risultato HTTP sottile invece di sollevare un'eccezione: un dato parziale
   è meglio di nessun dato.

`health()` ritorna sempre `AVAILABLE` — non ci sono credenziali che possano
mancare.

## Amazon / Keepa — mock-only

Nessuna integrazione reale esiste: SP-API è account-scoped (inutile per la
discovery di nuovi prospect, serve da Agent 02 in poi) e PA-API è
Associates-gated; Keepa non ha credenziali confermate per Dinamik. Entrambi
esistono per dare al layer Capability/Orchestrator qualcosa di reale a cui
instradare in `MOCK_MODE`, con fixture (`fixtures/amazon_mock_products.json`,
`fixtures/keepa_mock_history.json`, due ASIN fittizi `B0MOCK0001`/
`B0MOCK0002`, categoria "Casa e cucina", marketplace "IT") — e per rendere
testabile per davvero il fallback dell'orchestrator (Amazon fallisce su
`SALES_RANK` → Keepa risponde), non solo mockato a metà.

`health()` ritorna `AVAILABLE` in `MOCK_MODE`, `CONFIGURATION_ERROR` fuori
— deliberatamente non `UNAVAILABLE`: `UNAVAILABLE` implica "collegato ma
giù ora", lo stato onesto qui è "mai stato collegato a nulla di reale".

Una futura integrazione reale sostituisce solo il corpo di `fetch()`, senza
toccare orchestrator/registry/bootstrap.

`AmazonProvider._search()` (Milestone 5) filtra il fixture per
`category`/`marketplace` — nessun match è un risultato vero (ricerca
eseguita, categoria vuota), non "entità sconosciuta": ritorna sempre un
`ProviderResult`, mai `[]`, a differenza dei lookup per-ASIN.

## Jungle Scout / Perpetua

Nessuna classe provider esiste. Deprioritizzati nella revisione
architetturale del 21/09 — non disponibili per Dinamik oggi. Le voci in
`config/default.yaml` esistono solo per documentazione/wiring futuro;
`bootstrap.py` le salta comunque (vedi `docs/tools.md`), a prescindere da
`enabled`.

## Helium 10 — sospeso

Unico tool dati commerciale realmente attivo per Dinamik (piano Basic, non
Enterprise) — e nonostante questo, **nessun codice specifico Helium10 è
stato scritto**. Decisione del 21/09: il lavoro (sia modalità manuale sia
browser automation) è sospeso finché non si chiariscono i Termini di
Servizio sull'automazione dell'accesso al dashboard.

Cosa esisterebbe, quando sbloccato (Phase G, mai iniziata):
`Helium10Provider`, `Helium10BrowserProvider`, `Helium10ManualProvider` —
API opzionale, browser opzionale, manuale opzionale, secondo il documento
di refactor originale. `ProviderMode` (`app/models/enums.py`) esiste già
come vocabolario validato per questo (`api`/`browser`/`manual`/`disabled`)
ma non è ancora letto da nessun provider — riservato a quando Phase G
riparte.

Conseguenza concreta oggi: con Helium10 sospeso, **nessun provider con dati
di mercato Amazon reali è disponibile**. Il sistema resta in mock mode per
quella parte finché non si sblocca almeno un provider reale.
