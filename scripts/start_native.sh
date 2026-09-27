#!/usr/bin/env bash
# Starts GermlineIQ natively (no Docker Compose): Ollama + backend (conda env) + frontend (Next.js).
# GATK / Parabricks containers are still started by the backend through Docker.
#
# Usage: bash scripts/start_native.sh [--restart-backend | --restart-frontend | --stop]
# Configuration: Backend/.env (LOCAL_DATA_ROOT, …); conda env name via GERMLINEIQ_CONDA_ENV (default "genomic");
#                Ollama binary via OLLAMA_BIN (default ~/.local/ollama/bin/ollama, else "ollama" on PATH).
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

ENV_NAME="${GERMLINEIQ_CONDA_ENV:-genomic}"
CONDA_BASE="$(conda info --base 2>/dev/null || echo "$HOME/miniconda3")"
ENVBIN="$CONDA_BASE/envs/$ENV_NAME/bin"
[ -x "$ENVBIN/python" ] || die "conda env '$ENV_NAME' not found ($ENVBIN) — see DEPLOY_LOCAL.md section 8"
export PATH="$ENVBIN:$PATH"
OLLAMA_BIN="${OLLAMA_BIN:-$HOME/.local/ollama/bin/ollama}"
[ -x "$OLLAMA_BIN" ] || OLLAMA_BIN="$(command -v ollama || true)"
LOGS="$LOCAL_DATA_ROOT/tmp"
mkdir -p "$LOGS"

stop_all() {
  pkill -f "[u]vicorn app.main" || true
  pkill -f "[n]ext-server" || true
  pkill -f "[o]llama serve" || true
}

case "${1:-}" in
  --stop) stop_all; info "GermlineIQ stopped (data kept in $LOCAL_DATA_ROOT)."; exit 0 ;;
  --restart-backend) pkill -f "[u]vicorn app.main" || true; sleep 2 ;;
  --restart-frontend) pkill -f "[n]ext-server" || true; sleep 2 ;;
  "") ;;
  *) die "Unknown option: $1" ;;
esac

if ! curl -sf "http://127.0.0.1:11434/api/version" >/dev/null; then
  [ -n "$OLLAMA_BIN" ] || die "Ollama not found (set OLLAMA_BIN)"
  info "Starting Ollama"
  OLLAMA_MODELS="$LOCAL_DATA_ROOT/models/ollama/models" nohup "$OLLAMA_BIN" serve >"$LOGS/ollama.log" 2>&1 &
fi

if ! curl -sf "$BACKEND_URL/health" >/dev/null; then
  info "Starting the backend"
  (
    cd "$ROOT_DIR/Backend"
    export HF_HOME="$LOCAL_DATA_ROOT/models/huggingface" OLLAMA_HOST="http://localhost:11434" PYTHONPATH="$PWD"
    nohup python -m uvicorn app.main:app --host "$BIND_ADDRESS" --port "$BACKEND_PORT" --workers 1 >"$LOGS/backend.log" 2>&1 &
  )
fi

if ! curl -sf "$FRONTEND_URL/" >/dev/null; then
  info "Starting the frontend"
  [ -d "$ROOT_DIR/Frontend/.next" ] || (cd "$ROOT_DIR/Frontend" && npm ci && npm run build)
  (cd "$ROOT_DIR/Frontend" && nohup npx next start -H "$BIND_ADDRESS" -p "$FRONTEND_PORT" >"$LOGS/frontend.log" 2>&1 &)
fi

for _ in $(seq 90); do
  curl -sf "$BACKEND_URL/health" >/dev/null && curl -sf "$FRONTEND_URL/" >/dev/null && break
  sleep 2
done
if curl -sf "$BACKEND_URL/health" >/dev/null; then
  curl -s "$BACKEND_URL/health" | python3 -c "import json,sys; d=json.load(sys.stdin); print('backend:', d['status'], '|', d['orchestrator'], '|', d['pipeline_backend'], '-', d['pipeline_backend_reason'])"
  echo "${C_OK}GermlineIQ is ready.${C_RST} Interface: $FRONTEND_URL   (logs: $LOGS/*.log)"
else
  die "Backend not reachable at $BACKEND_URL/health — see $LOGS/backend.log"
fi
