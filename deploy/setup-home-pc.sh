#!/usr/bin/env bash
# Installazione di Dinamik Advisor sul PC di casa (Linux o macOS), ruolo: scraper.
# Uso: bash deploy/setup-home-pc.sh   (rilanciabile)
# Non tocca Amazon: il primo run lo lanci tu (o la sessione Claude locale).
set -euo pipefail
BRANCH="${BRANCH:-claude/dinamik-advisor-scout-mvp-7no1zb}"
REPO_URL="${REPO_URL:-https://github.com/filstreetwear-cyber/DINAMIK-.git}"
APP_DIR="${APP_DIR:-$HOME/DINAMIK-}"
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "1/6 Programmi di base"
PY=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    PY="$candidate"; break
  fi
done
if [[ -z "$PY" ]]; then
  echo "Serve Python 3.12+. macOS: brew install python@3.12 · Ubuntu: sudo apt install python3.12 python3.12-venv" >&2
  exit 1
fi
command -v git >/dev/null || { echo "Serve git (macOS: xcode-select --install · Ubuntu: sudo apt install git)" >&2; exit 1; }

step "2/6 Codice"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" checkout -q "$BRANCH"
  git -C "$APP_DIR" pull -q --ff-only origin "$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

step "3/6 Ambiente Python e dipendenze"
[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"

step "4/6 Browser per lo scraper (Chromium)"
.venv/bin/python -m playwright install chromium

step "5/6 Configurazione (.env)"
if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i.bak 's|^MOCK_MODE=.*|MOCK_MODE=false|' .env && rm -f .env.bak
  chmod 600 .env
  echo "Creato .env. DEVI incollare in DATABASE_URL la riga stampata da setup-server.sh sul server OVH."
else
  echo ".env già presente: non lo tocco"
fi

step "6/6 Tailscale"
if ! command -v tailscale >/dev/null; then
  echo "Installa Tailscale: https://tailscale.com/download (stesso account del server)."
else
  tailscale status >/dev/null 2>&1 && echo "Tailscale attivo." || echo "Tailscale installato ma non connesso: sudo tailscale up"
fi

printf '\n\033[1mFatto.\033[0m Prossimi comandi da %s:\n' "$APP_DIR"
echo "  .venv/bin/dinamik health"
echo "  .venv/bin/dinamik scout vies IT<11 cifre di una P.IVA che conosci>"
echo "  .venv/bin/dinamik scout run --name casa-v1 --category \"Casa e cucina\" --urls-file urls.txt --max-products 25"
