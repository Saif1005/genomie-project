#!/usr/bin/env bash
# Starts GermlineIQ (ollama + backend + frontend) on the local server.
# Usage : bash scripts/start.sh [--no-build]
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

BUILD="--build"
[ "${1:-}" = "--no-build" ] && BUILD=""

command -v docker >/dev/null 2>&1 || die "Docker introuvable — lancez bash scripts/check_prereqs.sh"
docker info >/dev/null 2>&1 || die "Docker unreachable (daemon stopped or user not in the docker group)"

ensure_env_file
load_env

# Data layout (see DEPLOY_LOCAL.md)
for d in reference/hg38 patients models/ollama models/huggingface tmp/work tmp/scratch tmp/reports; do
  mkdir -p "$LOCAL_DATA_ROOT/$d" 2>/dev/null || die \
    "Cannot create $LOCAL_DATA_ROOT/$d — run:
    sudo mkdir -p $LOCAL_DATA_ROOT && sudo chown -R \$USER: $LOCAL_DATA_ROOT"
done
chmod 750 "$LOCAL_DATA_ROOT" 2>/dev/null || true

if gpu_docker_ok; then
  info "NVIDIA GPU usable by Docker → docker-compose.gpu.yml override enabled"
else
  warn "No GPU usable by Docker → BioGPT/Mistral on CPU, FASTQ pipeline on GATK4 CPU (slow)"
fi

if [ "$BIND_ADDRESS" != "127.0.0.1" ]; then
  warn "Interface exposed on $BIND_ADDRESS (BIND_ADDRESS) — check the firewall: local network only, never the Internet"
fi

info "Starting the containers..."
# shellcheck disable=SC2086
compose up -d $BUILD

info "Waiting for the backend (up to 5 min on first start)..."
for _ in $(seq 1 60); do
  if curl -sf "$BACKEND_URL/health" >/dev/null 2>&1; then
    echo
    curl -s "$BACKEND_URL/health"; echo
    echo
    echo "${C_OK}GermlineIQ is ready.${C_RST} Open in the server browser: $FRONTEND_URL"
    exit 0
  fi
  printf '.'
  sleep 5
done
echo
die "Backend unreachable at $BACKEND_URL/health — see: bash scripts/logs.sh backend"
