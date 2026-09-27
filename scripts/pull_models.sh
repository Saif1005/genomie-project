#!/usr/bin/env bash
# Pre-downloads the models and images into $LOCAL_DATA_ROOT/models (persistent):
#   - Mistral (Ollama)          ~4.1 GB
#   - BioGPT (HuggingFace)      ~1.6 GB
#   - GATK image                ~2 GB   (CPU pipeline)
#   - Parabricks 4.6 image      several GB (only if GPU ≥ PARABRICKS_MIN_VRAM_GB)
# Usage: bash scripts/pull_models.sh [--yes]
# Prerequisite: containers built (bash scripts/start.sh)
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
[ "${1:-}" = "--yes" ] && GERMLINEIQ_YES=1

ensure_env_file
load_env
MODEL="${ORCHESTRATOR_LLM_MODEL:-mistral:v0.3}"
BIOGPT="${PREDICTION_MODEL:-microsoft/biogpt}"
GATK_IMG="${GATK_DOCKER_IMAGE:-broadinstitute/gatk:4.2.6.1}"
PB_IMG="${PARABRICKS_IMAGE:-nvcr.io/nvidia/clara/clara-parabricks:4.6.0-1}"

PULL_PB=0
if command -v nvidia-smi >/dev/null 2>&1; then
  max_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1 || echo 0)
  # 1 GB tolerance: a "16 GB" card exposes 15,360-16,384 MiB
  min_mb=$(awk -v g="${PARABRICKS_MIN_VRAM_GB:-16}" 'BEGIN {printf "%d", (g - 1) * 1024}')
  [ "${max_mb:-0}" -ge "$min_mb" ] && PULL_PB=1
fi

echo "Planned downloads:"
echo "  - Ollama $MODEL (~4 GB)"
echo "  - BioGPT $BIOGPT (~1.6 GB)"
echo "  - $GATK_IMG (~2 GB)"
[ "$PULL_PB" = 1 ] && echo "  - $PB_IMG (several GB — compatible GPU detected)"
confirm "Continue (> 5 GB in total)?" || die "Cancelled."

info "Ollama: $MODEL"
compose up -d ollama
compose exec -T ollama ollama pull "$MODEL"

info "BioGPT: $BIOGPT → $LOCAL_DATA_ROOT/models/huggingface"
compose run --rm --no-deps -T backend python -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
AutoTokenizer.from_pretrained('$BIOGPT')
AutoModelForCausalLM.from_pretrained('$BIOGPT')
print('BioGPT cached')
"

info "GATK image: $GATK_IMG"
docker pull "$GATK_IMG"

if [ "$PULL_PB" = 1 ]; then
  info "Parabricks image: $PB_IMG"
  docker pull "$PB_IMG"
fi

echo "${C_OK}Models ready.${C_RST}"
