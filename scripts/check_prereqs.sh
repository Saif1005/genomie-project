#!/usr/bin/env bash
# Vérifie que le serveur est prêt pour ZAYNB. Lecture seule : ne modifie rien.
# Usage : bash scripts/check_prereqs.sh
# Code retour : 0 si aucun KO, 1 sinon.
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

KO=0
ok()   { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_RST" "$1"; }
ko()   { printf '%s[ KO ]%s %s\n       → %s\n' "$C_KO" "$C_RST" "$1" "$2"; KO=$((KO + 1)); }
wrn()  { printf '%s[WARN]%s %s\n       → %s\n' "$C_WARN" "$C_RST" "$1" "$2"; }
section() { printf '\n%s== %s ==%s\n' "$C_DIM" "$1" "$C_RST"; }

section "Système"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "$ID:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04) ok "OS : $PRETTY_NAME" ;;
    *) wrn "OS : $PRETTY_NAME" "Testé sur Ubuntu 22.04/24.04 ; Parabricks 4.6 exige un Linux x86_64 récent" ;;
  esac
fi
[ "$(uname -m)" = "x86_64" ] && ok "Architecture x86_64" || ko "Architecture $(uname -m)" "Parabricks/GATK nécessitent x86_64"
if grep -qi microsoft /proc/version 2>/dev/null; then
  wrn "Exécution sous WSL2" "Supporté pour les tests ; en production, préférez un Linux natif"
fi

CPUS=$(nproc)
if [ "$CPUS" -ge 16 ]; then ok "CPU : $CPUS cœurs"
elif [ "$CPUS" -ge 8 ]; then wrn "CPU : $CPUS cœurs" "Pipeline CPU lent ; ≥ 16 cœurs recommandés sans GPU"
else wrn "CPU : $CPUS cœurs" "Très lent pour BWA/GATK ; ≥ 16 cœurs recommandés"; fi

RAM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
if [ "$RAM_GB" -ge 64 ]; then ok "RAM : ${RAM_GB} Go"
elif [ "$RAM_GB" -ge 32 ]; then wrn "RAM : ${RAM_GB} Go" "Parabricks recommande ≥ 64 Go ; réduisez PARABRICKS_MEMORY_GB dans Backend/.env"
elif [ "$RAM_GB" -ge 16 ]; then wrn "RAM : ${RAM_GB} Go" "Suffisant pour le mode VCF ; limite pour BWA/GATK sur hg38 (≥ 32 Go conseillés)"
else ko "RAM : ${RAM_GB} Go" "Minimum 16 Go (BWA index hg38 ≈ 6 Go + GATK + BioGPT + Mistral)"; fi

section "Disque ($LOCAL_DATA_ROOT)"
probe="$LOCAL_DATA_ROOT"
while [ ! -d "$probe" ] && [ "$probe" != "/" ]; do probe=$(dirname "$probe"); done
FREE_GB=$(df -BG --output=avail "$probe" | tail -1 | tr -dc '0-9')
if [ ! -d "$LOCAL_DATA_ROOT" ]; then
  wrn "$LOCAL_DATA_ROOT absent" "sudo mkdir -p $LOCAL_DATA_ROOT && sudo chown -R \$USER: $LOCAL_DATA_ROOT (ou lancez scripts/start.sh)"
elif [ ! -w "$LOCAL_DATA_ROOT" ]; then
  ko "$LOCAL_DATA_ROOT non accessible en écriture" "sudo chown -R \$USER: $LOCAL_DATA_ROOT"
else
  ok "$LOCAL_DATA_ROOT accessible en écriture"
fi
if [ "${FREE_GB:-0}" -ge 500 ]; then ok "Espace libre : ${FREE_GB} Go"
elif [ "${FREE_GB:-0}" -ge 200 ]; then wrn "Espace libre : ${FREE_GB} Go" "OK pour quelques patients ; un génome complet produit ~100-200 Go de BAM"
else ko "Espace libre : ${FREE_GB} Go" "≥ 200 Go requis (référence ~12 Go, images ~25 Go, BAM volumineux)"; fi

section "GPU NVIDIA"
HAS_GPU=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  HAS_GPU=1
  DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  DRIVER_MAJOR=${DRIVER%%.*}
  if [ "$DRIVER_MAJOR" -ge 525 ]; then ok "Driver NVIDIA $DRIVER"
  else ko "Driver NVIDIA $DRIVER" "Parabricks 4.6 exige un driver ≥ 525 : sudo ubuntu-drivers install"; fi
  MIN_VRAM_GB="${PARABRICKS_MIN_VRAM_GB:-16}"
  while IFS=, read -r name vram; do
    vram_gb=$(( ${vram// /} / 1024 ))
    if [ "$vram_gb" -ge "$(( ${MIN_VRAM_GB%.*} - 1 ))" ]; then
      ok "GPU :${name} — ${vram_gb} Go VRAM → Parabricks activable"
    else
      wrn "GPU :${name} — ${vram_gb} Go VRAM" "< ${MIN_VRAM_GB} Go : Parabricks impossible, bascule auto sur GATK4 CPU (plusieurs heures/échantillon). BioGPT utilisera le GPU."
    fi
  done < <(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits)
else
  wrn "Aucun GPU NVIDIA détecté" "Tout tournera sur CPU (GATK4 : plusieurs heures par échantillon). Installez le driver si le serveur a une carte NVIDIA."
fi

section "Docker"
# `docker --version` plutôt que `command -v` : sous WSL, un stub « docker » existe sans Docker
if DOCKER_VERSION=$(docker --version 2>/dev/null); then
  ok "$DOCKER_VERSION"
  if docker info >/dev/null 2>&1; then
    ok "Démon Docker accessible"
  else
    ko "Démon Docker inaccessible" "sudo systemctl start docker && sudo usermod -aG docker \$USER (puis reconnexion)"
  fi
  if docker compose version >/dev/null 2>&1; then ok "$(docker compose version)"
  else ko "Docker Compose v2 absent" "sudo apt install docker-compose-plugin"; fi
  if [ "$HAS_GPU" = 1 ]; then
    if docker info 2>/dev/null | grep -qi nvidia; then ok "NVIDIA Container Toolkit (runtime nvidia)"
    else ko "NVIDIA Container Toolkit absent" "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html puis sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"; fi
  fi
else
  ko "Docker absent" "https://docs.docker.com/engine/install/ubuntu/"
fi
command -v curl >/dev/null 2>&1 && ok "curl présent" || ko "curl absent" "sudo apt install curl"
command -v python3 >/dev/null 2>&1 && ok "python3 présent (smoke test)" || wrn "python3 absent" "sudo apt install python3 (requis par scripts/smoke_test.sh)"

section "Configuration"
if [ -f "$ENV_FILE" ]; then
  ok "Backend/.env présent"
  perms=$(stat -c %a "$ENV_FILE")
  [ "$perms" = "600" ] || wrn "Backend/.env en $perms" "chmod 600 Backend/.env"
  [ "${DEPLOYMENT_MODE:-local}" = "local" ] && ok "DEPLOYMENT_MODE=local" || wrn "DEPLOYMENT_MODE=${DEPLOYMENT_MODE}" "Mettre DEPLOYMENT_MODE=local pour le serveur on-premise"
else
  wrn "Backend/.env absent" "Créé automatiquement par scripts/start.sh depuis Backend/.env.local.example"
fi
case "$BIND_ADDRESS" in
  127.0.0.1) ok "Ports liés à 127.0.0.1 (accès depuis le serveur uniquement)" ;;
  0.0.0.0) wrn "BIND_ADDRESS=0.0.0.0" "Expose sur toutes les interfaces : préférez l'IP LAN précise + pare-feu (ufw)" ;;
  *) wrn "BIND_ADDRESS=$BIND_ADDRESS" "Accessible depuis le réseau local : vérifiez le pare-feu, jamais d'accès Internet" ;;
esac

section "Référence génomique ($LOCAL_DATA_ROOT/reference/hg38)"
REF="$LOCAL_DATA_ROOT/reference/hg38"
check_file() {
  if [ -s "$REF/$1" ]; then ok "$1"; else ko "$1 manquant" "$2"; fi
}
check_file hg38.fa "bash scripts/download_reference.sh"
check_file hg38.fa.fai "bash scripts/download_reference.sh (ou samtools faidx hg38.fa)"
check_file hg38.dict "bash scripts/download_reference.sh (ou gatk CreateSequenceDictionary)"
for ext in amb ann bwt pac sa; do check_file "hg38.fa.$ext" "bash scripts/download_reference.sh (index BWA)"; done
check_file Homo_sapiens_assembly38.known_indels.vcf.gz "bash scripts/download_reference.sh (known-sites BQSR)"
check_file Homo_sapiens_assembly38.known_indels.vcf.gz.tbi "bash scripts/download_reference.sh"
check_file Mills_and_1000G_gold_standard.indels.hg38.vcf.gz "bash scripts/download_reference.sh"
echo "       ${C_DIM}(la référence n'est requise que pour l'analyse FASTQ ; le mode VCF direct fonctionne sans)${C_RST}"

section "Modèles"
MODEL="${ORCHESTRATOR_LLM_MODEL:-mistral:v0.3}"
MANIFEST="$LOCAL_DATA_ROOT/models/ollama/models/manifests/registry.ollama.ai/library/${MODEL%%:*}/${MODEL#*:}"
[ -f "$MANIFEST" ] && ok "Ollama : $MODEL" || ko "Ollama : $MODEL absent" "bash scripts/pull_models.sh"
HF_DIR="$LOCAL_DATA_ROOT/models/huggingface/hub/models--$(echo "${PREDICTION_MODEL:-microsoft/biogpt}" | sed 's#/#--#g')"
[ -d "$HF_DIR" ] && ok "BioGPT : ${PREDICTION_MODEL:-microsoft/biogpt}" || ko "BioGPT absent du cache" "bash scripts/pull_models.sh"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  IMG="${GATK_DOCKER_IMAGE:-broadinstitute/gatk:4.2.6.1}"
  docker image inspect "$IMG" >/dev/null 2>&1 && ok "Image $IMG" || wrn "Image $IMG absente" "bash scripts/pull_models.sh (téléchargée aussi au 1er job FASTQ)"
  PB="${PARABRICKS_IMAGE:-nvcr.io/nvidia/clara/clara-parabricks:4.6.0-1}"
  if [ "$HAS_GPU" = 1 ]; then
    docker image inspect "$PB" >/dev/null 2>&1 && ok "Image Parabricks" || wrn "Image Parabricks absente" "bash scripts/pull_models.sh (si GPU ≥ 16 Go)"
  fi
fi

echo
if [ "$KO" -eq 0 ]; then
  echo "${C_OK}Aucun point bloquant.${C_RST}"
  exit 0
fi
echo "${C_KO}$KO point(s) bloquant(s) — voir les correctifs ci-dessus.${C_RST}"
exit 1
