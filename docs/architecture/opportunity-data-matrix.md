# Opportunity Data Matrix — Milestone 8.5

Una vista sola per la domanda che altrimenti va ricostruita da tre file
diversi (`opportunity_engine.py`, `config/default.yaml`,
`docs/architecture/milestone-8.5-audit.md`): per ciascuno dei 6
`OpportunityType`, cosa serve, cosa esiste, e con quale Data Contract
(`app/schemas/contracts.py`, Milestone 8.5) verrebbe rappresentato se/quando
il dato ci sarà.

| OpportunityType | Detector | Data Contract | Provider/dato richiesto | Stato |
|---|---|---|---|---|
| `AMAZON_OPPORTUNITY` | `opportunity_engine.evaluate_amazon_opportunity()` | `AmazonProductContract` | `AmazonProduct` (rating, review_count) — popolato da Milestone 5+6 | **Implementato** |
| `BUSINESS_VALUE` | `opportunity_engine.evaluate_business_value()` | `AmazonProductContract` | `AmazonProduct` + `AmazonSeller` (conteggio prodotti/seller, volume recensioni) | **Implementato** |
| `LISTING_OPPORTUNITY` | — (nessuno) | `AmazonListingContract` | `AmazonListing` — tabella esiste, **nessun codice la scrive mai** (nessun agente acquisisce contenuto di listing: bullet/immagini/A+) | Non implementabile — manca l'acquisizione, non solo il detector |
| `ADVERTISING_OPPORTUNITY` | — (nessuno) | `AdvertisingDataContract` (shape only, nessuna factory — vedi il file stesso) | `Capability.PPC_DATA` — mai chiamata da nessun agente, nessun provider la implementa | Non implementabile — manca provider e agente |
| `WEBSITE_OPPORTUNITY` | — (nessuno) | `WebsiteDataContract` | `WebsiteIntelligenceProvider` (**reale**, non mock) — ma `Brand.website` resta sempre `NULL` da Milestone 5 in poi, quindi non è mai invocato su un brand scoperto | Non implementabile — il provider esiste, manca solo il collegamento nella pipeline di discovery |
| `SELLER_FAST_FIT` | — (nessuno) | `SellerFastFitContract` (shape only, nessuna factory) | Criteri di business mai specificati da Frank (confermato: refactor doc originale, `docs/architecture.md`, note personali di Fil — Seller Fast/sellerfast.tech è un prodotto separato di Frank) | **NOT FORMALLY SPECIFIED** — non un gap tecnico |

## Nota sulla differenza tra i tre "non implementabile"

Vale la pena distinguerli, perché richiedono lavoro diverso per essere sbloccati:

- **`LISTING_OPPORTUNITY`**: serve costruire l'acquisizione (un provider o
  un'estensione di uno esistente che scriva `AmazonListing`) — lavoro di
  provider, non di detector. Il detector stesso, una volta che il dato
  esiste, è probabilmente semplice (regole su bullet_points vuoti,
  assenza di A+ content, pochi video/immagini — simile per forma a
  `evaluate_amazon_opportunity`).
- **`ADVERTISING_OPPORTUNITY`**: serve sia un provider PPC sia
  un'estensione della capability routing — il gap più ampio dei tre,
  nessun pezzo esiste oggi.
- **`WEBSITE_OPPORTUNITY`**: il provider esiste già ed è reale
  (`WebsiteIntelligenceProvider`, nessuna credenziale richiesta) — manca
  solo che Amazon Scout discovery (o un futuro step) recuperi l'URL del
  sito del brand da qualche parte e chiami `Capability.WEBSITE_DATA` su
  quell'URL. Il più economico dei tre da sbloccare, se mai richiesto.

`SELLER_FAST_FIT` è categoricamente diverso dagli altri tre: non manca un
pezzo tecnico, manca la specifica stessa. Costruire un detector per questo
tipo oggi significherebbe indovinare i criteri di business di Frank — la
cosa esplicitamente vietata da Milestone 8.5 e da ogni istruzione precedente
di questo progetto su questo punto.
