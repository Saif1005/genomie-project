#!/usr/bin/env bash
# Démarre ZAYNB (ollama + backend + frontend) sur le serveur local.
# Usage : bash scripts/start.sh [--no-build]
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

BUILD="--build"
[ "${1:-}" = "--no-build" ] && BUILD=""

command -v docker >/dev/null 2>&1 || die "Docker introuvable — lancez bash scripts/check_prereqs.sh"
docker info >/dev/null 2>&1 || die "Docker inaccessible (démon arrêté ou utilisateur hors du groupe docker)"

ensure_env_file
load_env

# Arborescence des données (voir DEPLOY_LOCAL.md)
for d in reference/hg38 patients models/ollama models/huggingface tmp/work tmp/scratch tmp/reports; do
  mkdir -p "$LOCAL_DATA_ROOT/$d" 2>/dev/null || die \
    "Impossible de créer $LOCAL_DATA_ROOT/$d — exécutez :
    sudo mkdir -p $LOCAL_DATA_ROOT && sudo chown -R \$USER: $LOCAL_DATA_ROOT"
done
chmod 750 "$LOCAL_DATA_ROOT" 2>/dev/null || true

if gpu_docker_ok; then
  info "GPU NVIDIA utilisable par Docker → override docker-compose.gpu.yml activé"
else
  warn "Aucun GPU utilisable par Docker → BioGPT/Mistral sur CPU, pipeline FASTQ en GATK4 CPU (lent)"
fi

if [ "$BIND_ADDRESS" != "127.0.0.1" ]; then
  warn "Interface exposée sur $BIND_ADDRESS (BIND_ADDRESS) — vérifiez le pare-feu : réseau local uniquement, jamais Internet"
fi

info "Démarrage des conteneurs..."
# shellcheck disable=SC2086
compose up -d $BUILD

info "Attente du backend (jusqu'à 5 min au premier démarrage)..."
for _ in $(seq 1 60); do
  if curl -sf "$BACKEND_URL/health" >/dev/null 2>&1; then
    echo
    curl -s "$BACKEND_URL/health"; echo
    echo
    echo "${C_OK}ZAYNB est prêt.${C_RST} Ouvrez dans le navigateur du serveur : $FRONTEND_URL"
    exit 0
  fi
  printf '.'
  sleep 5
done
echo
die "Backend non joignable sur $BACKEND_URL/health — consultez : bash scripts/logs.sh backend"
