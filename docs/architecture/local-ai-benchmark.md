# Local AI Benchmark — Milestone 9.5

## Stato: non eseguito

**Nessuna misura in questo documento è stata eseguita.** Non contiene
numeri stimati, copiati da benchmark pubblici su hardware diverso, o
"tipici per un modello di questa dimensione" — la spec di Milestone 9.5
vieta esplicitamente questo ("Do not fabricate measurements", "Do not
estimate results. Do not copy benchmark numbers from the internet"), e
questo documento rispetta quel vincolo restando vuoto piuttosto che
inventare.

## Perché non è stato possibile eseguirlo qui

Vedi `docs/architecture/local-ai.md`, sezione "Limite di questa sessione",
per il dettaglio completo. In sintesi: questo sandbox di sviluppo non può
scaricare né Ollama (`ollama.com` bloccato dalla policy di rete) né i pesi
di `qwen3:1.7b` (`huggingface.co` bloccato allo stesso modo), e questa
sessione non ha accesso alla VPS reale assegnata a Dinamik dove il
benchmark andrebbe eseguito.

## Template — da compilare con numeri reali

Eseguire lo script/i comandi sotto sulla VPS reale (2 vCPU / 4GB RAM /
Ubuntu, dopo aver seguito il runbook di installazione in `local-ai.md`) e
riportare qui i valori ESATTI ottenuti, con data e comando usato per
ciascuno. Nessuna cella va riempita con una stima.

| Metrica | Come misurarla | Valore reale |
|---|---|---|
| RAM idle (Ollama non avviato) | `free -h` prima di `ollama serve` | _da misurare_ |
| RAM con Ollama avviato, nessun modello caricato | `free -h` dopo `systemctl start ollama`, prima di una generazione | _da misurare_ |
| RAM durante un'inferenza reale | `free -h` durante una chiamata a `/api/generate` in corso | _da misurare_ |
| Tempo di caricamento del modello (cold start) | tempo tra la prima richiesta e la prima risposta, con `keep_alive` scaduto | _da misurare_ |
| Latenza — prompt breve (~50 token) | `time curl .../api/generate ...` | _da misurare_ |
| Latenza — prompt tipico AIAnalyst (~300-500 token di evidenza) | idem, con un prompt reale da `app/ai/analyst.py` | _da misurare_ |
| Token/sec in generazione | dal campo `eval_count`/`eval_duration` della risposta Ollama | _da misurare_ |
| Comportamento sotto carico concorrente (`max_concurrency: 1` rispettato?) | 2+ richieste inviate in parallelo, verificare che vengano serializzate, non che la VPS vada in OOM | _da misurare_ |

## Comando di riferimento per popolare questa tabella

```bash
# RAM
free -h

# Latenza + token/sec (il campo "eval_count"/"eval_duration" nella risposta
# JSON di Ollama dà token generati e tempo di generazione in nanosecondi)
time curl -s http://localhost:11434/api/generate -d '{
  "model": "qwen3:1.7b",
  "prompt": "<prompt reale>",
  "stream": false
}' | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('eval_count'), d.get('eval_duration'))"
```

## Una volta compilato

Sostituire ogni riga "_da misurare_" con il numero reale, la data della
misurazione e chi l'ha eseguita. Questo documento passa da "non eseguito"
a "eseguito il YYYY-MM-DD da <nome>" solo quando ogni riga ha un numero
reale dietro.
