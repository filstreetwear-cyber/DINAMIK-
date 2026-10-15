#!/usr/bin/env bash
# Backup del database Dinamik (formato custom di pg_dump), tiene 14 giorni.
# Lanciato ogni notte da cron (vedi deploy/setup-server.sh).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
mkdir -p "${BACKUP_DIR}"
URL="$(grep '^DATABASE_URL=' "${APP_DIR}/.env" | cut -d= -f2- | sed 's/+asyncpg//')"
pg_dump -Fc "${URL}" > "${BACKUP_DIR}/dinamik_$(date +%F).dump"
find "${BACKUP_DIR}" -name 'dinamik_*.dump' -mtime +14 -delete
# Ripristino: pg_restore --clean -d "<DATABASE_URL senza +asyncpg>" dinamik_AAAA-MM-GG.dump
