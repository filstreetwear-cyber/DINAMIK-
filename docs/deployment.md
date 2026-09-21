# Deployment — Docker/VPS

Riferimento per `docker-compose.yml`, `Dockerfile`, `Dockerfile.browser-worker`
(Phase J del refactor). Per il ragionamento dietro le scelte, vedi
`docs/architecture.md` addendum (6) — questo documento è la procedura, non
la cronologia delle decisioni.

## Limite onesto, prima di tutto

Queste immagini **non sono mai state buildate o eseguite per davvero** nel
sandbox di sviluppo in cui sono state scritte — `docker` (client) era
presente ma senza demone attivo. Quello che è stato verificato per davvero:
`docker compose config` (validazione strutturale — risoluzione
`env_file`+`environment`, `depends_on`, volumi, porte), `shellcheck` pulito
sullo script di entrypoint, e tutta la logica applicativa nuova via
`pytest` reale (incluso un lancio vero di Chromium attraverso il codice del
browser-worker). **Il primo `docker compose up --build` reale va fatto
sulla VPS assegnata al progetto** — trattalo come il primo test end-to-end
di questa fase, non come una formalità.

## Servizi

| Servizio | Immagine | Porta | Healthcheck | Volume |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | interna | `pg_isready` | `postgres_data` |
| `redis` | `redis:7-alpine` | interna | `redis-cli ping` | `redis_data` |
| `api` | `Dockerfile` | `8000` | `GET /health` | — |
| `browser-worker` | `Dockerfile.browser-worker` | `8100` | `GET /health` | `browser_profiles` |

Nessun servizio `worker`/celery — `app/workers/celery_app.py` non esiste
ancora (Milestone 14). Aggiungerlo ora, contro un modulo che non esiste,
avrebbe fatto sembrare finita una fase che non lo è.

## Come fare il deploy

```bash
cp .env.example .env
# valorizza le credenziali reali (AI, Keepa se/quando disponibile,
# HELIUM10_API_KEY quando Phase G riparte) prima del deploy — .env non
# entra mai nell'immagine (vedi .dockerignore), viene iniettato a runtime

docker compose up --build -d
docker compose ps          # tutti healthy?
curl http://localhost:8000/health
curl http://localhost:8100/health
```

`api` esegue `alembic upgrade head` al boot (`scripts/docker-entrypoint-api.sh`,
con retry fino a 30 tentativi da 2s per la finestra tra "Postgres accetta
TCP" e "Postgres ha finito l'inizializzazione al primo avvio") prima di
servire l'app — su ogni deploy, non solo il primo.

## Perché due immagini separate

`Dockerfile` (api) non installa Chromium — bloatare ogni deploy del
servizio principale con un browser che non lancia mai sarebbe stato uno
spreco. `Dockerfile.browser-worker` è la sola immagine con Chromium reale
(`playwright install --with-deps chromium`, poi un symlink stabile a
`/opt/pw-browsers/chromium` — stessa convenzione già usata dall'engine, vedi
`docs/browser.md`).

Entrambe usano **installazione editable** (`pip install -e .`), non un
wheel buildato: `app/core/config.py` e i fixture dei provider mock
risolvono `config/`/`fixtures/` relativi alla root del repo
(`Path(__file__).resolve().parents[N]`), non un path di dati impacchettato
— un `pip install .` avrebbe fatto avviare il container e poi fallire a
runtime non trovando `config/`.

## Variabili d'ambiente per servizio

`api` riceve `.env` per intero (`env_file:`) più override di rete per
`DATABASE_URL`/`REDIS_URL` (puntano agli hostname compose `postgres`/
`redis`, non `localhost`).

`browser-worker` **non** riceve `.env` per intero — solo `MOCK_MODE`,
`ENVIRONMENT`, `LOG_LEVEL`, `BROWSER_HEADLESS`, più
`BROWSER_PROFILES_DIR=/data/browser_profiles` (il path montato, non il
default relativo di `.env`). Scelta deliberata: è un container che guida un
browser reale ed è raggiungibile sulla propria porta — non ha motivo di
ricevere `ANTHROPIC_API_KEY`/`AMAZON_API_SECRET`/`HELIUM10_API_KEY` solo
perché sono nello stesso file `.env`.

## Volumi — cosa persiste e perché

- `postgres_data` — dati applicativi, ovvio.
- `redis_data` — persistenza opzionale della coda (rilevante da Milestone 14).
- `browser_profiles` — profili browser persistenti (cookie/stato di login).
  **Questo è il volume che conta per Helium10, quando Phase G riparte**: un
  login umano fatto una volta sopravvive a un redeploy del container,
  invece di richiedere un nuovo login a ogni riavvio.

## Troubleshooting

- `api` non diventa healthy → controlla i log delle migration
  (`docker compose logs api`); il retry loop nell'entrypoint fallisce
  rumorosamente dopo 30 tentativi, non silenziosamente.
- `browser-worker` risponde `"status": "unhealthy"` su `/health` →
  guarda il campo `smoke_test.error` nella risposta; se `engine_mode` è
  `"mock"` mentre ti aspettavi `"real"`, `MOCK_MODE` è probabilmente
  ancora `true` nell'ambiente del container.
- Un vero build fallito su Chromium/`playwright install --with-deps` è
  esattamente il tipo di problema che questo sandbox non poteva
  intercettare in anticipo (vedi il limite onesto sopra) — primo posto da
  guardare sulla VPS.
