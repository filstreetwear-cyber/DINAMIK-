# Milestone "Anello 1" — prompt per Claude Code sul server OVH (2026-10-14)

Da incollare in Claude Code **lanciato sul server OVH** (non in una sessione
cloud: dal sandbox cloud `www.amazon.it` risponde 403 di policy, verificato
anche il 14/10). Prerequisito: leggere prima
`docs/architecture/fase-roadmap-status.md`.

---

## Prompt

Lavori sul repo `filstreetwear-cyber/DINAMIK-`, branch
`claude/dinamik-advisor-scout-mvp-7no1zb`. Obiettivo di questa milestone,
e nient'altro: chiudere il primo anello reale

**Amazon.it reale → dati verificati → PostgreSQL → opportunità documentata con
evidenze → prospect esportabile in .xlsx**

Fuori scope (non toccarli): Ollama/AI locale, AIAdvisor/OutreachWriter,
health-check Fase 7, Keepa/Helium10, nuovi agenti.

### Topologia (decisa il 14/10)

- **PC di casa (connessione residenziale)** = solo scraper. Un solo PC alla
  volta: più PC sulla stessa linea hanno lo stesso IP, quindi non aumentano
  la capacità, aumentano solo il rischio di blocco.
- **Server OVH** = PostgreSQL, API, export, analisi. Lo scraper non gira mai
  da qui.
- Collegamento PC → Postgres OVH tramite rete privata (Tailscale, piano
  gratuito, oppure tunnel SSH). La porta 5432 di Postgres **non** va mai
  esposta su internet.
- Lo Step 0 va eseguito su entrambe le macchine.

### Step 0 — Verifica stato, senza sovrascrivere nulla

1. Se sul server esiste già una copia del progetto: `git status`, `git log -5`,
   confronto con `origin/claude/dinamik-advisor-scout-mvp-7no1zb`. Se ci sono
   modifiche locali non committate o commit non presenti sul branch,
   **fermati e riportali** prima di fare pull/merge.
2. Inventario: versione Python (serve >=3.12), Postgres attivo (`pg_isready`),
   Node/`lighthouse`, `ollama` presente o no, RAM/disco liberi. Solo report.
3. `pip install -e ".[dev]"`, `alembic upgrade head`, `pytest -q`.
   Baseline attesa: 345 passed / 2 skipped con Lighthouse installato
   (344/3 senza). Qualsiasi altro numero va spiegato prima di procedere.

### Step 1 — Estrazione venditore (amzpy NON basta: già verificato)

`amzpy.parser` non estrae nome/ID venditore (verificato leggendo il sorgente,
Addendum 24 di `docs/architecture.md`). Non ripetere il test "amzpy estrae il
seller?": la risposta è no. Invece:

1. Nuovo `ScrapingAdapter` (stessa interfaccia di `AmzPyAdapter` in
   `app/tools/amazon_scraping/`) che scarica la pagina prodotto `/dp/{ASIN}`
   (Playwright già presente, Chromium headless, profilo persistente) ed
   estrae dalla stessa pagina:
   - venditore: `#sellerProfileTriggerId` (nome + `seller=` ID dall'href),
     fallback `#merchant-info` / blocco "Venduto da" del buybox;
     "Amazon" come venditore va riconosciuto e marcato, non scartato in silenzio;
   - brand: `#bylineInfo`;
   - rating, numero recensioni, prezzo, BSR;
   - qualità scheda (per `AmazonListing`): numero bullet, lunghezza titolo,
     numero immagini, presenza video, presenza A+ (`#aplus`).
2. Per ogni seller ID trovato, pagina `/sp?seller={ID}`: ragione sociale,
   indirizzo, P.IVA quando presenti (obbligo informativo UE). È il dato che
   trasforma un "seller name" in un'azienda contattabile.
3. Salva **sempre** l'HTML grezzo di ogni pagina come evidenza (path + URL +
   timestamp), così ogni campo è verificabile a posteriori. Salva anche gli
   URL di tutte le immagini della galleria e il valore del BSR con data
   (ogni nuova scansione aggiunge una riga: è l'inizio dello storico).
   **Screenshot** (Playwright) della scheda come la vede il cliente:
   parte visibile senza scroll + immagine principale, **solo per i prospect
   target** (sede in Italia/UE), non per tutti gli ASIN. Salvati con
   lo stesso legame all'`Evidence` della pagina. Servono per la revisione
   visiva successiva (umana o con Claude), non vengono analizzati in
   automatico in questa milestone.
4. Disciplina di accesso: 1 richiesta ogni 8–15 s randomizzati, massimo
   ~200 pagine/giorno, rilevamento CAPTCHA/robot-check → stop del batch e
   log, mai retry aggressivi. Niente proxy a pagamento in questa milestone.
5. **Gate di validazione** su 20–30 ASIN reali. Nicchie decise (14/10),
   scelte come proxy di un acquirente 30–60 anni (Amazon non espone dati
   demografici degli acquirenti, quindi l'età non è filtrabile: si sceglie
   la categoria). Primo campione: **Casa e cucina** (organizzazione casa,
   pulizia, piccoli accessori cucina). Nicchie successive, una alla volta:
   Giardino, Fai da te, Cura della persona/benessere, Prodotti per animali.
   Punto di partenza: Bestseller e "Novità" della sottocategoria, pagine
   2–5 (le prime posizioni sono quasi tutte Amazon o grandi brand).
   produrre `reports/anello1/validation.md` con tabella ASIN → seller
   estratto → brand → esito; controllare a mano almeno 10 righe aprendo le
   pagine. Soglia per proseguire: ≥80% seller corretti e <10% CAPTCHA.
   Se non passa, fermati e riporta numeri e HTML di esempio — non
   "aggiustare" i parser finché i numeri tornano.
6. **Filtro prospect**: dalla pagina seller ricavare il paese dell'indirizzo
   aziendale. Prospect = sede in Italia (poi UE). In Casa e cucina la
   maggioranza dei seller è probabilmente extra-UE: vanno salvati ma marcati
   `non_target`, non cancellati, così si misura la resa reale della nicchia.
7. **CAPTCHA**: nessun tentativo di risolverlo o aggirarlo (né OCR, né
   modelli di visione, né servizi esterni). Un CAPTCHA è il segnale che il
   ritmo è troppo alto: stop del batch, pausa di almeno 24 h, ritmo dimezzato.
8. **Da dove gira lo scraper**: preferire una macchina su connessione
   residenziale/ufficio (PC di Frank o un mini-PC acceso in ufficio) che
   scrive nel Postgres del server OVH via tunnel SSH. Gli IP dei datacenter
   OVH vengono bloccati da Amazon molto prima di quelli residenziali.
   Niente proxy gratuiti (inaffidabili e a rischio sicurezza) né Tor.

`SELLER_DATA` va dichiarata supportata dal provider solo dopo che il gate passa.

### Step 2 — Ingestion in PostgreSQL

Chiudere il gap documentato di Fase 2: i risultati scrapati scrivono
`Brand` / `AmazonSeller` / `AmazonProduct` / `AmazonListing` + righe
`Evidence` (fonte, URL, timestamp, path HTML), riusando
`brand_resolution` e `brand_deduplication` esistenti (niente nuove regole di
merge). Idempotente: rilanciare lo stesso batch non crea duplicati
(aggiorna `last_seen_at`). Test con HTML reale salvato come fixture.

### Step 3 — Opportunità documentata

1. Lanciare l'`opportunity_engine` esistente sui dati reali
   (`AMAZON_OPPORTUNITY`, `BUSINESS_VALUE`).
2. Implementare `LISTING_OPPORTUNITY` (oggi "non implementabile" solo perché
   nessuno scriveva `AmazonListing` — lo Step 1 lo sblocca): regole
   deterministiche e configurabili in `config/default.yaml` (es. <5 immagini,
   nessun video, nessun A+, <5 bullet, titolo troppo corto/lungo), ogni
   motivo collegato alla riga `Evidence` che lo prova. Nessuna AI qui.
   È l'opportunità che si vende a un cliente; rating/recensioni basse da
   sole non lo sono.
3. Criterio di successo della milestone: **almeno un brand reale** con
   opportunità MEDIUM/HIGH i cui motivi sono tutti verificabili aprendo
   l'evidenza salvata.

### Step 4 — Prospect esportabile

`dinamik export brands` esistente, esteso con: ragione sociale/P.IVA/
indirizzo dalla pagina seller, motivi dell'opportunità, link alle evidenze.
Consegna: `.xlsx` con i prospect del campione, ordinati per score.

### Dopo questa milestone (non ora): funzioni "stile Helium10" costruite in casa

Non si copia né si fa reverse engineering di Helium10: si ricostruiscono
solo le due funzioni che servono allo Scout, con dati propri.
- **Storico**: riscansione settimanale degli ASIN dei prospect target →
  andamento di BSR, prezzo, recensioni nel tempo (un mini-Keepa nostro).
- **Stima vendite da BSR**: curva per categoria, usata solo per fasce
  (piccolo / medio / grande venditore), mai come numero preciso nei report.

### Componenti esterni da valutare (14/10) — non riscrivere ciò che esiste

- **VIES** (Commissione UE, `ec.europa.eu/taxation_customs/vies/rest-api`):
  validare la P.IVA trovata sulla pagina seller e recuperare ragione sociale
  e indirizzo ufficiali. Verificare prima gratuità, limiti e copertura dei
  dati per l'Italia. Serve anche come chiave di deduplica forte (stessa
  P.IVA = stessa azienda).
- **rapidfuzz**: somiglianza tra nomi brand/seller per i casi senza P.IVA,
  dentro `brand_deduplication`, senza cambiare le regole di merge esistenti.
- **Crawlee for Python** (Apify, open source): coda, retry e limite di
  ritmo per lo scraper. Valutare solo se il codice di coda scritto a mano
  diventa più complesso di questo; non è un obbligo.
- Nessun repo open source trovato che estragga venditore da Amazon.it:
  il parser seller/listing va scritto da noi.

### Regole di lavoro

- Test e `ruff check .` verdi prima di ogni commit; `mypy app tests` non
  deve superare i 3 errori pre-esistenti noti.
- Ogni step chiuso = un commit + un addendum in `docs/architecture.md`
  con i numeri reali (successo estrazione, CAPTCHA, brand trovati).
- Mai inventare un campo: se un dato non è nella pagina, resta `NULL`.
- Nessun invio di messaggi verso nessuno.
