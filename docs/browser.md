# Browser Automation Engine

Riferimento per `app/tools/browser/` (Phase F del refactor) e per il
container `browser-worker` che lo esegue in produzione (Phase J, vedi
`docs/deployment.md`).

## Cosa fa, cosa non fa

`BrowserAutomationEngine` è il **meccanismo di acquisizione**, non una
fonte dati — non sa nulla di Helium10, Amazon, o di qualsiasi target
specifico. Implementa un piccolo set generico di `task_type`; un provider
che ha bisogno di automazione browser (oggi: `WebsiteIntelligenceProvider`;
domani, se sbloccato: un `Helium10BrowserProvider`) fornisce i propri
url/selettori come parametri.

Due `task_type` implementati oggi, scelti perché sono esattamente ciò che
servono al fallback JS/browser di Website Intelligence (Phase E) e al
requisito "serve un umano" (Phase G, sospesa):

- **`fetch_rendered_html`** — naviga, aspetta network-idle, ritorna l'HTML
  renderizzato (post-JS). È il fallback browser di Website Intelligence.
- **`check_requires_login`** — naviga, cerca un campo password. Proxy
  generico per "questa pagina richiede un umano" — **non tenta di
  autenticarsi**, non compila nulla, non risolve captcha. Un provider che
  ha bisogno di un vero flusso di login (un futuro
  `Helium10BrowserProvider`) guida quel flusso da sé usando la sessione che
  questo engine apre; il compito dell'engine finisce a "sì, serve un
  umano qui".

`WAITING_FOR_HUMAN` (vedi `BrowserTaskStatus`) non viene mai convertito in
un retry-fino-al-successo da questo engine — una chiamata, un esito.
Ritentare è una decisione di orchestrazione che appartiene a un livello
superiore, non a questo.

## Le classi

- `BrowserTaskSpec` (frozen dataclass): `provider`, `task_type`, `params`,
  `capability` opzionale — cosa si chiede.
- `BrowserTaskOutcome`: `status`, `output`, `error`, `requires_human`,
  `duration_ms` — cosa è successo.
- `BrowserSessionHandle`: handle live a una directory di profilo browser
  persistente (cookie/login salvati), namespaced per `provider` +
  `account_label` sotto `settings.browser_profiles_dir` — così due account
  per lo stesso provider non condividono mai stato di login.
- **Non** le stesse classi di `app.models.browser.BrowserSession`/
  `BrowserTask` (la riga DB persistita, Phase B) — uno spec è cosa si
  chiede, un outcome è cosa è successo, la riga DB è il record durevole di
  entrambi. Scriverla è lavoro di service layer, non dell'engine.

## Motore reale vs mock

`build_browser_engine(settings=None)` è l'unico punto che decide:

```
MOCK_MODE=true                       → MockBrowserAutomationEngine
MOCK_MODE=false + binario assente    → MockBrowserAutomationEngine (fallback, non crash)
MOCK_MODE=false + binario presente   → PlaywrightBrowserAutomationEngine
```

Un binario Chromium mancante fuori da `MOCK_MODE` **non fa crashare** un
intero research job per un browser mancante — degrada a mock. Ogni
provider/servizio dovrebbe passare da qui, mai istanziare
`PlaywrightBrowserAutomationEngine` direttamente (eccetto i test che
verificano l'engine stesso in isolamento).

### Path dell'eseguibile Chromium

`PlaywrightBrowserAutomationEngine` non lascia che Playwright risolva da
solo il binario — riceve un `executable_path` esplicito. Il default
storico (`_DEFAULT_EXECUTABLE_PATH = "/opt/pw-browsers/chromium"`) è la
convenzione del sandbox di sviluppo di questo progetto (un symlink
stabile), non qualcosa che `playwright install` crea automaticamente su
una VPS pulita.

`BROWSER_EXECUTABLE_PATH` (env var, `app/core/config.py`, aggiunta in
Phase J) sovrascrive quel default quando impostata:
`build_browser_engine()`/`browser_engine_available()` la leggono via
`_resolve_executable_path(settings)`. Non impostata, il comportamento è
identico a prima — l'immagine Docker `browser-worker` crea comunque un
symlink allo stesso path convenzionale (`Dockerfile.browser-worker`), quindi
in pratica non serve valorizzarla lì; esiste come via di fuga per quando
smette di essere vero (un'immagine base diversa, un layout d'installazione
di Playwright diverso).

## Il container `browser-worker`

`app/workers/browser_worker.py` è il processo che gira nel container
dedicato. **Non è ancora un consumer di task** — non esiste una coda di
righe `BrowserTask` da cui pescare (serve Milestone 14: Workers +
scheduler, non costruita). Quello che fa: al boot esegue un vero smoke
test dell'engine (`fetch_rendered_html` su `about:blank`, zero rete
richiesta) attraverso lo stesso `build_browser_engine()` usato da ogni
provider, e lo serve su `GET /health` — provando che il toolchain browser
del container e il volume `BROWSER_PROFILES_DIR` funzionano davvero, non
solo che il processo è in esecuzione. Milestone 14 è dove questo file
diventa un vero consumer.

## Testare

- `tests/test_browser_mock.py` — `MockBrowserAutomationEngine` (ovunque,
  nessun browser richiesto) + engine reale (skip automatico se il binario
  Chromium pre-installato non è presente).
- `tests/test_browser_worker.py` — health server, selezione mock/reale
  onesta, un vero lancio Chromium attraverso il path di codice del
  browser-worker.
- `tests/conftest.py` — fixture autouse che isola `BROWSER_PROFILES_DIR` su
  una directory temporanea per ogni test, per non lasciare directory reali
  nel working tree come effetto collaterale dei test.
