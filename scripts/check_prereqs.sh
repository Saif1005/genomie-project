#!/usr/bin/env bash
# Checks that the server is ready for GermlineIQ. Read-only: changes nothing.
# Usage: bash scripts/check_prereqs.sh
# Exit code: 0 if no KO, 1 otherwise.
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

KO=0
ok()   { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_RST" "$1"; }
ko()   { printf '%s[ KO ]%s %s\n       → %s\n' "$C_KO" "$C_RST" "$1" "$2"; KO=$((KO + 1)); }
wrn()  { printf '%s[WARN]%s %s\n       → %s\n' "$C_WARN" "$C_RST" "$1" "$2"; }
section() { printf '\n%s== %s ==%s\n' "$C_DIM" "$1" "$C_RST"; }

section "System"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "$ID:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04) ok "OS: $PRETTY_NAME" ;;
    *) wrn "OS: $PRETTY_NAME" "Tested on Ubuntu 22.04/24.04; Parabricks 4.6 requires a recent x86_64 Linux" ;;
  esac
fi
[ "$(uname -m)" = "x86_64" ] && ok "Architecture x86_64" || ko "Architecture $(uname -m)" "Parabricks/GATK require x86_64"
if grep -qi microsoft /proc/version 2>/dev/null; then
  wrn "Running under WSL2" "Supported for testing; prefer native Linux in production"
fi

CPUS=$(nproc)
if [ "$CPUS" -ge 16 ]; then ok "CPU: $CPUS cores"
elif [ "$CPUS" -ge 8 ]; then wrn "CPU: $CPUS cores" "Slow CPU pipeline; ≥ 16 cores recommended without a GPU"
else wrn "CPU: $CPUS cores" "Very slow for BWA/GATK; ≥ 16 cores recommended"; fi

RAM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
if [ "$RAM_GB" -ge 64 ]; then ok "RAM: ${RAM_GB} GB"
elif [ "$RAM_GB" -ge 32 ]; then wrn "RAM: ${RAM_GB} GB" "Parabricks recommends ≥ 64 GB; reduce PARABRICKS_MEMORY_GB in Backend/.env"
elif [ "$RAM_GB" -ge 16 ]; then wrn "RAM: ${RAM_GB} GB" "Enough for VCF mode; borderline for BWA/GATK on hg38 (≥ 32 GB advised)"
else ko "RAM: ${RAM_GB} GB" "Minimum 16 GB (hg38 BWA index ≈ 6 GB + GATK + BioGPT + Mistral)"; fi

section "Disk ($LOCAL_DATA_ROOT)"
probe="$LOCAL_DATA_ROOT"
while [ ! -d "$probe" ] && [ "$probe" != "/" ]; do probe=$(dirname "$probe"); done
FREE_GB=$(df -BG --output=avail "$probe" | tail -1 | tr -dc '0-9')
if [ ! -d "$LOCAL_DATA_ROOT" ]; then
  wrn "$LOCAL_DATA_ROOT missing" "sudo mkdir -p $LOCAL_DATA_ROOT && sudo chown -R \$USER: $LOCAL_DATA_ROOT (or run scripts/start.sh)"
elif [ ! -w "$LOCAL_DATA_ROOT" ]; then
  ko "$LOCAL_DATA_ROOT not writable" "sudo chown -R \$USER: $LOCAL_DATA_ROOT"
else
  ok "$LOCAL_DATA_ROOT writable"
fi
if [ "${FREE_GB:-0}" -ge 500 ]; then ok "Free space: ${FREE_GB} GB"
elif [ "${FREE_GB:-0}" -ge 200 ]; then wrn "Free space: ${FREE_GB} GB" "OK for a few patients; a whole genome produces ~100-200 GB of BAM"
else ko "Free space: ${FREE_GB} GB" "≥ 200 GB required (reference ~12 GB, images ~25 GB, large BAM files)"; fi

section "NVIDIA GPU"
HAS_GPU=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  HAS_GPU=1
  DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  DRIVER_MAJOR=${DRIVER%%.*}
  if [ "$DRIVER_MAJOR" -ge 525 ]; then ok "NVIDIA driver $DRIVER"
  else ko "NVIDIA driver $DRIVER" "Parabricks 4.6 requires driver ≥ 525: sudo ubuntu-drivers install"; fi
  MIN_VRAM_GB="${PARABRICKS_MIN_VRAM_GB:-16}"
  while IFS=, read -r name vram; do
    vram_gb=$(( ${vram// /} / 1024 ))
    if [ "$vram_gb" -ge "$(( ${MIN_VRAM_GB%.*} - 1 ))" ]; then
      ok "GPU:${name} — ${vram_gb} GB VRAM → Parabricks available"
    else
      wrn "GPU:${name} — ${vram_gb} GB VRAM" "< ${MIN_VRAM_GB} GB: Parabricks not possible, automatic fallback to GATK4 CPU (several hours/sample). BioGPT will use the GPU."
    fi
  done < <(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits)
else
  wrn "No NVIDIA GPU detected" "Everything will run on CPU (GATK4: several hours per sample). Install the driver if the server has an NVIDIA card."
fi

section "Docker"
# `docker --version` rather than `command -v`: under WSL a "docker" stub exists without Docker
if DOCKER_VERSION=$(docker --version 2>/dev/null); then
  ok "$DOCKER_VERSION"
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon reachable"
  else
    ko "Docker daemon unreachable" "sudo systemctl start docker && sudo usermod -aG docker \$USER (then log in again)"
  fi
  if docker compose version >/dev/null 2>&1; then ok "$(docker compose version)"
  else ko "Docker Compose v2 missing" "sudo apt install docker-compose-plugin"; fi
  if [ "$HAS_GPU" = 1 ]; then
    if docker info 2>/dev/null | grep -qi nvidia; then ok "NVIDIA Container Toolkit (nvidia runtime)"
    else ko "NVIDIA Container Toolkit missing" "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html then sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"; fi
  fi
else
  ko "Docker missing" "https://docs.docker.com/engine/install/ubuntu/"
fi
command -v curl >/dev/null 2>&1 && ok "curl present" || ko "curl missing" "sudo apt install curl"
command -v python3 >/dev/null 2>&1 && ok "python3 present (smoke test)" || wrn "python3 missing" "sudo apt install python3 (required by scripts/smoke_test.sh)"

section "Configuration"
if [ -f "$ENV_FILE" ]; then
  ok "Backend/.env present"
  perms=$(stat -c %a "$ENV_FILE")
  [ "$perms" = "600" ] || wrn "Backend/.env has mode $perms" "chmod 600 Backend/.env"
else
  wrn "Backend/.env missing" "Created automatically by scripts/start.sh from Backend/.env.local.example"
fi
case "$BIND_ADDRESS" in
  127.0.0.1) ok "Ports bound to 127.0.0.1 (access from the server only)" ;;
  0.0.0.0) wrn "BIND_ADDRESS=0.0.0.0" "Exposed on every interface: prefer the exact LAN IP + firewall (ufw)" ;;
  *) wrn "BIND_ADDRESS=$BIND_ADDRESS" "Reachable from the local network: check the firewall, never expose to the Internet" ;;
esac

section "ClinVar ($LOCAL_DATA_ROOT/reference/clinvar)"
CLINVAR="${CLINVAR_VCF:-$LOCAL_DATA_ROOT/reference/clinvar/clinvar_GRCh38.vcf.gz}"
if [ -s "$CLINVAR" ]; then
  ok "ClinVar: $(zcat "$CLINVAR" 2>/dev/null | head -50 | grep -m1 '^##fileDate' | cut -d= -f2)"
else
  ko "ClinVar missing ($CLINVAR)" "bash scripts/download_reference.sh --clinvar-only (~0.2 GB) — required for unannotated VCFs and FASTQ mode"
fi

section "Genome reference ($LOCAL_DATA_ROOT/reference/hg38)"
REF="$LOCAL_DATA_ROOT/reference/hg38"
check_file() {
  if [ -s "$REF/$1" ]; then ok "$1"; else ko "$1 missing" "$2"; fi
}
check_file hg38.fa "bash scripts/download_reference.sh"
check_file hg38.fa.fai "bash scripts/download_reference.sh (or samtools faidx hg38.fa)"
check_file hg38.dict "bash scripts/download_reference.sh (or gatk CreateSequenceDictionary)"
for ext in amb ann bwt pac sa; do check_file "hg38.fa.$ext" "bash scripts/download_reference.sh (BWA index)"; done
check_file Homo_sapiens_assembly38.known_indels.vcf.gz "bash scripts/download_reference.sh (BQSR known sites)"
check_file Homo_sapiens_assembly38.known_indels.vcf.gz.tbi "bash scripts/download_reference.sh"
check_file Mills_and_1000G_gold_standard.indels.hg38.vcf.gz "bash scripts/download_reference.sh"
echo "       ${C_DIM}(the reference is only required for FASTQ analysis; direct VCF mode works without it)${C_RST}"

section "Models"
MODEL="${ORCHESTRATOR_LLM_MODEL:-mistral:v0.3}"
MANIFEST="$LOCAL_DATA_ROOT/models/ollama/models/manifests/registry.ollama.ai/library/${MODEL%%:*}/${MODEL#*:}"
[ -f "$MANIFEST" ] && ok "Ollama: $MODEL" || ko "Ollama: $MODEL missing" "bash scripts/pull_models.sh"
HF_DIR="$LOCAL_DATA_ROOT/models/huggingface/hub/models--$(echo "${PREDICTION_MODEL:-microsoft/biogpt}" | sed 's#/#--#g')"
[ -d "$HF_DIR" ] && ok "BioGPT: ${PREDICTION_MODEL:-microsoft/biogpt}" || ko "BioGPT missing from the cache" "bash scripts/pull_models.sh"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  IMG="${GATK_DOCKER_IMAGE:-broadinstitute/gatk:4.2.6.1}"
  docker image inspect "$IMG" >/dev/null 2>&1 && ok "Image $IMG" || wrn "Image $IMG missing" "bash scripts/pull_models.sh (also pulled on the first FASTQ job)"
  PB="${PARABRICKS_IMAGE:-nvcr.io/nvidia/clara/clara-parabricks:4.6.0-1}"
  if [ "$HAS_GPU" = 1 ]; then
    docker image inspect "$PB" >/dev/null 2>&1 && ok "Parabricks image" || wrn "Parabricks image missing" "bash scripts/pull_models.sh (if GPU ≥ 16 GB)"
  fi
fi

echo
if [ "$KO" -eq 0 ]; then
  echo "${C_OK}No blocking issue.${C_RST}"
  exit 0
fi
echo "${C_KO}$KO blocking issue(s) — see the fixes above.${C_RST}"
exit 1
