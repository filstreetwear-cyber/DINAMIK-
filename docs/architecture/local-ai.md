# Local AI Infrastructure — Milestone 9.5

## Perché esiste

Milestone 10 (AI layer) implica chiamate AI per-brand con un costo reale
per chiamata. Frank ha chiesto esplicitamente di non "spendere un
patrimonio in API" prima di sapere se il sistema le usa bene — Milestone
9.5 costruisce quindi l'infrastruttura AI **locale, a costo zero
obbligatorio**, prima di qualunque integrazione con un provider cloud a
pagamento. Nessuna chiamata a un'API AI a pagamento è richiesta per far
funzionare DINAMIK con questa milestone.

## Vincoli della VPS target

Per specifica: 2 vCPU, 4GB RAM, 40GB disco, Ubuntu Linux. Questo è un
budget stretto per un LLM — ogni scelta sotto (dimensione modello,
`max_concurrency`, `num_ctx`) è dimensionata per stare dentro questo
limite, non per prestazioni ottimali. Vedi "Limiti" più sotto per cosa
questo significa concretamente.

## Ollama + Qwen3 1.7B

Backend: [Ollama](https://ollama.com), un runtime locale per LLM via
un'API REST minimale (`/api/generate`, `/api/tags`). Modello: **`qwen3:1.7b`
esatto** — non `qwen3:4b`/`8b`/`14b`. L'artefatto del modello (~1.4GB,
quantizzazione Q4_K_M) è la scelta più piccola della famiglia Qwen3 che
resta utilizzabile, l'unica che ha senso su 4GB di RAM condivisi con
Postgres/Redis/l'app stessa.

## Architettura

```
Agente DINAMIK (es. AIAnalyst)
        |
        v
   AIGateway            <- unico punto che gli agenti devono chiamare
        |
        v
   AIRouter              <- decide local/cloud da config/default.yaml "ai:"
        |
        v
  OllamaProvider (AIProvider)   <- unica implementazione concreta di questa milestone
        |
        v
      Ollama  ->  Qwen3 1.7B
```

**Principio critico (sezione 4 della spec)**: nessun punto della logica di
business chiama `ollama.chat()` o `http://localhost:11434` direttamente.
Tutto passa da `AIGateway`. Questo è ciò che rende possibile aggiungere un
secondo provider (cloud) in futuro senza toccare un solo agente esistente
— vedi "Provider cloud futuro" più sotto.

File reali di questa milestone:

- `app/ai/provider.py` — `AIProvider` (ABC), `AIGenerationRequest`/
  `AIGenerationResult`, famiglia di eccezioni `AIProviderError` (deliberatamente
  separata da `ProviderError`/`IntelligenceProvider` in `app/tools/core/` —
  vedi il docstring del modulo per il perché, sezione 23 della spec)
- `app/ai/ollama_provider.py` — `OllamaProvider`, l'unica implementazione
  concreta, via `httpx` (già dipendenza del progetto)
- `app/ai/mock_provider.py` — `MockAIProvider`, per i test unitari senza
  Ollama reale (sezione 29/30 della spec)
- `app/ai/gateway.py` — `AIGateway`, punto di logging/scelta unico
- `app/ai/router.py` — `AIRouter`, risolve `ai.provider` (local/cloud) in
  un `AIProvider` concreto
- `app/ai/bootstrap.py` — `build_default_ai_gateway(settings)`, mirror di
  `build_default_registry()`/`build_default_agent_registry()`
- `app/ai/contracts.py` — `AIClaimDraft` (ciò che il modello produce) e
  `AIClaim` (draft + provenienza system-assigned: id/created_at/model/
  provider — mai auto-dichiarata dal modello)
- `app/ai/analyst.py` — `AIAnalyst`, l'unico agente AI di questa milestone:
  consuma `Evidence` strutturata, produce `AIClaim` strutturate. Non
  produce raccomandazioni commerciali finali.

## Configurazione

`config/default.yaml`, sezione `ai:` (ristrutturata da un `default_model/
max_tokens/temperature` mai letto da nessun codice):

```yaml
ai:
  enabled: true
  provider: local
  local:
    backend: ollama
    base_url: http://localhost:11434
    model: qwen3:1.7b
    timeout_seconds: 60
    max_concurrency: 1
    temperature: 0.2
    num_ctx: 2048
    num_predict: 512
    keep_alive: 5m
  cloud:
    enabled: false
```

`ai.enabled: false` disattiva l'intero layer AI in modo pulito —
`build_default_ai_gateway()` ritorna `None`, ogni chiamante deve gestirlo
(niente crash altrove). `ai.provider: cloud` con nessun provider cloud
implementato solleva `AICloudNotImplementedError` in modo esplicito e
controllato, non silenziosamente ricade su `local`.

## Sicurezza di memoria su una VPS da 4GB

- `max_concurrency: 1` — un `asyncio.Semaphore` in `OllamaProvider`
  serializza le chiamate di generazione. Più contesti caricati in
  parallelo su 4GB è il modo documentato per andare in OOM.
- `num_ctx`/`num_predict` conservativi (2048/512), mai lasciati ai default
  di Ollama (tarati per macchine molto più grandi).
- `keep_alive: 5m` — quanto Ollama tiene il modello in RAM tra una
  chiamata e l'altra, così non ricarica il modello a ogni singola
  richiesta né lo tiene residente indefinitamente.

## Installazione (runbook — da eseguire sulla VPS reale)

**Questo blocco non è stato eseguito da questa sessione — vedi "Limite di
questa sessione" sotto per il perché.** È scritto per essere eseguito
copia-incolla, così com'è, sulla VPS assegnata.

```bash
# 1. Installazione Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. Verifica che il servizio sia attivo
systemctl status ollama
curl http://localhost:11434/api/tags

# 3. Pull del modello esatto richiesto dalla spec — non 4b/8b/14b
ollama pull qwen3:1.7b

# 4. Verifica che il modello sia presente
ollama list | grep qwen3

# 5. Smoke test manuale
curl http://localhost:11434/api/generate -d '{
  "model": "qwen3:1.7b",
  "prompt": "Rispondi con una sola parola: funziona?",
  "stream": false
}'
```

## Health check

`OllamaProvider.health_check()` chiama `GET /api/tags` e distingue due
stati (`ToolHealthStatus`, riusato da `app/models/enums.py`, non un
vocabolario nuovo):

- `UNAVAILABLE` — Ollama stesso non è raggiungibile (nulla in ascolto)
- `CONFIGURATION_ERROR` — Ollama è raggiungibile ma `qwen3:1.7b` non è tra
  i modelli scaricati (es. è stato scaricato un modello diverso per
  errore)
- `AVAILABLE` — raggiungibile e il modello configurato è presente

## Benchmark

Vedi `docs/architecture/local-ai-benchmark.md` — **non eseguito in questa
sessione**, con il motivo esatto e un template pronto per registrare i
numeri reali quando disponibili.

## Limite di questa sessione — da leggere prima di tutto il resto

**[Certain]** Questo sandbox di sviluppo non può raggiungere `ollama.com`
né `huggingface.co` — confermato sia con `curl` diretto (403 sul CONNECT)
sia interrogando lo stato del proxy di rete stesso (`$HTTPS_PROXY/__agentproxy/status`),
che registra esplicitamente il rifiuto come un blocco a livello di
policy per entrambi gli host, non un errore transitorio. L'allowlist di
rete di questo ambiente copre `pypi.org`/`registry.npmjs.org` e pochi
altri registry — non la distribuzione binaria di Ollama né i pesi del
modello ospitati su HuggingFace.

**[Certain]** Questo sandbox inoltre non è la VPS reale assegnata a
Dinamik (qui: 2 vCPU/7.8GB RAM/Ubuntu 24.04 — diversa dalla VPS target di
spec, 2 vCPU/4GB RAM) e questa sessione non ha alcun accesso — SSH o altro
— alla VPS vera.

**Conseguenza**: l'installazione reale di Ollama, il pull del modello, lo
smoke test, il benchmark e il test di integrazione reale (sezioni 5, 6,
25, 30-31 della spec) non sono eseguibili da questa sessione in nessun
ambiente a cui ha accesso ora. Il codice sopra (`app/ai/*`) è reale,
testato (via `MockAIProvider`), lintato e type-checked con lo stesso
rigore di ogni milestone precedente — la parte che manca è
esclusivamente la verifica contro un Ollama reale, non l'architettura.

Due strade per chiuderla, alternative:

1. **Fil esegue il runbook sopra sulla VPS reale** e riporta i numeri
   reali (RAM idle/con Ollama/durante inferenza, tempo di caricamento del
   modello, latenza, token/sec) — vengono poi registrati in
   `local-ai-benchmark.md` così come sono, senza arrotondamenti o stime.
2. **Questa sessione ottiene un accesso reale alla VPS** (SSH o
   device-bridge) — a quel punto l'intero runbook, incluso il test di
   integrazione `tests/test_ai_ollama_integration.py` (già scritto, già
   verificato che si salta pulito senza Ollama — vedi il file), viene
   eseguito qui e i numeri arrivano diretti da una misurazione reale.

## Provider cloud futuro

`AIRouter`/`config/default.yaml`'s `ai.cloud` sono lo spazio riservato per
un secondo `AIProvider` a pagamento (Anthropic/OpenAI — i campi
`anthropic_api_key`/`openai_api_key` in `Settings` esistono già,
inutilizzati, riservati per allora). Nessun codice per questo esiste
ancora — `ai.provider: cloud` solleva `AICloudNotImplementedError`
esplicitamente, per design (sezione 24: FAIL SAFE, mai un fallback
automatico verso un'API a pagamento non richiesta).

## Regola finale

DINAMIK non è un chatbot. L'architettura resta `DATA → EVIDENCE → SIGNALS
→ AI INTERPRETATION → OPPORTUNITY → ADVISOR`. L'AI qui costruita è un
livello di interpretazione/ragionamento (`AIAnalyst` legge `Evidence`,
produce `AIClaim` — mai una raccomandazione commerciale finale, mai un
dato inventato, mai una promozione silenziosa di INFERENCE/HYPOTHESIS a
FACT) — non è il database, non è l'evidenza, non è la fonte di verità.
