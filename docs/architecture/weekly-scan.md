# Weekly Intelligence Scan

## Perché esiste

Milestone 14 (Workers + scheduler) ha lasciato un gap dichiarato
esplicitamente nel proprio report: `scheduler.enabled` resta `false` di
default perché nessuno aveva ancora specificato COSA una scansione
ricorrente dovesse cercare — una decisione di business mancante, non un
bug. Questa feature è la risposta diretta a quella domanda: una spec
completa (46 sezioni) che descrive un motore di rilevamento cambiamenti
settimanale, autonomo, a costo AI zero obbligatorio, costruito sopra
l'architettura esistente (`AgentRegistry`, Evidence Layer, Data
Contracts, Provider abstractions, `AIGateway`/Ollama/Qwen3 1.7B) senza
riscriverla.

**Principio guida, ripetuto in ogni sezione della spec originale**: "Cosa
è cambiato dalla settimana scorsa, quali evidenze lo supportano, quali
cambiamenti creano opportunità reali, cosa merita attenzione ora" — non
"cosa pensa l'AI questa settimana". Un motore di intelligence guidato da
evidenze e cambiamenti, non un chatbot settimanale.

## Cosa è stato audit-ato prima di scrivere codice

Ogni decisione di scope sotto è ancorata a un fatto verificato nel
codice esistente, non assunto:

- `AgentRegistry` (`app/agents/registry.py`) è un registro di
  introspezione (`register`/`get`/`list`/`find_by_capability`), **non**
  un dispatcher di esecuzione — solo `amazon_scout` è registrato. Lo
  scan orchestrator lo consulta per introspezione ma esegue il lavoro
  reale attraverso `run_research_pipeline` (Milestone 11), lo stesso
  entrypoint già usato da CLI/API/Celery — nessun nuovo meccanismo di
  dispatch inventato.
- `AmazonProduct.price`/`.currency` non sono **mai** popolati da questa
  pipeline — `Capability.PRODUCT_PRICE` non è nella tupla
  `_ENRICHMENT_CAPABILITIES` di `app/agents/amazon_scout/discovery.py`.
  Questo esclude `PRICE_CHANGE` con dati reali, confermato
  indipendentemente anche dal docstring di `opportunity_engine.py`.
- `app/services/brand_resolution.py`'s `_get_or_create_product`
  aggiorna `rating`/`review_count`/`bsr` **in place** a ogni nuova
  osservazione di un `AmazonProduct` esistente — la riga live contiene
  solo l'ultimo valore, senza storia. Questo è il motivo reale per cui
  una tabella di snapshot separata (`EntitySnapshot`) è necessaria per
  un confronto settimana-su-settimana, non una scelta architetturale
  arbitraria.
- Il provider `AmazonProvider` mock (`app/tools/amazon/provider.py`)
  legge un fixture statico (`fixtures/amazon_mock_products.json`) —
  output identico a ogni chiamata. Due run reali in mock mode non
  divergono mai organicamente; testare il change detection richiede
  dati sintetici (uno snapshot "settimana precedente" scritto a mano),
  non solo run ripetuti.
- `ResearchJobStatus` (PENDING/RUNNING/COMPLETED/PARTIAL/FAILED/
  CANCELLED) copre già tutto ciò che serve a `WeeklyScan.status` —
  riusato direttamente, nessun nuovo enum.
- `scoring_engine.py`'s `Score` è già una serie temporale append-only
  per `(brand_id, score_type)`, senza vincolo di unicità — base diretta
  per `classify_opportunity_lifecycle()`, che confronta le due righe
  `Score` più recenti.
- `opportunity_engine.py` implementa realmente solo due dei sei
  `OpportunityType`: `AMAZON` ("AMAZON_OPPORTUNITY") e `BUSINESS_VALUE`.
  Gli altri quattro non hanno detector — riusato come
  `_IMPLEMENTED_OPPORTUNITY_TYPES`.

## Architettura

```
Scheduler (Celery Beat, weekly_scan.day/time/timezone)
        │
        ▼
run_weekly_scan()  (app/services/weekly_scan.py)
        │
        ├─ idempotenza: WeeklyScan.period_end (UNIQUE) — skip se già
        │  COMPLETED per oggi, salvo --force
        │
        ├─ dry_run? → preview (universe_size), NESSUNO stato toccato
        │  (valutato PRIMA del controllo idempotenza — vedi "Bug reale
        │  trovato" sotto)
        │
        ├─ Universo = Brand ACTIVE esistenti (nessuna nuova tabella
        │  ScanUniverse — riuso diretto, stesso universo che
        │  opportunity_engine/scoring_engine/ai_analysis_engine già
        │  iterano)
        │
        ├─ run_research_pipeline()  (Milestone 11, INTATTO)
        │     discovery → brand resolution → dedup → opportunity →
        │     scoring → AI analysis (Ollama/Qwen3 1.7B, locale, o
        │     AI_UNAVAILABLE se irraggiungibile — non blocca lo scan)
        │
        ├─ Per ogni AmazonProduct di ogni Brand ACTIVE:
        │     current_fields = {rating, review_count, bsr}
        │     previous = ultimo EntitySnapshot per quell'entity_id
        │     changes = compute_changes(previous, current, thresholds)
        │     → nuovo EntitySnapshot(scan_id, entity_type, entity_id,
        │       fields, changes, observed_at)
        │
        ├─ Per ogni Brand × {AMAZON, BUSINESS_VALUE}:
        │     ultime 2 righe Score → classify_opportunity_lifecycle()
        │     → NEW / CONTINUING / IMPROVING / DETERIORATING / RESOLVED
        │
        ├─ evidence_created = count(Evidence creata durante questo scan)
        │
        └─ _write_report() → reports/weekly/{period_end}.md
           (mai sovrascrive un report di un'altra data — nome per data)
```

## Cosa è implementato e cosa no (onestamente, non un elenco di scuse)

**Tipi di cambiamento implementati — 3 su ~12 nominati dalla spec**:
`RATING_CHANGE`, `REVIEW_COUNT_CHANGE`, `RANK_CHANGE`. Sono gli unici tre
campi che questa pipeline popola e re-osserva davvero ad ogni run.
`PRICE_CHANGE`/`SELLER_COUNT_CHANGE`/`BUY_BOX_CHANGE`/
`AVAILABILITY_CHANGE`/`LISTING_CHANGE`/`WEBSITE_CHANGE`/
`TECHNOLOGY_CHANGE`/`BUSINESS_CHANGE`/`COMPETITION_CHANGE` non sono
implementati: nessun campo prezzo/seller-count/Buy Box/availability
esiste nello schema con dati reali dietro; nessuna riga `AmazonListing`
è mai stata scritta da questa pipeline; `Brand.website` resta `NULL` dal
Milestone 5, quindi nessun `WebsiteProfile` è mai collegato a un brand
scansionato. Costruire questi change type oggi significherebbe riportare
"cambiato da None a None" ogni settimana — peggio che non riportarli.

**Soglie di significatività**, in `config/default.yaml`'s
`weekly_scan.significance`, ciascuna con motivazione documentata inline
nel file stesso:

| Soglia | Valore | Motivazione |
|---|---|---|
| `rating_change_threshold` | 0.3 | Amazon arrotonda a 0.1 — sotto 0.3 è rumore di arrotondamento, non segnale |
| `review_growth_pct_threshold` | 10.0% | Le review sono cumulative in questa pipeline; solo una crescita a doppia cifra % è segnale reale, non oscillazione |
| `rank_change_pct_threshold` | 20.0% | Il BSR è il campo più rumoroso che Amazon espone — serve una barra più alta |
| `opportunity_score_delta_threshold` | 15.0 | Score 0-100; ~15 punti intercetta un cambio di livello (`_LEVEL_TO_SCORE` di `scoring_engine.py`) |

**Lifecycle delle opportunità — 5 stati su 7**: `NEW`, `CONTINUING`,
`IMPROVING`, `DETERIORATING`, `RESOLVED` sono derivati da delta reali di
`Score`. `DISAPPEARED`/`RECURRING` non sono distinguibili da questo
modello dati senza una finestra di storia più lunga delle due righe
`Score` più recenti — collassarli in RESOLVED/NEW è onesto; inventare
una distinzione fittizia non lo sarebbe.

**AI usage**: nessuna chiamata AI per-entità aggiuntiva in questa
feature — l'unica AI usata è quella che `run_research_pipeline`
(Milestone 10/11) già invoca durante la fase `ai_analysis_engine`
(Ollama/Qwen3 1.7B locale, `AI_UNAVAILABLE` gestito senza bloccare lo
scan). La spec chiedeva AI "solo dove serve interpretazione" — questa
prima implementazione riusa l'AI esistente della pipeline invece di
aggiungere una seconda passata AI specifica per i cambiamenti rilevati;
un budget AI dedicato per-scan (`weekly_scan.max_ai_calls`, priorità
change-driven) resta un gap dichiarato per un'iterazione successiva, non
costruito qui — evitare di dire "fatto" su una parte della spec non
davvero implementata.

## Idempotenza

Vincolo di unicità DB su `WeeklyScan.period_end` (il giorno di calendario
UTC in cui lo scan viene lanciato). Un secondo run non forzato nello
stesso giorno ritorna lo scan `COMPLETED` esistente
(`skipped_existing=True`), senza duplicare nulla. `--force` riusa la
stessa riga (mai una seconda — il vincolo unique la rifiuterebbe),
cancella i suoi `EntitySnapshot` precedenti e rilancia il lavoro.

## Bug reale trovato durante il testing dal vivo (non nella spec, nel mio codice)

Il controllo `dry_run` era originariamente scritto **dopo** il controllo
di idempotenza. Risultato: `run_weekly_scan(dry_run=True)` chiamato lo
stesso giorno di uno scan già `COMPLETED` restituiva silenziosamente il
riepilogo stantio di quello scan (`skipped_existing=True`, `dry_run=
False`) invece di un vero preview a stato zero, come richiesto dalla
spec (sezione 28: "NOT modify persistent state"). Trovato lanciando
manualmente la sequenza reale (run normale → run senza --force → run
--force → run --dry-run) contro Postgres reale, non da un test unitario
scritto a priori. Corretto spostando il branch `dry_run` **prima** del
lookup di idempotenza — un dry run ora è sempre un preview onesto,
indipendentemente da cosa esiste già per quel `period_end`. Coperto da
un test di regressione dedicato
(`test_dry_run_is_honest_preview_even_when_a_completed_scan_already_exists`
in `tests/test_weekly_scan.py`).

## Scheduler

`config/default.yaml`'s `weekly_scan:` section (`enabled: false` di
default, `day: sunday`, `time: "06:00"`, `timezone: "Europe/Rome"`, più
`significance:`) — riusa lo stesso pattern già stabilito da
`Settings.scheduler_config()`/`ai_config()`: la sezione YAML è
autorevole. `app/workers/celery_app.py` aggiunge una seconda entry
`beat_schedule`, indipendente da quella di Milestone 14
(`weekly-research-job`), chiamata `weekly-intelligence-scan`.

**Conversione timezone**: l'app Celery gira con `timezone="UTC",
enable_utc=True` (impostazione globale, unica, condivisa con lo
scheduler di Milestone 14). `celery.schedules.crontab` non supporta un
timezone per singola entry, quindi `_weekly_scan_crontab()` converte
"domenica 06:00 Europe/Rome" nel suo equivalente UTC usando il modulo
standard `zoneinfo` (nessuna nuova dipendenza), una volta, al momento
della costruzione dello schedule (import del modulo).

**Limite noto, documentato, non nascosto**: la conversione avviene una
sola volta all'avvio del processo Beat, non ricalcolata a ogni tick. Un
processo Beat che resta in esecuzione attraverso un cambio di ora legale
(ultima domenica di marzo o ottobre) continua a sparare all'orario UTC
già calcolato finché non viene riavviato — lo scan settimanale può
finire spostato di un'ora rispetto a "06:00 Europe/Rome" fino al
prossimo deploy. Dato che Celery Beat non offre un timezone per-entry
nativo e la spec chiedeva esplicitamente "no large scheduling
framework", questo è il trade-off accettato per un job in background
dove l'ora esatta non è critica — non qualcosa che una correzione ben
scoperta avrebbe silenziosamente nascosto.

**Attivazione, in ordine, mai automatica**: `weekly_scan.enabled` resta
`false` finché un umano non esegue manualmente `dinamik scan weekly` e
conferma che il risultato è corretto (Definition of Done della spec,
sezioni 40/45) — lo scheduler non si attiva mai da solo durante questa
build.

## CLI

`dinamik scan weekly [--dry-run] [--force] [--max-products N]
[--max-batch N]`, `dinamik scan status` (ultimo scan), `dinamik scan
history [--limit N]` — stesso `typer.Typer` sub-app della CLI esistente
(`app/cli.py`), nessun secondo framework CLI.

## Report

`reports/weekly/{period_end:%Y-%m-%d}.md` — markdown leggibile senza
aprire il database: periodo, stato, conteggi, cambiamenti significativi
(bullet, con brand/campo/delta), transizioni di lifecycle delle
opportunità, errori (primi 50), e una sezione "Cost model" esplicita
(vedi sotto). Mai sovrascrive un report di un'altra data — il nome file
è la data del periodo.

## Modello di costo

**Costo AI obbligatorio: €0.** L'unica AI usata (dalla fase
`ai_analysis_engine` della pipeline esistente) è Ollama locale +
`qwen3:1.7b`. Nessuna chiamata automatica a un'API AI a pagamento — se
Ollama non è raggiungibile, lo scan continua deterministicamente e
marca l'analisi come non disponibile invece di fallire l'intero scan o
ripiegare su un provider cloud.

## Isolamento errori

Un fallimento della pipeline (o di una singola entità durante la fase
di change detection) non perde l'intero scan: l'eccezione è catturata,
registrata in `WeeklyScan.errors`, e lo stato finale diventa `PARTIAL`
invece di `COMPLETED` — mai un'eccezione propagata che lascia la riga
`WeeklyScan` bloccata in `RUNNING`.

## Test

`tests/test_weekly_scan.py`, 32 test: unitari puri per
`compute_changes()`/`classify_opportunity_lifecycle()`/
`ChangeThresholds.from_config()`/`_period_bounds()` (nessun DB);
integrazione contro Postgres reale (mock mode) per idempotenza, force,
il bug di dry-run corretto (regressione dedicata), change detection
contro uno snapshot sintetico "settimana precedente" scritto a mano
(il fixture mock è statico — due run reali non divergono mai da soli),
isolamento errori (pipeline mockata per fallire → stato PARTIAL); test
CLI via `CliRunner` reale; un test Celery via `.apply()` più i test
unitari della conversione timezone e del wiring dello scheduler.
