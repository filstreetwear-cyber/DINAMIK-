# Browser abstraction — Playwright MCP e Browser Use, valutazione reale (Milestone 8.5)

Non è una valutazione a tavolino: entrambi i pacchetti sono stati
**installati per davvero** e fatti girare contro il binario Chromium reale
di questo sandbox (`/opt/pw-browsers/chromium`, lo stesso che
`PlaywrightBrowserAutomationEngine` già usa) — non fixture, non
documentazione letta e basta. Ambienti isolati (`/tmp/m85-eval/`, fuori dal
repo, non committati — nessuno dei due è una dipendenza del progetto),
rimossi al termine della valutazione.

## Playwright MCP (`@playwright/mcp`, Microsoft)

**Installato**: `npm install @playwright/mcp@latest` → versione `0.0.82`
(server MCP `1.64.0-alpha`), pulito, zero errori.

**Smoke test reale**: avviato il server in stdio (`--headless --isolated
--executable-path /opt/pw-browsers/chromium --no-sandbox`), eseguito
l'handshake MCP completo:

1. `initialize` → risposta valida (`protocolVersion: 2024-11-05`, `serverInfo: Playwright 1.64.0-alpha`)
2. `tools/list` → **25 tool reali** esposti: `browser_navigate`, `browser_click`, `browser_type`, `browser_snapshot`, `browser_take_screenshot`, `browser_evaluate`, `browser_run_code_unsafe`, `browser_network_requests`, `browser_fill_form`, ecc.
3. `tools/call` su `browser_navigate` con `url: about:blank` → risposta reale con snapshot pagina, non un errore o un timeout.

**Esito: funziona al 100% in questo sandbox.** [Certain]

**Valutazione tecnica, non solo "funziona"**: Playwright MCP è pensato per
un consumer completamente diverso da quello che questo progetto ha oggi. I
suoi 25 tool sono azioni granulari e libere (inclusa `browser_run_code_unsafe`,
esecuzione di JS arbitrario) pensate per un agente AI che guida il browser
in modo interattivo, tool call dopo tool call — non per una pipeline
deterministica che scrive Evidence rows con provenance tracciata.
`BrowserAutomationEngine` di questo progetto fa deliberatamente l'opposto:
espone solo 2 `task_type` generici, chiusi, senza esecuzione di codice
arbitrario, proprio perché il chiamante è un provider automatico, non un
umano o un AI che decide azione per azione.

**Decisione: INTEGRATE_OPTIONAL, non ora.** Il fit tecnico esiste solo per
un consumer che oggi non esiste — un layer AI interattivo (Milestone 10) o
uno strumento di browsing assistito per un analista umano. Aggiungerlo come
dipendenza runtime oggi significherebbe introdurre 25 tool a esecuzione
libera (incluso JS arbitrario) senza nessun chiamante che li usi e senza il
livello di revisione che un'esecuzione-di-codice-arbitrario da parte di un
AI meriterebbe in un sistema che scrive su un DB di produzione. Non è un
rifiuto per limiti tecnici — è un rifiuto per assenza di un consumer e di
un modello di sicurezza adeguato *oggi*. Da riconsiderare quando Milestone
10 (AI layer) è in scope.

## Browser Use (`browser-use`, PyPI)

**Installato**: venv isolato, `pip install browser-use` → versione
`0.13.10`, pulito. Nota architetturale rilevante: dalla 0.13 in poi
**non usa più Playwright** come motore — dipende da `browser-harness` e
`cdp-use` (client CDP proprio). Questo cambia la valutazione: non è "un
motore Playwright alternativo", è un motore CDP indipendente con una sua
astrazione (`browser_use.actor.page.Page`), incompatibile 1:1 con
`BrowserTaskOutcome`/`BrowserSessionHandle` di questo progetto senza un
adapter scritto da zero.

**Smoke test reale**: istanziata `Browser(executable_path=..., headless=True,
enable_default_extensions=False)` (il livello basso, senza passare per la
classe `Agent` che richiede un LLM e consumerebbe una chiamata API reale non
necessaria per provare che il motore browser funziona), navigato a un
`data:` URL locale, letto titolo/URL con `page.get_title()`/`page.get_url()`:

```
SMOKE TEST RESULT: SUCCESS — url='data:text/html,<title>Dinamik Milestone 8.5 smoke test</title><h1>ok</h1>' title='Dinamik Milestone 8.5 smoke test'
```

**Esito: funziona.** [Certain] Nota onesta: un primo tentativo verso
`https://example.com` è fallito silenziosamente (titolo rimasto sul
placeholder interno "Starting agent...") — non un bug di Browser Use, ma
il proxy di rete di questo sandbox che rifiuta `example.com` (`curl` verso
lo stesso host: `403` sul CONNECT). Confermato non essere un limite del
pacchetto, testando poi con un `data:` URL locale (nessuna rete richiesta).

**Valutazione tecnica**: il caso d'uso per cui Browser Use è davvero
disegnato — un `Agent` guidato da un LLM che pianifica azioni multi-step
autonome (login, navigazione, estrazione) — è esattamente la forma del
task che un futuro `Helium10BrowserProvider` avrebbe bisogno (login alla
dashboard, navigazione, estrazione dati). È l'unico dei due pacchetti con
un fit architetturale concreto su un caso reale di questo progetto.

**Decisione: EVALUATE_LATER, con trigger esplicito.** Non `REJECT` — il fit
tecnico è reale e specifico — ma non `INTEGRATE` ora, per due motivi
indipendenti dal fit tecnico: (1) Frank ha sospeso qualunque lavoro
Helium10 (manuale o browser) il 21/09 in attesa di chiarire i Termini di
Servizio — non esiste oggi un consumer autorizzato a usarlo; (2) la
dipendenza è pesante (trascina SDK di Anthropic/OpenAI/Google/Groq/Ollama,
`browser-harness`, `cdp-use`, `browser-use-sdk` — non una libreria
leggera) e, non usando Playwright, richiederebbe un adapter dedicato per
rientrare nel contratto `BrowserAutomationEngine`/`BrowserTaskOutcome`
esistente, invece di essere un drop-in. **Trigger per rivalutare**: quando
Frank sblocca Phase G (Helium10), non prima — a quel punto la domanda
diventa "Browser Use vs. guidare `PlaywrightBrowserAutomationEngine` a mano
per il login flow", una scelta di implementazione, non più di principio.

## Cosa NON cambia nel codice del progetto

Nessuna delle due valutazioni tocca `app/tools/browser/` o
`pyproject.toml`/`package.json` — entrambe restano fuori dalle dipendenze
runtime del progetto, per design, non per omissione. `BrowserAutomationEngine`
resta l'unica astrazione browser in uso oggi.
