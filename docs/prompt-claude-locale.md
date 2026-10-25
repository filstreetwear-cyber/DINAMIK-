# Prompt per la sessione Claude Code sul PC di casa

Da incollare in una sessione Claude Code aperta **sul PC di casa** (app
desktop Claude, oppure `claude` in un terminale). Quella sessione vede il
tuo computer; la sessione cloud no.

---

Lavori sul PC di casa di Fil per installare e validare Dinamik Advisor.
Repository: `https://github.com/filstreetwear-cyber/DINAMIK-`, branch
`claude/dinamik-advisor-scout-mvp-7no1zb`. Leggi prima
`docs/anello1-installazione.md` e `docs/architecture/fase-roadmap-status.md`.

Fai, in ordine, chiedendo conferma prima di ogni installazione di programmi:

1. **Installazione PC di casa**: Windows → `deploy/setup-home-pc.ps1`;
   Linux/macOS → `deploy/setup-home-pc.sh`. Se il repository non è ancora
   clonato, clonalo tu con le credenziali GitHub di Fil.
2. **Server OVH**: se Fil ha accesso SSH dal PC (`ssh utente@ip-server`),
   copia ed esegui lì `deploy/setup-server.sh` (interattivo solo per il
   login Tailscale: fai aprire il link a Fil). Riporta a Fil la chiave
   dell'interfaccia e il `DATABASE_URL` stampati alla fine. Non salvare
   password in file diversi da `.env`, non incollarle in chat.
3. **Collegamento**: Tailscale attivo su entrambe le macchine; nel `.env`
   del PC metti il `DATABASE_URL` con l'IP Tailscale del server.
   Verifica con `dinamik health` (database raggiungibile).
4. **Verifiche**: `pytest -q` sul PC (attesi tutti verdi, qualche skip);
   `dinamik scout vies IT<P.IVA nota>` per confermare che VIES risponde.
5. **Primo run di validazione**: chiedi a Fil 2-3 URL di pagine elenco
   Amazon.it in Casa e cucina (Bestseller di una sottocategoria o pagine
   2-5 di una ricerca), salvale in `urls.txt`, poi
   `dinamik scout run --name casa-v1 --category "Casa e cucina" --urls-file urls.txt --max-products 25`.
   Mai aumentare il ritmo o aggirare un CAPTCHA: se il run si ferma per
   CAPTCHA, stop e riporta.
6. **Gate**: apri il report in `reports/anello1/`, verifica a mano con Fil
   almeno 10 righe. Se il venditore estratto è sotto l'80%, apri 2-3 HTML
   da `data/evidence/<data>/product/`, trova i selettori corretti,
   correggi `app/tools/amazon_scraping/page_parser.py`, aggiungi quelle
   pagine come fixture di test, e rilancia sullo stesso campione.
7. **Commit**: correzioni e report sul branch, messaggio chiaro, push.
   Non committare `.env` né `data/evidence/`.

Riporta a Fil alla fine: numeri del gate (venditore estratto %, CAPTCHA,
target trovati), cosa hai corretto, cosa resta bloccato.
