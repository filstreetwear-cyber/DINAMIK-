# Database — Milestone 2

## Setup locale

```bash
# richiede un server PostgreSQL 16 raggiungibile
psql -c "CREATE USER dinamik WITH PASSWORD 'dinamik';"
psql -c "CREATE DATABASE dinamik_amazon_intelligence OWNER dinamik;"
alembic upgrade head
```

`DATABASE_URL` in `.env` deve puntare a questo database (vedi `.env.example`).

## Schema

22 tabelle, corrispondenti 1:1 a quelle elencate nell'architettura approvata
(`docs/architecture.md`, sezione 3), applicate e verificate:

`companies, brands, marketplaces, categories, amazon_sellers, amazon_products,
amazon_listings, research_jobs, research_results, competitors, keywords,
website_profiles, opportunities, scores, evidence, contacts, prospect_status,
auditor_packages, agent_runs, agent_events, tool_calls, errors`

## Decisioni di design (rilevanti per chi estende lo schema dopo)

- **Primary key**: UUID generati lato applicazione (`uuid.uuid4`), non
  `SERIAL`. Il sistema scriverà da più worker asincroni in parallelo — gli
  UUID evitano il problema di coordinamento che le sequenze intere
  introdurrebbero.
- **Vocabolari di stato** (status dei job, tipo di evidenza, categorie di
  errore, ecc.) sono colonne `String` validate a livello applicativo da
  classi `enum.Enum` (`app/models/enums.py`), **non** tipi `ENUM` nativi di
  Postgres. Aggiungere un valore (come è successo con `PERPETUA` al posto
  di `SELLICS` durante la revisione architetturale) non richiede una
  migration `ALTER TYPE`.
- **Evidence è polimorfica non vincolata**: `entity_type` + `entity_id` non
  sono una foreign key reale, per lo stesso motivo per cui la sezione 14
  della spec la descrive in modo generico — un'evidenza può attaccarsi a
  company, brand, prodotto, opportunity o score senza aggiungere una nuova
  colonna nullable ad ogni nuova entità. Il controllo che `entity_id`
  esista davvero appartiene al service layer (Milestone 3+), non a un
  vincolo di database.
- **AmazonProduct vs AmazonListing**: `AmazonProduct` è l'entità
  deduplicata canonica (vincolo unique su `asin` + `marketplace`).
  `AmazonListing` è uno snapshot puntuale del contenuto (titolo, bullet,
  A+, immagini) — è quello che permette in futuro un confronto
  BEFORE/AFTER (sezione 6 della spec) e che rende misurabile la
  `data_freshness` (sezione 65): un prodotto non "invecchia",
  un'osservazione di listing sì.
- **AgentRun / AgentEvent / ToolCall / errors sono quattro granularità
  distinte**, non una sola tabella di log: run complessiva (costo/modello),
  eventi interni fine-grained per debug, chiamate a servizi esterni
  (mai con secret salvati), errori classificati e opzionalmente collegati
  a job/run/tool_call.

## Verifica eseguita (non solo dichiarata)

- `alembic upgrade head` / `alembic downgrade base` / `alembic upgrade head`
  — la migration è reversibile e riapplicabile.
- `alembic check` — zero differenze tra modelli SQLAlchemy e schema
  applicato.
- Test di integrazione reali (`tests/test_database_integration.py`) contro
  Postgres vero: relazione Company↔Brand con round-trip di lettura, e
  verifica che il vincolo unique `(asin, marketplace)` blocchi un duplicato
  ma permetta lo stesso ASIN su marketplace diversi.
