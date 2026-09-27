#!/usr/bin/env bash
# Downloads the public reference data into $LOCAL_DATA_ROOT/reference:
#   hg38/     GATK Resource Bundle (Broad Institute): FASTA, indexes, BQSR known sites
#   clinvar/  ClinVar GRCh38 (NCBI, public domain): classification of pathogenic variants
#
# Usage: bash scripts/download_reference.sh [--yes] [--clinvar-only] [--build-bwa-index] [--with-dbsnp]
#   --yes              no interactive confirmation
#   --clinvar-only     ClinVar only (~0.2 GB): enough to analyse VCFs
#   --build-bwa-index  builds the BWA index locally (≈ 1-2 h, ~6 GB RAM) instead of downloading it
#   --with-dbsnp       adds dbSNP 138 (~11 GB) to the BQSR known sites
#
# Expected disk space: ~9.7 GB (FASTA 3.2 GB + BWA index 5.5 GB + known sites ~0.1 GB + ClinVar ~0.2 GB)
#                      ~21 GB with --with-dbsnp. Allow 2× during the download.
# Update ClinVar (weekly release): rerun with --clinvar-only --refresh-clinvar.
# Resume: rerun the script; partial files (.part) are completed (curl -C -).
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

BUILD_BWA=0; WITH_DBSNP=0; CLINVAR_ONLY=0; REFRESH_CLINVAR=0
for arg in "$@"; do
  case "$arg" in
    --yes) GERMLINEIQ_YES=1 ;;
    --build-bwa-index) BUILD_BWA=1 ;;
    --with-dbsnp) WITH_DBSNP=1 ;;
    --clinvar-only) CLINVAR_ONLY=1 ;;
    --refresh-clinvar) REFRESH_CLINVAR=1 ;;
    *) die "Option inconnue : $arg" ;;
  esac
done

command -v curl >/dev/null 2>&1 || die "curl required: sudo apt install curl"

# --- ClinVar (NCBI) ------------------------------------------------------------
CLINVAR_URL="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz"
CLINVAR_DIR="$LOCAL_DATA_ROOT/reference/clinvar"
CLINVAR="$CLINVAR_DIR/clinvar_GRCh38.vcf.gz"
download_clinvar() {
  mkdir -p "$CLINVAR_DIR"
  if [ -s "$CLINVAR" ] && [ "$REFRESH_CLINVAR" = 0 ]; then
    info "ClinVar already present: $CLINVAR ($(zcat "$CLINVAR" | head -50 | grep -m1 '^##fileDate' || echo 'unknown date'))"
    return
  fi
  info "↓ ClinVar GRCh38 ($CLINVAR_URL)"
  curl -fL --retry 5 --retry-delay 10 -o "$CLINVAR.part" "$CLINVAR_URL"
  expected=$(curl -fsL "$CLINVAR_URL.md5" | awk '{print $1}')
  if [ -n "$expected" ]; then
    [ "$(md5sum "$CLINVAR.part" | awk '{print $1}')" = "$expected" ] \
      || { rm -f "$CLINVAR.part"; die "Invalid ClinVar MD5 — rerun"; }
    info "  MD5 verified"
  fi
  gzip -t "$CLINVAR.part" || { rm -f "$CLINVAR.part"; die "ClinVar corrupted — rerun"; }
  mv "$CLINVAR.part" "$CLINVAR"
  # The panel index (JSON cache) is rebuilt automatically at the next job
  rm -f "$CLINVAR".panel-*.json
  info "ClinVar ready: $(zcat "$CLINVAR" | head -50 | grep -m1 '^##fileDate')"
}

download_clinvar
if [ "$CLINVAR_ONLY" = 1 ]; then
  echo "${C_OK}ClinVar ready in $CLINVAR_DIR${C_RST}"
  exit 0
fi

BASE_URL="https://storage.googleapis.com/gcp-public-data--broad-references/hg38/v0"
REF_DIR="$LOCAL_DATA_ROOT/reference/hg38"
mkdir -p "$REF_DIR"

# remote file → local name
FILES=(
  "Homo_sapiens_assembly38.fasta hg38.fa"
  "Homo_sapiens_assembly38.fasta.fai hg38.fa.fai"
  "Homo_sapiens_assembly38.dict hg38.dict"
  "Homo_sapiens_assembly38.known_indels.vcf.gz Homo_sapiens_assembly38.known_indels.vcf.gz"
  "Homo_sapiens_assembly38.known_indels.vcf.gz.tbi Homo_sapiens_assembly38.known_indels.vcf.gz.tbi"
  "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz Mills_and_1000G_gold_standard.indels.hg38.vcf.gz"
  "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi"
)
if [ "$BUILD_BWA" = 0 ]; then
  # Bundle index (bwa index -6 → .64 suffix): same format, renamed to hg38.fa.*
  # (the .alt file is not installed: non ALT-aware alignment, like a standard bwa index)
  for ext in amb ann bwt pac sa; do
    FILES+=("Homo_sapiens_assembly38.fasta.64.$ext hg38.fa.$ext")
  done
fi
if [ "$WITH_DBSNP" = 1 ]; then
  FILES+=("Homo_sapiens_assembly38.dbsnp138.vcf Homo_sapiens_assembly38.dbsnp138.vcf")
  FILES+=("Homo_sapiens_assembly38.dbsnp138.vcf.idx Homo_sapiens_assembly38.dbsnp138.vcf.idx")
fi

remote_size() { curl -sIL "$BASE_URL/$1" | awk 'tolower($1)=="content-length:" {v=$2} END {gsub("\r","",v); print v}'; }
remote_md5()  { curl -sIL "$BASE_URL/$1" | tr -d '\r' | awk -F'md5=' 'tolower($0) ~ /^x-goog-hash:.*md5=/ {print $2}' | tail -1; }
local_md5()   { openssl dgst -md5 -binary "$1" | base64; }

info "Computing the download size..."
TOTAL=0
TODO=()
for entry in "${FILES[@]}"; do
  read -r remote local <<<"$entry"
  size=$(remote_size "$remote")
  [ -n "$size" ] || die "Remote file unreachable: $BASE_URL/$remote (Internet access?)"
  if [ -s "$REF_DIR/$local" ] && [ "$(stat -c %s "$REF_DIR/$local")" = "$size" ]; then
    continue
  fi
  TODO+=("$remote $local $size")
  TOTAL=$((TOTAL + size))
done

if [ "${#TODO[@]}" -eq 0 ]; then
  info "All files are already present."
else
  TOTAL_GB=$(awk -v b="$TOTAL" 'BEGIN {printf "%.1f", b/1024/1024/1024}')
  FREE_GB=$(df -BG --output=avail "$REF_DIR" | tail -1 | tr -dc '0-9')
  info "${#TODO[@]} file(s) to download: ${TOTAL_GB} GB (free: ${FREE_GB} GB in $REF_DIR)"
  confirm "Start downloading ${TOTAL_GB} GB?" || die "Cancelled."

  for entry in "${TODO[@]}"; do
    read -r remote local size <<<"$entry"
    dest="$REF_DIR/$local"
    info "↓ $remote → $local"
    curl -fL --retry 5 --retry-delay 10 -C - -o "$dest.part" "$BASE_URL/$remote"
    got=$(stat -c %s "$dest.part")
    [ "$got" = "$size" ] || die "Wrong size for $local ($got ≠ $size) — rerun the script to resume"
    md5=$(remote_md5 "$remote")
    if [ -n "$md5" ] && command -v openssl >/dev/null 2>&1; then
      [ "$(local_md5 "$dest.part")" = "$md5" ] || { rm -f "$dest.part"; die "Invalid MD5 for $local — file deleted, rerun"; }
      info "  MD5 verified"
    fi
    mv "$dest.part" "$dest"
  done
fi

# --- Missing indexes ----------------------------------------------------------
FA="$REF_DIR/hg38.fa"
if [ ! -s "$FA.fai" ]; then
  command -v samtools >/dev/null 2>&1 || die "samtools required for the .fai index: sudo apt install samtools"
  info "samtools faidx"; samtools faidx "$FA"
fi
if [ ! -s "$REF_DIR/hg38.dict" ]; then
  info "gatk CreateSequenceDictionary (through Docker)"
  docker run --rm -v "$REF_DIR:$REF_DIR" "${GATK_DOCKER_IMAGE:-broadinstitute/gatk:4.2.6.1}" \
    gatk CreateSequenceDictionary -R "$FA" -O "$REF_DIR/hg38.dict"
fi
if [ ! -s "$FA.bwt" ]; then
  command -v bwa >/dev/null 2>&1 || die "bwa required to build the index: sudo apt install bwa"
  info "bwa index (≈ 1-2 h)..."; bwa index "$FA"
fi
for vcf in Homo_sapiens_assembly38.known_indels.vcf.gz Mills_and_1000G_gold_standard.indels.hg38.vcf.gz; do
  if [ -s "$REF_DIR/$vcf" ] && [ ! -s "$REF_DIR/$vcf.tbi" ]; then
    command -v tabix >/dev/null 2>&1 || die "tabix required: sudo apt install tabix"
    tabix -p vcf "$REF_DIR/$vcf"
  fi
done

# --- Final check -------------------------------------------------------------
info "Checking..."
head -c 1 "$FA" | grep -q '>' || die "hg38.fa does not look like a FASTA file"
[ "$(wc -l < "$FA.fai")" -ge 25 ] || die "hg38.fa.fai incomplete"
grep -q '^@SQ' "$REF_DIR/hg38.dict" || die "hg38.dict invalid"
for ext in amb ann bwt pac sa; do [ -s "$FA.$ext" ] || die "BWA index missing: hg38.fa.$ext"; done
gzip -t "$REF_DIR/Homo_sapiens_assembly38.known_indels.vcf.gz" || die "known_indels corrupted"

du -sh "$REF_DIR"
echo "${C_OK}hg38 reference ready in $REF_DIR${C_RST}"
