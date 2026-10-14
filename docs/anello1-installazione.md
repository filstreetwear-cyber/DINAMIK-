# Anello 1 — installazione stasera (PC di casa + server OVH)

Ruoli:
- **Server OVH (Ubuntu 24.04)**: PostgreSQL. Ci gira anche export/analisi.
- **PC di casa**: solo lo scraper (`dinamik scout run`). Un PC alla volta.
- **Collegamento**: Tailscale (gratuito). La porta 5432 di Postgres non va
  mai aperta su internet.

Tempo stimato: 45–60 minuti la prima volta.

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

## Dove finiscono i dati

| Cosa | Dove |
|---|---|
| HTML di ogni pagina, screenshot schede dei target | `data/evidence/AAAA-MM-GG/` sul PC di casa |
| Brand, venditori, aziende, prodotti, storico | Postgres sul server OVH |
| Report di validazione | `reports/anello1/` sul PC di casa |

Le evidenze restano sul PC di casa. Se servono sul server: `rsync` via
Tailscale o una cartella sincronizzata. Non è automatico in questa versione.
