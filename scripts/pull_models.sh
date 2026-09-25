#!/usr/bin/env bash
# Pré-télécharge les modèles et images dans $LOCAL_DATA_ROOT/models (persistants) :
#   - Mistral (Ollama)          ~4,1 Go
#   - BioGPT (HuggingFace)      ~1,6 Go
#   - Image GATK                ~2 Go   (pipeline CPU)
#   - Image Parabricks 4.6      plusieurs Go (seulement si GPU ≥ PARABRICKS_MIN_VRAM_GB)
# Usage : bash scripts/pull_models.sh [--yes]
# Prérequis : conteneurs construits (bash scripts/start.sh)
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
[ "${1:-}" = "--yes" ] && ZAYNB_YES=1

ensure_env_file
load_env
MODEL="${ORCHESTRATOR_LLM_MODEL:-mistral:v0.3}"
BIOGPT="${PREDICTION_MODEL:-microsoft/biogpt}"
GATK_IMG="${GATK_DOCKER_IMAGE:-broadinstitute/gatk:4.2.6.1}"
PB_IMG="${PARABRICKS_IMAGE:-nvcr.io/nvidia/clara/clara-parabricks:4.6.0-1}"

PULL_PB=0
if command -v nvidia-smi >/dev/null 2>&1; then
  max_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1 || echo 0)
  # Tolérance 1 Go : une carte « 16 Go » expose 15 360-16 384 MiB
  min_mb=$(awk -v g="${PARABRICKS_MIN_VRAM_GB:-16}" 'BEGIN {printf "%d", (g - 1) * 1024}')
  [ "${max_mb:-0}" -ge "$min_mb" ] && PULL_PB=1
fi

echo "Téléchargements prévus :"
echo "  - Ollama $MODEL (~4 Go)"
echo "  - BioGPT $BIOGPT (~1,6 Go)"
echo "  - $GATK_IMG (~2 Go)"
[ "$PULL_PB" = 1 ] && echo "  - $PB_IMG (plusieurs Go — GPU compatible détecté)"
confirm "Continuer (> 5 Go au total) ?" || die "Annulé."

info "Ollama : $MODEL"
compose up -d ollama
compose exec -T ollama ollama pull "$MODEL"

info "BioGPT : $BIOGPT → $LOCAL_DATA_ROOT/models/huggingface"
compose run --rm --no-deps -T backend python -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
AutoTokenizer.from_pretrained('$BIOGPT')
AutoModelForCausalLM.from_pretrained('$BIOGPT')
print('BioGPT en cache')
"

info "Image GATK : $GATK_IMG"
docker pull "$GATK_IMG"

if [ "$PULL_PB" = 1 ]; then
  info "Image Parabricks : $PB_IMG"
  docker pull "$PB_IMG"
fi

echo "${C_OK}Modèles prêts.${C_RST}"
