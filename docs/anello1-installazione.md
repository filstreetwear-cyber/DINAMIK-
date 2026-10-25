# Anello 1 — installazione stasera (PC di casa + server OVH)

Ruoli:
- **Server OVH (Ubuntu 24.04)**: PostgreSQL. Ci gira anche export/analisi.
- **PC di casa**: solo lo scraper (`dinamik scout run`). Un PC alla volta.
- **Collegamento**: Tailscale (gratuito). La porta 5432 di Postgres non va
  mai aperta su internet.

Tempo stimato: 45–60 minuti la prima volta a mano, **~10 minuti con lo
script automatico** per il server.

## Scorciatoia: server OVH con un solo comando

Fa da solo le sezioni 1 (lato server), 2, 3, 7, il backup notturno e la
pianificazione della ricerca siti. L'unico passo interattivo è il login
Tailscale.
```bash
curl -fsSL https://raw.githubusercontent.com/filstreetwear-cyber/DINAMIK-/claude/dinamik-advisor-scout-mvp-7no1zb/deploy/setup-server.sh -o setup-server.sh
less setup-server.sh     # leggilo prima
bash setup-server.sh     # --with-ollama per installare anche l'AI locale
```
Alla fine stampa la chiave dell'interfaccia e il `DATABASE_URL` da
mettere nel `.env` del PC di casa. Se il repository è privato, il
download con `curl` fallisce: clona con le tue credenziali GitHub e lancia
`bash deploy/setup-server.sh` dalla cartella del clone.

PC di casa, sempre con un comando (rilanciabile):
- Windows (PowerShell): `Set-ExecutionPolicy -Scope Process Bypass` poi
  `.\deploy\setup-home-pc.ps1`
- Linux/macOS: `bash deploy/setup-home-pc.sh`

Se vuoi che lo faccia Claude: apri Claude Code **sul PC di casa** e
incollagli `docs/prompt-claude-locale.md`.

---

## 1. Tailscale su entrambe le macchine

Server OVH:
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4        # annota questo IP: 100.x.y.z
```
PC di casa: installa Tailscale da tailscale.com/download, accedi con lo
stesso account. Verifica dal PC: `ping 100.x.y.z`.

## 2. PostgreSQL sul server OVH

```bash
sudo apt update && sudo apt install -y postgresql
sudo -u postgres psql -c "CREATE USER dinamik WITH PASSWORD 'SCEGLI-UNA-PASSWORD-LUNGA';"
sudo -u postgres psql -c "CREATE DATABASE dinamik_amazon_intelligence OWNER dinamik;"
```
Accesso solo dalla rete Tailscale:
```bash
# /etc/postgresql/16/main/postgresql.conf
listen_addresses = 'localhost,100.x.y.z'

# /etc/postgresql/16/main/pg_hba.conf  (aggiungi in fondo)
host  dinamik_amazon_intelligence  dinamik  100.64.0.0/10  scram-sha-256

sudo systemctl restart postgresql
```
Controllo: `sudo ss -tlnp | grep 5432` deve mostrare solo `127.0.0.1` e
`100.x.y.z`, **mai** `0.0.0.0`.

## 3. Codice (su entrambe le macchine)

```bash
git clone https://github.com/filstreetwear-cyber/DINAMIK-.git
cd DINAMIK-
git checkout claude/dinamik-advisor-scout-mvp-7no1zb
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```
In `.env` imposta:
```
MOCK_MODE=false
DATABASE_URL=postgresql+asyncpg://dinamik:LA-PASSWORD@100.x.y.z:5432/dinamik_amazon_intelligence
```
(sul server OVH puoi usare `localhost` al posto di `100.x.y.z`).

Solo sul PC di casa, il browser per lo scraper:
```bash
playwright install chromium
```

Schema del database (una volta sola, da una delle due macchine):
```bash
alembic upgrade head
```

Verifica (su entrambe):
```bash
dinamik health
pytest -q          # attesi: tutti verdi, qualche skip (Ollama, Lighthouse)
```

## 4. Primo run di validazione (PC di casa)

Prima controlla che VIES risponda da casa, con una P.IVA che conosci
(es. quella di Dinamik):
```bash
dinamik scout vies IT<11 cifre>
```
Poi 20–30 schede della nicchia Casa e cucina. Due modi:

**A. Da pagine elenco** (file `urls.txt`, un URL per riga, es. pagine
2–5 di una ricerca o la pagina Bestseller di una sottocategoria):
```bash
dinamik scout run --name casa-organizzazione-v1 --category "Casa e cucina" \
  --urls-file urls.txt --max-products 25
```

**B. Da ASIN scelti a mano** (file `asins.txt`, un ASIN o un link /dp/
per riga):
```bash
dinamik scout run --name casa-organizzazione-v1 --asins-file asins.txt
```

Su Windows scrivi il comando su una riga sola (niente `\` a fine riga).

Durata: circa 10–12 secondi per pagina (attesa voluta). 25 schede + i
profili venditore ≈ 8–10 minuti.

Alla fine il comando stampa il percorso del report in `reports/anello1/`.

## 5. Gate di validazione — da fare a mano

1. Apri il report `.md` e almeno 10 righe su amazon.it.
2. Colonna **OK?**: venditore e brand corrispondono?
3. Si passa allo step successivo solo se:
   - venditore estratto ≥ 80% (lo stampa il comando);
   - CAPTCHA < 10% (con un CAPTCHA il run si ferma da solo).
4. Se fallisce: non cambiare il codice a tentativi. Manda il report e
   2–3 file HTML da `data/evidence/<data>/product/` — il parser si
   corregge su quelle pagine vere.

Se compare `STOPPED: captcha`: fermati almeno 24 ore, poi dimezza il
ritmo in `config/default.yaml` (`scout.min_delay_seconds: 16`,
`max_delay_seconds: 30`).

## 6. Export dei prospect (server OVH o PC)

```bash
dinamik export brands
```
Il file .xlsx in `exports/` ha in coda le colonne nuove: opportunità
scheda (livello e motivi), venditore, target sì/no, P.IVA, VIES, indirizzo.

## 7. Interfaccia web (server OVH)

In `.env` sul server aggiungi una chiave lunga, condivisa col team:
```
DASHBOARD_API_KEY=una-frase-lunga-difficile-da-indovinare
```
Avvio come servizio (riparte da solo dopo un riavvio):
```bash
sudo cp deploy/dinamik-web.service /etc/systemd/system/
# modifica User e i percorsi nel file se il tuo utente non è "ubuntu"
sudo systemctl daemon-reload && sudo systemctl enable --now dinamik-web
```
Il servizio ascolta solo su `127.0.0.1:8000`. Per aprirlo dai PC del team
(che hanno Tailscale) senza esporlo su internet:
```bash
sudo tailscale serve --bg 8000
```
Poi apri `https://<nome-server>.<tailnet>.ts.net/ui` dal browser. Login: il
tuo nome + la chiave. Il nome resta registrato accanto a ogni approvazione.

Cosa si fa dall'interfaccia:
- **Panoramica**: numeri chiave, migliori prospect, ultime scansioni.
- **Prospect**: lista filtrabile (stato, ricerca, includi non target).
- **Scheda prospect**: screenshot e problemi di ogni scheda, storico BSR,
  opportunità, azienda e P.IVA, sito web, evidenze; pulsanti Approva /
  Scarta / Contattato; bozza di contatto modificabile; generazione AI;
  audit stampabile per il cliente.
- **Scansioni**: esito di ogni run dello scout.

## 8. Sito ufficiale dei brand (server OVH)

```bash
dinamik websites discover --max-brands 50
```
Prova i domini plausibili (brand.it, brand.com, ...) e conferma il sito
solo se nella home compare la P.IVA verificata su VIES. Se trova solo il
nome nel titolo lo segna come "possibile": lo confermi a mano dalla scheda
prospect. Con `--lighthouse` aggiunge velocità e SEO (serve
`npm install -g lighthouse`).

### 8b. Email e telefono dell'azienda

```bash
dinamik contacts discover --max-brands 50
```
Per i brand con sito confermato legge la home e fino a 3 pagine
"contatti / chi siamo / note legali" e salva email e telefoni pubblicati.
Scarta P.IVA e codici fiscali scambiati per numeri, indirizzi tecnici
(wix, sentry, esempi) e domini estranei. Ogni email è marcata
**aziendale** (info@, commerciale@...), **personale** (nome.cognome@: dato
personale, GDPR) o **PEC**. Dalla scheda prospect: pulsante "Cerca
contatti sul sito" e, sulla bozza, "Apri nella mia email" (apre il tuo
programma di posta con destinatario, oggetto e testo già compilati: l'invio
resta tuo). Lo script del server lo pianifica ogni mattina dopo la ricerca
siti.

## 9. AI locale (server OVH, facoltativo)

Serve solo per "Genera raccomandazioni e bozza con AI". Tutto il resto
funziona senza.
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:1.7b
```
Verifica: `dinamik health` deve mostrare `ai: AVAILABLE`. Con 4 GB di RAM
il modello 1.7B è il massimo ragionevole; una bozza richiede 20-60 secondi.

## 10. Pianificazioni

Esempi pronti in `deploy/crontab.example`:
- PC di casa, ogni lunedì: `dinamik scout refresh` (storico delle schede
  target, il "mini-Keepa").
- Server, ogni giorno: `dinamik websites discover`.

## Dove finiscono i dati

| Cosa | Dove |
|---|---|
| HTML di ogni pagina | `data/evidence/AAAA-MM-GG/` sul PC di casa |
| Screenshot schede dei target | sul PC di casa **e** copia nel database (li mostra l'interfaccia) |
| Brand, venditori, aziende, prodotti, storico | Postgres sul server OVH |
| Report di validazione | `reports/anello1/` sul PC di casa |

Gli HTML grezzi restano sul PC di casa (servono per correggere il parser).
Gli screenshot dei target sono copiati nel database, quindi l'interfaccia
sul server li mostra senza sincronizzare cartelle.
