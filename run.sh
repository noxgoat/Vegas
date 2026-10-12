#!/usr/bin/env bash
# Inicia o JurisBusca SP. Uso: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec python3 -m uvicorn app.api:app --host "${JURIS_HOST:-127.0.0.1}" --port "${JURIS_PORT:-8000}"
