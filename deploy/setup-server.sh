#!/usr/bin/env bash
# Installazione completa di Dinamik Advisor sul server OVH (Ubuntu 24.04).
#
# Uso (da utente normale con sudo, NON da root):
#   curl -fsSL https://raw.githubusercontent.com/filstreetwear-cyber/DINAMIK-/claude/dinamik-advisor-scout-mvp-7no1zb/deploy/setup-server.sh -o setup-server.sh
#   less setup-server.sh            # leggilo prima di lanciarlo
#   bash setup-server.sh            # aggiungi --with-ollama per l'AI locale
#
# Cosa fa (rilanciabile: salta quello che c'è già):
#   1. pacchetti di sistema (PostgreSQL, Python, git)
#   2. database e utente PostgreSQL con password generata
#   3. codice dal branch, virtualenv, dipendenze, migrazioni
#   4. .env con password DB e chiave dell'interfaccia generate
#   5. servizio systemd dell'interfaccia web (solo 127.0.0.1:8000)
#   6. backup notturno del database (14 giorni)
#   7. Tailscale: rete privata per PC di casa e team; Postgres e
#      interfaccia raggiungibili SOLO da lì, mai da internet
#   8. (facoltativo) Ollama + qwen3:1.7b
#
# L'unico passo interattivo è il login Tailscale (apre un link).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/filstreetwear-cyber/DINAMIK-.git}"
BRANCH="${BRANCH:-claude/dinamik-advisor-scout-mvp-7no1zb}"
APP_DIR="${APP_DIR:-$HOME/DINAMIK-}"
DB_NAME="dinamik_amazon_intelligence"
DB_USER="dinamik"
WITH_OLLAMA=0
for arg in "$@"; do
  case "$arg" in
    --with-ollama) WITH_OLLAMA=1 ;;
    *) echo "opzione sconosciuta: $arg" >&2; exit 2 ;;
  esac
done

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Lancia lo script da un utente normale con sudo, non da root." >&2
  exit 1
fi

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "1/8 Pacchetti di sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql python3-venv python3-pip git curl ca-certificates >/dev/null
PG_VERSION="$(ls /etc/postgresql | sort -V | tail -1)"
PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main"
sudo systemctl enable --now postgresql >/dev/null

step "2/8 Database"
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  echo "utente ${DB_USER} già presente: password invariata (la trovi in ${APP_DIR}/.env)"
  DB_PASSWORD=""
else
  DB_PASSWORD="$(openssl rand -hex 24)"
  sudo -u postgres psql -qc "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres psql -qc "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
fi

step "3/8 Codice e dipendenze"
if [[ -d "${APP_DIR}/.git" ]]; then
  if [[ -n "$(git -C "${APP_DIR}" status --porcelain)" ]]; then
    echo "ATTENZIONE: modifiche locali in ${APP_DIR}, non aggiorno il codice." >&2
  else
    git -C "${APP_DIR}" fetch -q origin "${BRANCH}"
    git -C "${APP_DIR}" checkout -q "${BRANCH}"
    git -C "${APP_DIR}" pull -q --ff-only origin "${BRANCH}"
  fi
else
  if ! git clone -q --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"; then
    echo "Clone fallito. Se il repository è privato: crea un token GitHub in sola lettura e rilancia con" >&2
    echo "  REPO_URL=https://<token>@github.com/filstreetwear-cyber/DINAMIK-.git bash setup-server.sh" >&2
    exit 1
  fi
fi
cd "${APP_DIR}"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

step "4/8 Configurazione (.env)"
if [[ ! -f .env ]]; then
  if [[ -z "${DB_PASSWORD}" ]]; then
    echo "L'utente DB esiste ma .env no: imposta DATABASE_URL a mano in ${APP_DIR}/.env" >&2
    DB_PASSWORD="IMPOSTA-QUI-LA-PASSWORD"
  fi
  DASHBOARD_KEY="$(openssl rand -hex 16)"
  cp .env.example .env
  sed -i "s|^MOCK_MODE=.*|MOCK_MODE=false|" .env
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}|" .env
  sed -i "s|^DASHBOARD_API_KEY=.*|DASHBOARD_API_KEY=${DASHBOARD_KEY}|" .env
  chmod 600 .env
  NEW_ENV=1
else
  echo ".env già presente: non lo tocco"
  NEW_ENV=0
fi
.venv/bin/alembic upgrade head

step "5/8 Servizio interfaccia web"
sed -e "s|^User=.*|User=$(id -un)|" \
    -e "s|/home/ubuntu/DINAMIK-|${APP_DIR}|g" \
    deploy/dinamik-web.service | sudo tee /etc/systemd/system/dinamik-web.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now dinamik-web >/dev/null
sudo systemctl restart dinamik-web

step "6/8 Backup notturno del database"
mkdir -p "${APP_DIR}/logs"
BACKUP_LINE="17 3 * * * ${APP_DIR}/deploy/backup-db.sh >> ${APP_DIR}/logs/backup.log 2>&1"
WEBSITES_LINE="7 6 * * * cd ${APP_DIR} && .venv/bin/dinamik websites discover --max-brands 50 >> logs/websites.log 2>&1 && .venv/bin/dinamik contacts discover --max-brands 50 >> logs/contacts.log 2>&1"
( { crontab -l 2>/dev/null || true; } | { grep -v -e 'backup-db.sh' -e 'dinamik websites discover' || true; }; echo "${BACKUP_LINE}"; echo "${WEBSITES_LINE}" ) | crontab -
"${APP_DIR}/deploy/backup-db.sh" && echo "primo backup creato in \$HOME/backups"

step "7/8 Tailscale (rete privata)"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! tailscale ip -4 >/dev/null 2>&1; then
  echo "Apri il link che compare qui sotto e accedi con lo stesso account che userai sul PC di casa."
  sudo tailscale up
fi
TS_IP="$(tailscale ip -4 | head -1)"
if ! grep -q "${TS_IP}" "${PG_CONF_DIR}/postgresql.conf"; then
  echo "listen_addresses = 'localhost,${TS_IP}'" | sudo tee -a "${PG_CONF_DIR}/postgresql.conf" >/dev/null
fi
HBA_LINE="host  ${DB_NAME}  ${DB_USER}  100.64.0.0/10  scram-sha-256"
sudo grep -qF "${HBA_LINE}" "${PG_CONF_DIR}/pg_hba.conf" || echo "${HBA_LINE}" | sudo tee -a "${PG_CONF_DIR}/pg_hba.conf" >/dev/null
sudo systemctl restart postgresql
sudo tailscale serve --bg 8000 >/dev/null 2>&1 || echo "tailscale serve non riuscito: l'interfaccia resta su 127.0.0.1:8000"

if [[ "${WITH_OLLAMA}" -eq 1 ]]; then
  step "8/8 Ollama + qwen3:1.7b"
  command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
  ollama pull qwen3:1.7b
else
  step "8/8 Ollama saltato (rilancia con --with-ollama quando serve)"
fi

step "Verifica"
sleep 2
.venv/bin/dinamik health || true
curl -fsS -o /dev/null -w "interfaccia web: HTTP %{http_code}\n" http://127.0.0.1:8000/ui/login || echo "interfaccia web non risponde: sudo journalctl -u dinamik-web -n 50"
if sudo ss -tlnp | grep ':5432' | grep -q '0.0.0.0'; then
  echo "ATTENZIONE: Postgres ascolta su 0.0.0.0, controlla ${PG_CONF_DIR}/postgresql.conf" >&2
fi

TS_NAME="$(tailscale status --self --json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
printf '\n\033[1mFatto.\033[0m\n'
echo "Interfaccia (dai dispositivi con Tailscale): https://${TS_NAME:-<nome-server>}/ui"
echo "IP Tailscale del server (per il PC di casa): ${TS_IP}"
if [[ "${NEW_ENV}" -eq 1 ]]; then
  echo
  echo "Salva questi due valori in un posto sicuro (sono anche in ${APP_DIR}/.env):"
  echo "  Chiave interfaccia:   ${DASHBOARD_KEY}"
  echo "  DATABASE_URL per il PC di casa:"
  echo "  postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${TS_IP}:5432/${DB_NAME}"
fi
